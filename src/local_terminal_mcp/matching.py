"""Allowlist pattern matching and path-argument containment.

Allowlist entries are glob patterns over a command's argv:

- ``git``        -> the program ``git`` with **any** arguments (shorthand).
- ``git *``      -> same: ``git`` followed by any arguments.
- ``git log *``  -> ``git log`` followed by any arguments.
- ``git log``    -> exactly ``git log`` and nothing more.
- ``rg *``       -> ripgrep with any arguments.

Rules enforced at parse time:

- The program (first token) must be a literal name: no glob metacharacters and
  no ``/``. This forbids a program-position wildcard such as ``*`` (which would
  allow *any* executable) and absolute-path programs such as ``/bin/rm``.

Non-program tokens may use :mod:`fnmatch` globs; a trailing ``*`` token matches
zero or more remaining argv tokens.

Separately, :func:`path_escape_reason` flags arguments that try to leave the
project root (absolute paths, ``~`` expansion, or ``..`` traversal).
"""

from __future__ import annotations

import fnmatch
import os
import shlex

_GLOB_CHARS = set("*?[]")


class PatternError(ValueError):
    """Raised when an allowlist entry is not a valid, safe pattern."""


class CommandPattern:
    """A parsed, validated allowlist entry."""

    def __init__(self, entry: str) -> None:
        self.source = entry
        try:
            tokens = shlex.split(entry)
        except ValueError as exc:
            raise PatternError(f"could not parse pattern {entry!r}: {exc}") from None
        if not tokens:
            raise PatternError("empty allowlist pattern")

        program = tokens[0]
        if set(program) & _GLOB_CHARS:
            raise PatternError(
                f"program in pattern {entry!r} must be a literal name, not a "
                "wildcard (a bare '*' would allow any executable)"
            )
        if "/" in program:
            raise PatternError(
                f"program in pattern {entry!r} must be a bare name, not a path"
            )
        self.program = program
        self.tokens = tokens

    def matches(self, argv: list[str]) -> bool:
        """Whether ``argv`` (whose program is matched by basename) is allowed."""
        if not argv:
            return False
        head = os.path.basename(argv[0])
        if head != self.program:
            return False

        # Single-token entry: program with any arguments.
        if len(self.tokens) == 1:
            return True

        rest_patterns = self.tokens[1:]
        rest_args = argv[1:]

        if rest_patterns and rest_patterns[-1] == "*":
            required = rest_patterns[:-1]  # trailing * matches zero or more
            if len(rest_args) < len(required):
                return False
            return all(
                fnmatch.fnmatchcase(a, p)
                for a, p in zip(rest_args, required, strict=False)
            )

        if len(rest_args) != len(rest_patterns):
            return False
        return all(
            fnmatch.fnmatchcase(a, p)
            for a, p in zip(rest_args, rest_patterns, strict=True)
        )


def parse_patterns(entries: object) -> tuple[CommandPattern, ...]:
    """Parse an iterable of entry strings into validated patterns."""
    patterns: list[CommandPattern] = []
    for entry in entries:  # type: ignore[attr-defined]
        patterns.append(CommandPattern(str(entry)))
    return tuple(patterns)


def matches_any(patterns: tuple[CommandPattern, ...], argv: list[str]) -> bool:
    return any(p.matches(argv) for p in patterns)


def path_escape_reason(argv: list[str]) -> str | None:
    """Return why an argument would leave the root, or None if all are safe.

    Flags absolute paths, ``~`` expansion and ``..`` traversal in arguments
    (argv[1:]). A relative path without ``..`` resolved from a working
    directory inside the root stays inside the root, so it is allowed — this
    keeps false positives low (e.g. ``git log origin/main`` is fine).
    """
    for token in argv[1:]:
        if token.startswith(("/", "~")):
            return f"argument {token!r} is an absolute or home path"
        parts = token.replace("\\", "/").split("/")
        if ".." in parts:
            return f"argument {token!r} uses '..' to traverse outside the root"
    return None
