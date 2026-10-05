"""Tests for allowlist pattern matching and path-argument containment."""

from __future__ import annotations

import pytest

from local_terminal_mcp.matching import (
    CommandPattern,
    PatternError,
    matches_any,
    parse_patterns,
    path_escape_reason,
)

# -- pattern validation ----------------------------------------------------


@pytest.mark.parametrize("bad", ["*", "* foo", "?it", "git[12]", "/bin/rm", "./x"])
def test_program_wildcard_or_path_is_rejected(bad: str) -> None:
    with pytest.raises(PatternError):
        CommandPattern(bad)


def test_empty_pattern_rejected() -> None:
    with pytest.raises(PatternError):
        CommandPattern("   ")


# -- matching semantics ----------------------------------------------------


def test_bare_program_matches_any_args() -> None:
    p = CommandPattern("git")
    assert p.matches(["git"]) is True
    assert p.matches(["git", "status"]) is True
    assert p.matches(["git", "log", "--oneline"]) is True
    assert p.matches(["rm", "-rf"]) is False


def test_trailing_star_matches_any_args() -> None:
    p = CommandPattern("git *")
    assert p.matches(["git"]) is True
    assert p.matches(["git", "push", "origin"]) is True


def test_subcommand_pattern() -> None:
    p = CommandPattern("git log *")
    assert p.matches(["git", "log", "--oneline", "-5"]) is True
    assert p.matches(["git", "log"]) is True
    assert p.matches(["git", "status"]) is False


def test_exact_arg_match_without_trailing_star() -> None:
    p = CommandPattern("git status")
    assert p.matches(["git", "status"]) is True
    assert p.matches(["git", "status", "--short"]) is False


def test_matches_by_basename() -> None:
    # argv[0] may be an absolute path; it is matched by basename.
    assert CommandPattern("git").matches(["/usr/bin/git", "status"]) is True


def test_matches_any() -> None:
    patterns = parse_patterns(["git *", "rg *", "ls"])
    assert matches_any(patterns, ["rg", "foo"]) is True
    assert matches_any(patterns, ["python", "x.py"]) is False


# -- path containment ------------------------------------------------------


def test_safe_relative_args_ok() -> None:
    assert path_escape_reason(["cat", "src/app.py"]) is None
    assert path_escape_reason(["git", "log", "origin/main"]) is None
    assert path_escape_reason(["rg", "foo", "."]) is None


@pytest.mark.parametrize(
    "argv",
    [
        ["cat", "/etc/passwd"],
        ["cat", "~/secrets"],
        ["cat", "../../etc/passwd"],
        ["cat", "src/../../outside"],
    ],
)
def test_escaping_args_flagged(argv: list[str]) -> None:
    assert path_escape_reason(argv) is not None
