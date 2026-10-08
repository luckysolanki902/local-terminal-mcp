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


def test_read_file_window(repo: Path) -> None:
    (repo / "big.txt").write_text("".join(f"line{i}\n" for i in range(1, 51)))
    policy = Policy(root=repo)
    out = executor.read_file(policy, "big.txt", offset=10, limit=3)
    assert out.startswith("[lines 10-12 of 50]\n")
    assert "line10\nline11\nline12\n" in out
    assert "line13" not in out


def test_read_file_offset_past_end(repo: Path) -> None:
    (repo / "short.txt").write_text("one\ntwo\n")
    policy = Policy(root=repo)
    out = executor.read_file(policy, "short.txt", offset=99)
    assert "past the end" in out


def test_read_file_no_window_reads_all(repo: Path) -> None:
    policy = Policy(root=repo)
    assert executor.read_file(policy, "a.txt") == "hello"


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


def test_move_file(repo) -> None:
    from local_terminal_mcp import executor
    policy = Policy(root=repo, allow_write=True)
    executor.move_file(policy, "a.txt", "sub/moved.txt")
    assert (repo / "sub" / "moved.txt").read_text() == "hello"
    assert not (repo / "a.txt").exists()


def test_copy_file(repo) -> None:
    from local_terminal_mcp import executor
    policy = Policy(root=repo, allow_write=True)
    executor.copy_file(policy, "a.txt", "copy.txt")
    assert (repo / "copy.txt").read_text() == "hello"
    assert (repo / "a.txt").exists()


def test_move_requires_write(repo) -> None:
    from local_terminal_mcp import executor
    policy = Policy(root=repo, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.move_file(policy, "a.txt", "b.txt")


def test_move_cannot_escape_root(repo) -> None:
    from local_terminal_mcp import executor
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError):
        executor.move_file(policy, "a.txt", "../escape.txt")


def test_move_missing_src(repo) -> None:
    from local_terminal_mcp import executor
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError, match="does not exist"):
        executor.move_file(policy, "nope.txt", "x.txt")
