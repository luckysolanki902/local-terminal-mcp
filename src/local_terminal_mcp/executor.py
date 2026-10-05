"""Policy-gated filesystem and process operations.

These functions are the only place in the codebase that touch the OS. Each one
takes a :class:`~local_terminal_mcp.policy.Policy` and validates its inputs
through it before acting. Keeping them free of any MCP/network dependency means
they can be unit-tested directly.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

from .policy import Policy, PolicyError


def _truncate(text: str, limit: int) -> str:
    if len(text.encode("utf-8", "replace")) <= limit:
        return text
    clipped = text.encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")
    return clipped + f"\n\n[... output truncated to {limit} bytes ...]"


def run_command(policy: Policy, command: str, cwd: str = "") -> str:
    """Run a single allowlisted command without a shell and return its output.

    ``cwd`` is optional and, if given, must resolve inside the policy root.
    """
    argv = policy.parse_command(command)
    workdir = policy.resolve_path(cwd) if cwd else policy.root
    if not workdir.is_dir():
        raise PolicyError(f"working directory {cwd!r} is not a directory")

    try:
        completed = subprocess.run(  # noqa: S603 - argv is validated, shell=False
            argv,
            cwd=workdir,
            capture_output=True,
            text=True,
            timeout=policy.timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        raise PolicyError(f"program {argv[0]!r} was not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise PolicyError(
            f"command timed out after {policy.timeout_seconds}s"
        ) from None

    body = completed.stdout
    if completed.stderr:
        body += ("\n" if body else "") + completed.stderr
    header = f"[exit {completed.returncode}]\n"
    return header + _truncate(body, policy.max_output_bytes)


def read_file(policy: Policy, path: str) -> str:
    """Return the contents of a file inside the policy root."""
    target = policy.resolve_path(path)
    if not target.is_file():
        raise PolicyError(f"{path!r} is not a file")
    data = target.read_text(encoding="utf-8", errors="replace")
    return _truncate(data, policy.max_output_bytes)


def write_file(policy: Policy, path: str, content: str) -> str:
    """Write ``content`` to a file inside the policy root (write mode only)."""
    policy.require_write()
    target = policy.resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")
    return f"wrote {len(content)} characters to {target}"


def list_directory(policy: Policy, path: str = ".") -> str:
    """List the entries of a directory inside the policy root."""
    target = policy.resolve_path(path)
    if not target.is_dir():
        raise PolicyError(f"{path!r} is not a directory")
    lines: list[str] = []
    for entry in sorted(target.iterdir(), key=lambda p: (p.is_file(), p.name)):
        marker = "/" if entry.is_dir() else ""
        lines.append(f"{entry.name}{marker}")
    return "\n".join(lines) if lines else "(empty directory)"


__all__ = [
    "run_command",
    "read_file",
    "write_file",
    "list_directory",
    "Path",
]
