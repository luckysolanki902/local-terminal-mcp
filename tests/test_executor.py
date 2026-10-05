"""Tests for the policy-gated OS operations."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_terminal_mcp import executor
from local_terminal_mcp.policy import Policy, PolicyError


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "a.txt").write_text("hello")
    (tmp_path / "sub").mkdir()
    (tmp_path / "sub" / "b.txt").write_text("world")
    return tmp_path


def test_run_command_echo(repo: Path) -> None:
    policy = Policy(root=repo)
    out = executor.run_command(policy, "echo hi")
    assert "hi" in out
    assert out.startswith("[exit 0]")


def test_run_command_denied(repo: Path) -> None:
    policy = Policy(root=repo)
    with pytest.raises(PolicyError):
        executor.run_command(policy, "rm a.txt")


def test_run_command_output_truncated(repo: Path) -> None:
    policy = Policy(root=repo, max_output_bytes=10)
    out = executor.run_command(policy, "echo abcdefghijklmnopqrstuvwxyz")
    assert "truncated" in out


def test_read_file(repo: Path) -> None:
    policy = Policy(root=repo)
    assert executor.read_file(policy, "a.txt") == "hello"


def test_read_file_outside_root_denied(repo: Path) -> None:
    policy = Policy(root=repo)
    with pytest.raises(PolicyError):
        executor.read_file(policy, "/etc/hosts")


def test_write_file_denied_without_write_mode(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.write_file(policy, "new.txt", "data")


def test_write_file_allowed_in_write_mode(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    executor.write_file(policy, "nested/new.txt", "data")
    assert (repo / "nested" / "new.txt").read_text() == "data"


def test_write_file_cannot_escape_root(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError):
        executor.write_file(policy, "../escape.txt", "data")


def test_list_directory(repo: Path) -> None:
    policy = Policy(root=repo)
    listing = executor.list_directory(policy, ".")
    assert "sub/" in listing
    assert "a.txt" in listing
