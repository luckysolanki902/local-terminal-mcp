"""Command-line entry point.

CLI flags override the ``LTMCP_`` environment variables, which in turn override
the built-in defaults.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .config import ConfigError, load_config
from .policy import Policy


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="local-terminal-mcp",
        description="A safe MCP server for running allowlisted local commands.",
    )
    parser.add_argument("--root", help="Directory the server is confined to.")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http"],
        help="Transport to serve on (default: stdio).",
    )
    parser.add_argument("--host", help="HTTP bind host (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, help="HTTP bind port (default: 8000).")
    parser.add_argument(
        "--auth",
        dest="auth_mode",
        choices=["bearer", "path"],
        help=(
            "HTTP authentication mode: 'bearer' (Authorization header) or "
            "'path' (secret in the URL path, for clients like ChatGPT that "
            "cannot send a custom header). Default: bearer."
        ),
    )
    parser.add_argument(
        "--auth-token", help="Bearer token required for 'bearer' HTTP auth."
    )
    parser.add_argument(
        "--mcp-path",
        help=(
            "Path the MCP endpoint is served at (default: /mcp). For 'path' "
            "auth, include a long random final segment, e.g. "
            "/mcp/$(openssl rand -hex 16)."
        ),
    )
    parser.add_argument(
        "--allowed-hosts",
        help=(
            "Comma-separated Host header allowlist for HTTP transport "
            "(e.g. your tunnel hostname). If omitted, any host is accepted "
            "and the bearer token is the sole gate."
        ),
    )
    parser.add_argument(
        "--allow-commands",
        help="Comma-separated allowlist of programs (overrides the default).",
    )
    parser.add_argument(
        "--allow-write",
        action="store_true",
        help="Enable the write_file tool (default: disabled).",
    )
    parser.add_argument("--max-output-bytes", type=int, help="Output truncation limit.")
    parser.add_argument("--timeout", type=int, help="Per-command timeout in seconds.")
    return parser


def _apply_overrides(args: argparse.Namespace):
    """Start from env-based config and apply any CLI overrides on top."""
    config = load_config()
    policy = config.policy

    new_root = Path(args.root).expanduser() if args.root else policy.root
    allowed = (
        frozenset(c.strip() for c in args.allow_commands.split(",") if c.strip())
        if args.allow_commands
        else policy.allowed_commands
    )
    config.policy = Policy(
        root=new_root,
        allowed_commands=allowed,
        allow_write=policy.allow_write or args.allow_write,
        max_output_bytes=args.max_output_bytes or policy.max_output_bytes,
        timeout_seconds=args.timeout or policy.timeout_seconds,
    )
    if args.transport:
        config.transport = args.transport
    if args.host:
        config.host = args.host
    if args.port:
        config.port = args.port
    if args.auth_mode:
        config.auth_mode = args.auth_mode
    if args.auth_token:
        config.auth_token = args.auth_token
    if args.mcp_path:
        config.mcp_path = args.mcp_path
    if args.allowed_hosts:
        config.allowed_hosts = [
            h.strip() for h in args.allowed_hosts.split(",") if h.strip()
        ]
    return config


def main(argv: list[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    try:
        config = _apply_overrides(args)
        config.validate()
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    # Imported here so that --help and config errors don't require the MCP
    # runtime dependencies to be importable.
    from .server import run

    run(config)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
