"""MCP server definition and transport wiring (targets the mcp 2.x SDK).

The tools here are thin adapters: they translate an MCP tool call into a call
into :mod:`executor`, which enforces the policy. Policy errors are returned to
the client as readable messages rather than raised, so the assistant can see
why a request was refused and adjust.
"""

from __future__ import annotations

import os
from pathlib import Path

from mcp.server.mcpserver import Image, MCPServer

from . import __version__, executor
from .allowlist import DynamicAllowlist
from .approvals import (
    Approver,
    AutoDenyApprover,
    FileApprovalQueue,
    TTYApprover,
)
from .config import ServerConfig
from .policy import Policy, PolicyError
from .sessions import SessionManager


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
    instructions = None
    if config.inbox_dir and policy.allow_write:
        instructions = (
            "For NEW art (sprites, icons, illustrations, textures): generate it "
            "with your own native image-generation tool, not by hand-writing or "
            "deriving SVG/pixel data in code. A rendered image is auto-saved "
            "into this project's import inbox; call list_inbox to see it, then "
            "import_recent_images or import_file to bring it into the repo. "
            "Only fall back to writing image data yourself if image generation "
            "is unavailable."
        )
    mcp = MCPServer(
        "local-terminal-mcp", version=__version__, instructions=instructions
    )

    store = build_store(config)
    approver = build_approver(config) if config.approval_mode != "none" else None
    sessions = SessionManager(policy.root)

    allowed = ", ".join(sorted(str(p) for p in policy.allowed_commands))

    def _rel(path: Path) -> str:
        try:
            rel = path.relative_to(policy.root)
        except ValueError:
            return str(path)
        return str(rel) if str(rel) != "." else "."

    def _run(policy: Policy, command: str, base: Path) -> str:
        return executor.run_command(
            policy, command, cwd=str(base), store=store, approver=approver
        )

    @mcp.tool(
        description=(
            "Run a single allowlisted command and return its output. Exactly "
            "one program per call: pipes, chaining (&&, ;), redirects (>) and "
            "command substitution are not allowed. Use 'cd <dir>' to change the "
            "session's working directory (it persists across calls, so you need "
            "not repeat it); 'pwd' shows it. Pass 'session' to target a specific "
            "terminal opened with open_terminal (default: a shared session). "
            f"Allowed: {allowed}."
        )
    )
    def run_command(command: str, session: str = "", cwd: str = "") -> str:
        try:
            sess = sessions.get(session or None)
        except KeyError:
            return f"refused: unknown session {session!r} (open one first)"
        try:
            argv = policy.parse_structure(command)
        except PolicyError as exc:
            return f"refused: {exc}"

        program = os.path.basename(argv[0])
        if program == "cd":
            target = argv[1] if len(argv) > 1 else ""
            try:
                new_cwd = policy.resolve_within(sess.cwd, target)
            except PolicyError as exc:
                return f"refused: {exc}"
            if not new_cwd.is_dir():
                return f"refused: {target!r} is not a directory"
            sessions.set_cwd(sess.id, new_cwd)
            return f"cwd: {_rel(new_cwd)}"
        if program == "pwd":
            return _rel(sess.cwd)

        base = sess.cwd
        if cwd:
            try:
                base = policy.resolve_within(sess.cwd, cwd)
            except PolicyError as exc:
                return f"refused: {exc}"
        try:
            return _run(policy, command, base)
        except PolicyError as exc:
            return f"refused: {exc}"

    @mcp.tool(
        description=(
            "Open a new terminal session with its own persistent working "
            "directory (starts at the project root). Returns the session id to "
            "pass to run_command."
        )
    )
    def open_terminal(name: str = "") -> str:
        try:
            sess = sessions.open(name)
        except RuntimeError as exc:
            return f"refused: {exc}"
        return f"opened session '{sess.id}' (name: {sess.name}, cwd: {_rel(sess.cwd)})"

    @mcp.tool(description="List open terminal sessions and their directories.")
    def list_terminals() -> str:
        lines = [
            f"{s.id}  name={s.name}  cwd={_rel(s.cwd)}" for s in sessions.list()
        ]
        return "\n".join(lines) if lines else "(no sessions yet)"

    @mcp.tool(description="Close a terminal session by id.")
    def close_terminal(session: str) -> str:
        return (
            f"closed {session}"
            if sessions.close(session)
            else f"no such session {session!r}"
        )

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

    @mcp.tool(
        description=(
            "Read an image file (png/jpg/gif/webp/bmp) inside the project root "
            "and return it as an image so you can actually see it. Large images "
            "are auto-downscaled to fit; the file on disk is left unchanged."
        ),
        structured_output=False,
    )
    def read_image(path: str):
        try:
            data, fmt = executor.read_image_payload(
                policy, path, executor.IMAGE_READ_MAX_BYTES
            )
        except PolicyError as exc:
            return f"refused: {exc}"
        # Large images are returned downscaled so the model can still see them;
        # the file on disk is unchanged.
        return Image(data=data, format=fmt)

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

        @mcp.tool(
            description=(
                "Write a BINARY file (e.g. a PNG) inside the project root from "
                "base64 content (a plain base64 string or a data: URL). Use this "
                "to save generated images and other non-text assets."
            )
        )
        def write_file_base64(path: str, data_base64: str) -> str:
            try:
                return executor.write_file_base64(policy, path, data_base64)
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Move or rename a file/directory within the project root "
                "(both source and destination are confined to the root)."
            )
        )
        def move_file(src: str, dest: str) -> str:
            try:
                return executor.move_file(policy, src, dest)
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Copy a file or directory within the project root (both source "
                "and destination are confined to the root)."
            )
        )
        def copy_file(src: str, dest: str) -> str:
            try:
                return executor.copy_file(policy, src, dest)
            except PolicyError as exc:
                return f"refused: {exc}"

    if config.inbox_dir and policy.allow_write:
        inbox = Path(config.inbox_dir).expanduser()
        inbox.mkdir(parents=True, exist_ok=True)
        inbox_ttl = config.inbox_ttl_days

        def _clean() -> None:
            executor.cleanup_inbox(inbox, inbox_ttl)

        @mcp.tool(
            description=(
                "List recent image files in the import inbox (a staging folder), "
                "newest first. Use import_recent_images or import_file to bring "
                "them into the repo. Old files in the inbox are auto-cleaned."
            )
        )
        def list_inbox(images_only: bool = True, limit: int = 20) -> str:
            try:
                _clean()
                return executor.list_inbox(inbox, images_only, limit)
            except (PolicyError, OSError) as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Import the most recent image(s) from the inbox into a folder "
                "inside the repo. Use this right after rendering new art with "
                "your own image-generation tool (it lands in the inbox "
                "automatically) or after the user downloads an image from "
                "ChatGPT. Set move=true to move instead of copy."
            )
        )
        def import_recent_images(
            dest_folder: str = "assets", count: int = 1, move: bool = False
        ) -> str:
            try:
                _clean()
                return executor.import_recent_images(
                    policy, inbox, dest_folder, count, move
                )
            except PolicyError as exc:
                return f"refused: {exc}"

        @mcp.tool(
            description=(
                "Import a specific file (by name) from the inbox into a path "
                "inside the repo. Set move=true to move instead of copy."
            )
        )
        def import_file(src_name: str, dest: str, move: bool = False) -> str:
            try:
                return executor.import_file(policy, inbox, src_name, dest, move)
            except PolicyError as exc:
                return f"refused: {exc}"

    if config.image_gen_cmd:

        @mcp.tool(
            description=(
                "Generate an image from a text prompt using the local image "
                "generator and save it to the given path inside the project root. "
                "Unlimited and local: the image is created on this machine. Then "
                "use read_image to view the result."
            )
        )
        def generate_image(prompt: str, output: str) -> str:
            try:
                return executor.generate_image(
                    policy,
                    prompt,
                    output,
                    config.image_gen_cmd,
                    config.image_timeout,
                )
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
    if config.inbox_dir and config.inbox_ttl_days > 0:
        executor.cleanup_inbox(Path(config.inbox_dir), config.inbox_ttl_days)
    mcp = build_server(config)

    if config.transport == "stdio":
        mcp.run(transport="stdio")
        return

    # HTTP transport: serve the Streamable-HTTP ASGI app with a health route
    # and the configured authentication, then serve with uvicorn.
    import uvicorn
    from starlette.responses import JSONResponse, Response

    from .auth import HEALTH_PATH, BearerAuthMiddleware, health_endpoint

    policy = config.policy
    upload_max = 25_000_000
    allowed_origins = {"https://chatgpt.com", "https://chat.openai.com"}

    def _cors(origin: str) -> dict[str, str]:
        if origin in allowed_origins:
            return {
                "Access-Control-Allow-Origin": origin,
                "Access-Control-Allow-Methods": "POST, OPTIONS",
                "Access-Control-Allow-Headers": "Content-Type, X-Target-Path",
                "Access-Control-Max-Age": "600",
            }
        return {}

    async def upload_handler(request):
        """Receive raw image/file bytes from the browser extension and save them.

        The secret path segment is the credential; write mode and path
        containment limit what can be written. CORS is restricted to ChatGPT.
        """
        cors = _cors(request.headers.get("origin", ""))
        if request.method == "OPTIONS":
            return Response(status_code=204, headers=cors)
        path = request.query_params.get("path") or request.headers.get(
            "x-target-path", ""
        )
        if not path:
            return JSONResponse(
                {"error": "missing 'path'"}, status_code=400, headers=cors
            )
        body = await request.body()
        try:
            target = executor.write_bytes(policy, path, body, upload_max)
        except PolicyError as exc:
            return JSONResponse(
                {"error": str(exc)}, status_code=403, headers=cors
            )
        return JSONResponse(
            {"ok": True, "path": str(target), "bytes": len(body)}, headers=cors
        )

    # Register an unauthenticated health route on the MCP app.
    mcp.custom_route(HEALTH_PATH, methods=["GET"])(health_endpoint)
    # Upload endpoint lives under the secret path (so the secret gates it).
    mcp.custom_route(config.mcp_path + "/upload", methods=["POST", "OPTIONS"])(
        upload_handler
    )

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
