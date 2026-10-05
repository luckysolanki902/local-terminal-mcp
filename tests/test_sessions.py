"""Tests for persistent sessions and the pattern allowlist via Policy."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_terminal_mcp.policy import Policy, PolicyError
from local_terminal_mcp.sessions import SessionManager

# -- pattern allowlist through Policy --------------------------------------


def test_policy_allows_subcommand_pattern(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"git log *"}))
    assert policy.parse_command("git log --oneline") == ["git", "log", "--oneline"]
    with pytest.raises(PolicyError, match="not on the allowlist"):
        policy.parse_command("git push")


def test_policy_rejects_wildcard_program_at_construction(tmp_path: Path) -> None:
    with pytest.raises(PolicyError, match="invalid allowlist entry"):
        Policy(root=tmp_path, allowed_commands=frozenset({"*"}))


def test_policy_path_arg_containment(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"cat *"}))
    assert policy.parse_command("cat src/app.py") == ["cat", "src/app.py"]
    with pytest.raises(PolicyError, match="absolute or home path"):
        policy.parse_command("cat /etc/passwd")


def test_policy_path_containment_can_be_disabled(tmp_path: Path) -> None:
    policy = Policy(
        root=tmp_path,
        allowed_commands=frozenset({"cat *"}),
        contain_path_args=False,
    )
    # Now the absolute path is not rejected on containment grounds.
    assert policy.parse_command("cat /etc/passwd") == ["cat", "/etc/passwd"]


# -- session manager -------------------------------------------------------


def test_default_session_is_root(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path)
    assert mgr.get(None).cwd == tmp_path


def test_open_and_list(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path)
    s = mgr.open("build")
    assert s.name == "build"
    assert any(x.id == s.id for x in mgr.list())


def test_set_cwd_persists(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    mgr = SessionManager(tmp_path)
    s = mgr.open()
    mgr.set_cwd(s.id, tmp_path / "sub")
    assert mgr.get(s.id).cwd == tmp_path / "sub"


def test_unknown_session_raises(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path)
    with pytest.raises(KeyError):
        mgr.get("nope")


def test_close_default_resets_rather_than_deletes(tmp_path: Path) -> None:
    (tmp_path / "sub").mkdir()
    mgr = SessionManager(tmp_path)
    mgr.set_cwd(None, tmp_path / "sub")
    assert mgr.close(mgr.DEFAULT_ID) is True
    assert mgr.get(None).cwd == tmp_path  # reset to root


def test_max_sessions(tmp_path: Path) -> None:
    mgr = SessionManager(tmp_path, max_sessions=1)
    mgr.open()
    with pytest.raises(RuntimeError, match="too many sessions"):
        mgr.open()
