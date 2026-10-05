"""MCP server definition and transport wiring (targets the mcp 2.x SDK).

The tools here are thin adapters: they translate an MCP tool call into a call
into :mod:`executor`, which enforces the policy. Policy errors are returned to
the client as readable messages rather than raised, so the assistant can see
why a request was refused and adjust.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import __version__, executor
from .allowlist import DynamicAllowlist
from .approvals import (
    Approver,
    AutoDenyApprover,
    FileApprovalQueue,
    TTYApprover,
)
from .config import ServerConfig
from .policy import PolicyError


def build_store(config: ServerConfig) -> DynamicAllowlist | None:
    """The persistent allowlist that 'always allow' appends to, if configured."""
    if config.allowlist_file:
        return DynamicAllowlist(config.allowlist_file)
    return None


def build_approver(config: ServerConfig) -> Approver:
    """Build the approver for commands not on the allowlist."""
    if config.approval_mode == "tty":
        return TTYApprover()
    if config.approval_mode == "file":
        assert config.approvals_dir is not None  # ensured by config.validate()
        return FileApprovalQueue(config.approvals_dir, config.approval_timeout)
    return AutoDenyApprover()


def build_server(config: ServerConfig) -> MCPServer:
    """Construct a configured :class:`MCPServer` with tools registered."""
    policy = config.policy
    mcp = MCPServer("local-terminal-mcp", version=__version__)

    store = build_store(config)
    approver = build_approver(config) if config.approval_mode != "none" else None

    allowed = ", ".join(sorted(policy.allowed_commands))

    @mcp.tool(
        description=(
            "Run a single allowlisted command inside the project root and "
            "return its output. Exactly one program per call: pipes, chaining "
            "(&&, ;), redirects (>) and command substitution are not allowed. "
            f"Allowed programs: {allowed}. Optionally set 'cwd' to a "
            "subdirectory of the root."
        )
    )
    def run_command(command: str, cwd: str = "") -> str:
        try:
            return executor.run_command(
                policy, command, cwd, store=store, approver=approver
            )
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(description="Read a UTF-8 text file located inside the project root.")
    def read_file(path: str) -> str:
        try:
            return executor.read_file(policy, path)
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(
        description="List the entries of a directory inside the project root."
    )
    def list_directory(path: str = ".") -> str:
        try:
            return executor.list_directory(policy, path)
        except PolicyError as exc:
            return f"refused: {exc}"

    if policy.allow_write:

        @mcp.tool(
            description=(
                "Write a UTF-8 text file inside the project root. Creates parent "
                "directories as needed. Only available when the server is started "
                "with write access enabled."
            )
        )
        def write_file(path: str, content: str) -> str:
            try:
                return executor.write_file(policy, path, content)
            except PolicyError as exc:
                return f"refused: {exc}"

    return mcp


def _transport_security(config: ServerConfig):
    """Build DNS-rebinding settings that work with tunnels.

    DNS-rebinding protection validates the incoming ``Host`` header. Tunnels
    (ngrok, cloudflared) forward an arbitrary public hostname, which would be
    rejected by the default localhost-only allowlist. Since every HTTP request
    must already carry a valid bearer token, we default to accepting any host
    and let the user tighten it with ``--allowed-hosts`` for defense in depth.
    """
    from mcp.server.transport_security import TransportSecuritySettings

    if config.allowed_hosts:
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[*config.allowed_hosts, "127.0.0.1", "localhost"],
            allowed_origins=["*"],
        )
    return TransportSecuritySettings(enable_dns_rebinding_protection=False)


def run(config: ServerConfig) -> None:
    """Start the server using the configured transport."""
    config.validate()
    mcp = build_server(config)

    if config.transport == "stdio":
        mcp.run(transport="stdio")
        return

    # HTTP transport: serve the Streamable-HTTP ASGI app with a health route
    # and the configured authentication, then serve with uvicorn.
    import uvicorn

    from .auth import HEALTH_PATH, BearerAuthMiddleware, health_endpoint

    # Register an unauthenticated health route on the MCP app.
    mcp.custom_route(HEALTH_PATH, methods=["GET"])(health_endpoint)

    app = mcp.streamable_http_app(
        host=config.host,
        streamable_http_path=config.mcp_path,
        transport_security=_transport_security(config),
    )

    if config.auth_mode == "bearer":
        # Guaranteed by config.validate().
        assert config.auth_token is not None
        app.add_middleware(BearerAuthMiddleware, token=config.auth_token)
    # In "path" mode the unguessable mcp_path is the credential: the MCP route
    # only exists at that path, so unauthenticated probes of other paths 404.

    uvicorn.run(app, host=config.host, port=config.port, log_level="info")
