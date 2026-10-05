"""Deterministic security policy for the local terminal MCP server.

This module is the security boundary. It contains **no** model, no network
code and no third-party dependencies so that it can be reasoned about and
tested in isolation. Every tool in the server routes through the functions
here before anything touches the operating system.

Design rules (in priority order):

1. **Unknown is denied.** A command whose program is not on the allowlist is
   rejected. There is no interactive "ask a human" fallback the way an
   interactive agent has, so the safe default must be *deny*.
2. **No shell metacharacters.** Pipes, chaining (``&&``, ``;``), redirects
   (``>``) and command substitution (``$(...)``, backticks) are rejected.
   Each ``run_command`` call is exactly one program, executed without a shell
   (``shell=False``), which removes an entire class of injection.
3. **Path containment.** Every file path is resolved (symlinks and ``..``
   included) and must land inside the configured root directory.
4. **Writes are opt-in.** Mutating the filesystem requires the policy to have
   ``allow_write`` enabled.
"""

from __future__ import annotations

import os
import shlex
from dataclasses import dataclass
from pathlib import Path

# Tokens that indicate shell control flow / redirection. Their mere presence
# in a command is grounds for rejection: we never run a shell, so they can only
# be an attempt to smuggle a second action past the allowlist.
_OPERATOR_CHARS = frozenset(";&|()<>")

# Substrings that indicate command or variable substitution. shlex does not
# split these into neat operator tokens, so they are checked against the raw
# string directly.
_SUBSTITUTION_MARKERS = ("$(", "`", "${", "<(", ">(")

# A conservative default allowlist: read-only inspection commands that are
# useful for analysing a codebase and cannot themselves mutate state.
DEFAULT_ALLOWED_COMMANDS: frozenset[str] = frozenset(
    {
        "git",
        "rg",
        "grep",
        "ls",
        "cat",
        "head",
        "tail",
        "wc",
        "find",
        "tree",
        "stat",
        "file",
        "pwd",
        "echo",
        "diff",
    }
)


class PolicyError(Exception):
    """Raised when a request violates the security policy.

    The message is safe to return to the caller; it never contains secrets.
    """


@dataclass(frozen=True)
class Policy:
    """An immutable set of security rules applied to every request."""

    root: Path
    allowed_commands: frozenset[str] = DEFAULT_ALLOWED_COMMANDS
    allow_write: bool = False
    max_output_bytes: int = 100_000
    timeout_seconds: int = 120

    def __post_init__(self) -> None:
        # Resolve the root once so later comparisons are against a canonical,
        # symlink-free absolute path.
        object.__setattr__(self, "root", Path(self.root).expanduser().resolve())

    # -- command handling --------------------------------------------------

    def parse_command(self, command: str) -> list[str]:
        """Validate ``command`` and return its argv for shell-free execution.

        Raises :class:`PolicyError` if the command is empty, contains shell
        operators or substitution, or if its program is not on the allowlist.
        """
        if not command or not command.strip():
            raise PolicyError("empty command")

        for marker in _SUBSTITUTION_MARKERS:
            if marker in command:
                raise PolicyError(
                    f"command substitution ({marker!r}) is not allowed"
                )

        try:
            # punctuation_chars=True makes shlex emit operators such as
            # "&&", "|" and ">" as their own tokens, while still respecting
            # quotes (so `rg "a|b"` keeps "a|b" as a single literal token).
            lexer = shlex.shlex(command, posix=True, punctuation_chars=True)
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:  # e.g. unbalanced quotes
            raise PolicyError(f"could not parse command: {exc}") from None

        if not tokens:
            raise PolicyError("empty command")

        for token in tokens:
            if token and set(token) <= _OPERATOR_CHARS:
                raise PolicyError(
                    f"shell operator ({token!r}) is not allowed; "
                    "run one command per call"
                )

        program = os.path.basename(tokens[0])
        if program not in self.allowed_commands:
            raise PolicyError(f"command {program!r} is not on the allowlist")

        return tokens

    # -- path handling -----------------------------------------------------

    def resolve_path(self, path: str | os.PathLike[str]) -> Path:
        """Resolve ``path`` and guarantee it stays inside the root.

        Relative paths are taken relative to the root. Symlinks and ``..``
        segments are resolved before the containment check, so neither can be
        used to escape.
        """
        candidate = Path(path)
        if not candidate.is_absolute():
            candidate = self.root / candidate
        resolved = candidate.expanduser().resolve()

        if resolved != self.root and self.root not in resolved.parents:
            raise PolicyError(
                f"path {str(path)!r} resolves outside the allowed root"
            )
        return resolved

    def require_write(self) -> None:
        """Raise unless write operations are enabled."""
        if not self.allow_write:
            raise PolicyError(
                "write operations are disabled (start with --allow-write to enable)"
            )
