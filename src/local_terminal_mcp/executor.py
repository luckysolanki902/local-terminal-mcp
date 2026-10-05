"""Policy-gated filesystem and process operations.

These functions are the only place in the codebase that touch the OS. Each one
takes a :class:`~local_terminal_mcp.policy.Policy` and validates its inputs
through it before acting. Keeping them free of any MCP/network dependency means
they can be unit-tested directly.
"""

from __future__ import annotations

import base64
import binascii
import subprocess
from pathlib import Path

from .allowlist import DynamicAllowlist
from .approvals import Approver, Decision
from .imagegen import GeneratorConfigError, build_generator_argv
from .policy import Policy, PolicyError

# Raster image types that read_image can return to the model as an image.
IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp"}
# Keep returned images under this size; MCP clients (notably ChatGPT) cap the
# size of a tool result, and base64 inflates bytes by ~4/3.
IMAGE_READ_MAX_BYTES = 1_000_000


def _truncate(text: str, limit: int) -> str:
    if len(text.encode("utf-8", "replace")) <= limit:
        return text
    clipped = text.encode("utf-8", "replace")[:limit].decode("utf-8", "ignore")
    return clipped + f"\n\n[... output truncated to {limit} bytes ...]"


def _authorize(
    policy: Policy,
    argv: list[str],
    command: str,
    store: DynamicAllowlist | None,
    approver: Approver | None,
) -> None:
    """Decide whether ``argv`` may run, asking the approver if needed."""
    if "/" in argv[0]:
        raise PolicyError(
            f"program {argv[0]!r} must be a bare name, not a path"
        )
    # Path containment applies regardless of how the program is authorized.
    policy.check_path_args(argv)

    program = policy.program_of(argv)
    if policy.is_allowed(argv):
        return
    if store is not None and store.contains(program):
        return
    if approver is None:
        raise PolicyError(f"command {program!r} is not on the allowlist")

    decision = approver.request(program, command)
    if decision is Decision.DENY:
        raise PolicyError(
            f"command {program!r} is not on the allowlist (approval denied)"
        )
    if decision is Decision.ALWAYS and store is not None:
        store.add(program)
    # ONCE or ALWAYS: proceed with this run.


def run_command(
    policy: Policy,
    command: str,
    cwd: str = "",
    store: DynamicAllowlist | None = None,
    approver: Approver | None = None,
) -> str:
    """Run a single command without a shell and return its output.

    The program must be on the static allowlist, on the persistent dynamic
    allowlist (``store``), or approved via ``approver``. ``cwd`` is optional
    and, if given, must resolve inside the policy root.
    """
    argv = policy.parse_structure(command)
    _authorize(policy, argv, command, store, approver)
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


def write_file_base64(policy: Policy, path: str, data_base64: str) -> str:
    """Write a binary file (e.g. a PNG) from base64 content (write mode only).

    Accepts a plain base64 string or a ``data:<mime>;base64,<...>`` data URL.
    This is the path for saving generated images and other binary assets, which
    a UTF-8 text write cannot carry.
    """
    policy.require_write()
    target = policy.resolve_path(path)
    payload = data_base64.strip()
    if payload.startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    try:
        raw = base64.b64decode(payload, validate=True)
    except (binascii.Error, ValueError):
        raise PolicyError("content is not valid base64") from None
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(raw)
    return f"wrote {len(raw)} bytes to {target}"


def write_bytes(policy: Policy, path: str, data: bytes, max_bytes: int) -> Path:
    """Write raw bytes to a file inside the root (write mode). Used by /upload."""
    policy.require_write()
    if len(data) > max_bytes:
        raise PolicyError(f"upload is {len(data)} bytes; exceeds limit {max_bytes}")
    target = policy.resolve_path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return target


def resolve_image(policy: Policy, path: str) -> Path:
    """Validate that ``path`` is a readable raster image inside the root.

    Returns the resolved path; the server wraps it in an MCP image block so the
    model receives it as an image rather than text.
    """
    target = policy.resolve_path(path)
    if not target.is_file():
        raise PolicyError(f"{path!r} is not a file")
    if target.suffix.lower() not in IMAGE_EXTENSIONS:
        raise PolicyError(
            f"{path!r} is not a supported image "
            f"({', '.join(sorted(IMAGE_EXTENSIONS))})"
        )
    size = target.stat().st_size
    if size > IMAGE_READ_MAX_BYTES:
        raise PolicyError(
            f"image is {size} bytes; larger than the {IMAGE_READ_MAX_BYTES}-byte "
            "limit for inline return"
        )
    return target


def generate_image(
    policy: Policy,
    prompt: str,
    output: str,
    command_template: str,
    timeout_seconds: int,
) -> str:
    """Run the configured local image generator, writing the file into the root.

    The generator runs server-side (no shell), so the binary never has to be
    transferred through the connector.
    """
    policy.require_write()
    if not prompt.strip():
        raise PolicyError("prompt is empty")
    target = policy.resolve_path(output)
    try:
        argv = build_generator_argv(command_template, prompt, str(target))
    except GeneratorConfigError as exc:
        raise PolicyError(str(exc)) from None

    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        completed = subprocess.run(  # noqa: S603 - argv from operator template
            argv,
            cwd=policy.root,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
            shell=False,
        )
    except FileNotFoundError:
        raise PolicyError(
            f"image generator {argv[0]!r} was not found on PATH"
        ) from None
    except subprocess.TimeoutExpired:
        raise PolicyError(
            f"image generation timed out after {timeout_seconds}s"
        ) from None

    if completed.returncode != 0:
        tail = _truncate(completed.stderr or completed.stdout, 2000)
        raise PolicyError(f"generator failed (exit {completed.returncode}): {tail}")
    if not target.is_file():
        raise PolicyError("generator ran but produced no file at the output path")
    return f"generated image at {target} ({target.stat().st_size} bytes)"


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
