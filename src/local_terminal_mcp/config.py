"""Configuration loading from environment variables and CLI overrides.

Every setting has an ``LTMCP_`` environment variable and a corresponding CLI
flag. The resulting :class:`Policy` plus transport settings fully determine the
server's behaviour and security posture.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from .policy import DEFAULT_ALLOWED_COMMANDS, Policy

ENV_PREFIX = "LTMCP_"


def _env(name: str, default: str | None = None) -> str | None:
    return os.environ.get(ENV_PREFIX + name, default)


def _env_bool(name: str, default: bool) -> bool:
    raw = _env(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int) -> int:
    raw = _env(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError:
        raise ConfigError(
            f"{ENV_PREFIX}{name} must be an integer, got {raw!r}"
        ) from None


class ConfigError(Exception):
    """Raised when the configuration is invalid or unsafe to start with."""


@dataclass
class ServerConfig:
    """Everything needed to construct and run the server."""

    policy: Policy
    transport: str = "stdio"  # "stdio" | "http"
    host: str = "127.0.0.1"
    port: int = 8000
    # auth_mode: how HTTP requests are authenticated.
    #   "bearer" - Authorization: Bearer <token> header (for clients that
    #              support custom headers).
    #   "path"   - the secret lives in the URL path (a capability URL). Used
    #              for clients like ChatGPT whose custom-MCP UI only offers
    #              OAuth or no authentication.
    auth_mode: str = "bearer"
    auth_token: str | None = None
    mcp_path: str = "/mcp"
    allowed_hosts: list[str] = field(default_factory=list)
    # Human-in-the-loop approval for commands not on the static allowlist.
    #   approval_mode: "none" (deny, default), "tty" (prompt on the server
    #   terminal), or "file" (coordinate via approvals_dir + the approve CLI).
    approval_mode: str = "none"
    approvals_dir: str | None = None
    approval_timeout: int = 60
    # Persistent JSON allowlist that "always allow" appends to.
    allowlist_file: str | None = None

    @property
    def path_secret(self) -> str:
        """The final path segment, used as the capability secret in path mode."""
        return self.mcp_path.rstrip("/").rsplit("/", 1)[-1]

    def validate(self) -> None:
        """Fail closed on unsafe combinations.

        An HTTP server must never start without a credential: a bearer token
        (``bearer`` mode) or an unguessable secret path segment (``path``
        mode), because HTTP means it is reachable over the network.
        """
        if self.transport not in {"stdio", "http"}:
            raise ConfigError(
                f"transport must be 'stdio' or 'http', got {self.transport!r}"
            )
        if self.auth_mode not in {"bearer", "path"}:
            raise ConfigError(
                f"auth mode must be 'bearer' or 'path', got {self.auth_mode!r}"
            )
        if not self.mcp_path.startswith("/"):
            raise ConfigError("mcp path must start with '/'")

        if self.transport == "http":
            if self.auth_mode == "bearer":
                if not self.auth_token:
                    raise ConfigError(
                        "refusing to start an HTTP server without an auth token; "
                        f"set {ENV_PREFIX}AUTH_TOKEN or pass --auth-token"
                    )
            elif self.auth_mode == "path":
                if len(self.path_secret) < 24:
                    raise ConfigError(
                        "path auth requires an unguessable mcp path whose final "
                        "segment is at least 24 characters, e.g. "
                        "--mcp-path /mcp/$(openssl rand -hex 16)"
                    )

        if self.auth_token is not None and len(self.auth_token) < 16:
            raise ConfigError("auth token must be at least 16 characters")
        if self.approval_mode not in {"none", "tty", "file"}:
            raise ConfigError(
                "approval mode must be 'none', 'tty' or 'file', "
                f"got {self.approval_mode!r}"
            )
        if self.approval_mode == "file" and not self.approvals_dir:
            raise ConfigError(
                "approval mode 'file' requires --approvals-dir"
            )
        if not self.policy.root.is_dir():
            raise ConfigError(f"root {self.policy.root} is not a directory")


def _parse_commands(raw: str | None) -> frozenset[str]:
    if raw is None:
        return DEFAULT_ALLOWED_COMMANDS
    items = {c.strip() for c in raw.split(",") if c.strip()}
    return frozenset(items)


def load_config() -> ServerConfig:
    """Build a :class:`ServerConfig` purely from the environment."""
    policy = Policy(
        root=Path(_env("ROOT", os.getcwd())),
        allowed_commands=_parse_commands(_env("ALLOW_COMMANDS")),
        allow_write=_env_bool("ALLOW_WRITE", False),
        max_output_bytes=_env_int("MAX_OUTPUT_BYTES", 100_000),
        timeout_seconds=_env_int("TIMEOUT", 120),
    )
    return ServerConfig(
        policy=policy,
        transport=_env("TRANSPORT", "stdio") or "stdio",
        host=_env("HOST", "127.0.0.1") or "127.0.0.1",
        port=_env_int("PORT", 8000),
        auth_mode=_env("AUTH_MODE", "bearer") or "bearer",
        auth_token=_env("AUTH_TOKEN"),
        mcp_path=_env("MCP_PATH", "/mcp") or "/mcp",
        allowed_hosts=[
            h.strip() for h in (_env("ALLOWED_HOSTS") or "").split(",") if h.strip()
        ],
        approval_mode=_env("APPROVAL_MODE", "none") or "none",
        approvals_dir=_env("APPROVALS_DIR"),
        approval_timeout=_env_int("APPROVAL_TIMEOUT", 60),
        allowlist_file=_env("ALLOWLIST_FILE"),
    )
