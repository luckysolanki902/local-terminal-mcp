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
    auth_token: str | None = None
    allowed_hosts: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Fail closed on unsafe combinations.

        The most important rule: an HTTP server must never start without an
        auth token, because HTTP means it is reachable over the network.
        """
        if self.transport not in {"stdio", "http"}:
            raise ConfigError(
                f"transport must be 'stdio' or 'http', got {self.transport!r}"
            )
        if self.transport == "http" and not self.auth_token:
            raise ConfigError(
                "refusing to start an HTTP server without an auth token; "
                f"set {ENV_PREFIX}AUTH_TOKEN or pass --auth-token"
            )
        if self.auth_token is not None and len(self.auth_token) < 16:
            raise ConfigError("auth token must be at least 16 characters")
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
        auth_token=_env("AUTH_TOKEN"),
        allowed_hosts=[
            h.strip() for h in (_env("ALLOWED_HOSTS") or "").split(",") if h.strip()
        ],
    )
