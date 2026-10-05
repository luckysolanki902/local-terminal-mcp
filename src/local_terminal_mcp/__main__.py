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
    parser.add_argument(
        "--no-contain-path-args",
        dest="contain_path_args",
        action="store_false",
        default=None,
        help=(
            "Disable confining command path-arguments to the root (by default, "
            "arguments that are absolute, ~, or use .. are rejected)."
        ),
    )
    parser.add_argument("--max-output-bytes", type=int, help="Output truncation limit.")
    parser.add_argument("--timeout", type=int, help="Per-command timeout in seconds.")
    parser.add_argument(
        "--approval",
        dest="approval_mode",
        choices=["none", "tty", "file"],
        help=(
            "Ask to approve commands not on the allowlist: 'none' (deny, "
            "default), 'tty' (prompt on this terminal), or 'file' (approve via "
            "the 'approve'/'deny' subcommands, for a backgrounded server)."
        ),
    )
    parser.add_argument(
        "--approvals-dir",
        help="Directory used to coordinate approvals in 'file' mode.",
    )
    parser.add_argument(
        "--approval-timeout",
        type=int,
        help="Seconds to wait for an approval decision (default: 60).",
    )
    parser.add_argument(
        "--allowlist-file",
        help=(
            "JSON file of always-allowed programs; 'always allow' decisions "
            "are appended here and persist across restarts."
        ),
    )
    parser.add_argument(
        "--image-gen-cmd",
        help=(
            "Local image generator command template with {prompt} and {output} "
            "placeholders, e.g. 'sd -p {prompt} -o {output} --steps 8'. Enables "
            "the generate_image tool (requires --allow-write)."
        ),
    )
    parser.add_argument(
        "--image-timeout",
        type=int,
        help="Timeout for image generation in seconds (default: 300).",
    )
    parser.add_argument(
        "--inbox",
        help=(
            "A staging directory (ideally a folder inside the repo) the "
            "connector may import files FROM into the repo; enables the "
            "list_inbox / import_recent_images / import_file tools (requires "
            "--allow-write)."
        ),
    )
    parser.add_argument(
        "--inbox-ttl-days",
        type=int,
        help=(
            "Auto-delete files in the inbox older than this many days "
            "(default 0 = never). Use only with a dedicated staging folder."
        ),
    )
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
    contain = (
        policy.contain_path_args
        if args.contain_path_args is None
        else args.contain_path_args
    )
    config.policy = Policy(
        root=new_root,
        allowed_commands=allowed,
        allow_write=policy.allow_write or args.allow_write,
        max_output_bytes=args.max_output_bytes or policy.max_output_bytes,
        timeout_seconds=args.timeout or policy.timeout_seconds,
        contain_path_args=contain,
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
    if args.approval_mode:
        config.approval_mode = args.approval_mode
    if args.approvals_dir:
        config.approvals_dir = args.approvals_dir
    if args.approval_timeout:
        config.approval_timeout = args.approval_timeout
    if args.allowlist_file:
        config.allowlist_file = args.allowlist_file
    if args.image_gen_cmd:
        config.image_gen_cmd = args.image_gen_cmd
    if args.image_timeout:
        config.image_timeout = args.image_timeout
    if args.inbox:
        config.inbox_dir = args.inbox
    if args.inbox_ttl_days is not None:
        config.inbox_ttl_days = args.inbox_ttl_days
    return config


_SUBCOMMANDS = {"approvals", "approve", "deny"}


def _approvals_cli(argv: list[str]) -> int:
    """Handle the local approve/deny/approvals subcommands."""
    import os

    from .approvals import Decision, FileApprovalQueue

    parser = argparse.ArgumentParser(prog="local-terminal-mcp")
    sub = parser.add_subparsers(dest="cmd", required=True)
    for name in ("approvals", "approve", "deny"):
        p = sub.add_parser(name)
        p.add_argument(
            "--approvals-dir",
            default=os.environ.get("LTMCP_APPROVALS_DIR"),
            help="Directory the server uses for approvals.",
        )
        if name in ("approve", "deny"):
            p.add_argument("id", help="Pending request id (see 'approvals').")
        if name == "approve":
            p.add_argument(
                "--always",
                action="store_true",
                help="Also add the program to the JSON allowlist.",
            )
    args = parser.parse_args(argv)

    if not args.approvals_dir:
        print(
            "error: set --approvals-dir or LTMCP_APPROVALS_DIR", file=sys.stderr
        )
        return 2

    queue = FileApprovalQueue(args.approvals_dir)

    if args.cmd == "approvals":
        pending = queue.list_pending()
        if not pending:
            print("no pending approvals")
            return 0
        for item in pending:
            print(f"{item.id}  {item.program:<12}  {item.command}")
        return 0

    if args.cmd == "approve":
        queue.decide(args.id, Decision.ALWAYS if args.always else Decision.ONCE)
        print(f"approved {args.id} ({'always' if args.always else 'once'})")
        return 0

    # deny
    queue.decide(args.id, Decision.DENY)
    print(f"denied {args.id}")
    return 0


def main(argv: list[str] | None = None) -> int:
    raw = list(sys.argv[1:] if argv is None else argv)
    if raw and raw[0] in _SUBCOMMANDS:
        return _approvals_cli(raw)

    args = _build_parser().parse_args(raw)
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
