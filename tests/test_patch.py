"""Tests for the policy-gated V4A apply_patch layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from local_terminal_mcp.patch import PatchSyntaxError, apply_patch, parse_patch
from local_terminal_mcp.policy import Policy, PolicyError


@pytest.fixture()
def repo(tmp_path: Path) -> Path:
    (tmp_path / "hello.py").write_text("def hi():\n    return 1\n\nprint(hi())\n")
    (tmp_path / "gone.txt").write_text("delete me\n")
    return tmp_path


def _wrap(*body: str) -> str:
    return "*** Begin Patch\n" + "\n".join(body) + "\n*** End Patch\n"


def test_single_hunk_update_changes_only_target(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    out = apply_patch(
        policy,
        _wrap(
            "*** Update File: hello.py",
            "@@ def hi():",
            "-    return 1",
            "+    return 2",
        ),
    )
    assert "1 modified" in out
    assert (repo / "hello.py").read_text() == "def hi():\n    return 2\n\nprint(hi())\n"


def test_multi_file_update_add_delete(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    out = apply_patch(
        policy,
        _wrap(
            "*** Update File: hello.py",
            "@@ def hi():",
            "-    return 1",
            "+    return 9",
            "*** Add File: pkg/new.py",
            "+VALUE = 7",
            "*** Delete File: gone.txt",
        ),
    )
    assert "1 added, 1 modified, 1 deleted" in out
    assert (repo / "pkg" / "new.py").read_text() == "VALUE = 7"
    assert not (repo / "gone.txt").exists()
    assert "return 9" in (repo / "hello.py").read_text()


def test_rename_moves_content_and_removes_source(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    out = apply_patch(
        policy,
        _wrap(
            "*** Update File: hello.py",
            "*** Move to: renamed.py",
            "@@ def hi():",
            "-    return 1",
            "+    return 3",
        ),
    )
    assert "renamed" in out
    assert not (repo / "hello.py").exists()
    assert "return 3" in (repo / "renamed.py").read_text()


def test_context_mismatch_refuses_without_writing(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    before = (repo / "hello.py").read_text()
    with pytest.raises(PolicyError, match="hello.py"):
        apply_patch(
            policy,
            _wrap(
                "*** Update File: hello.py",
                "@@",
                "-    return 999",
                "+    return 0",
            ),
        )
    assert (repo / "hello.py").read_text() == before


def test_absolute_path_refused(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError):
        apply_patch(policy, _wrap("*** Delete File: /etc/hosts"))


def test_dotdot_escape_refused(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError):
        apply_patch(policy, _wrap("*** Add File: ../escape.txt", "+x"))


def test_malformed_envelope_reports_line(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PatchSyntaxError) as exc:
        apply_patch(policy, "*** Begin Patch\nnot a header\n*** End Patch\n")
    assert exc.value.line == 2


def test_missing_end_patch_refused(repo: Path) -> None:
    with pytest.raises(PatchSyntaxError, match="End Patch"):
        parse_patch("*** Begin Patch\n*** Delete File: gone.txt\n")


def test_failed_multi_patch_is_atomic(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    (repo / "taken.txt").write_text("already here\n")
    before = (repo / "hello.py").read_text()
    with pytest.raises(PolicyError):
        apply_patch(
            policy,
            _wrap(
                "*** Update File: hello.py",
                "@@ def hi():",
                "-    return 1",
                "+    return 5",
                "*** Add File: taken.txt",
                "+boom",
            ),
        )
    assert (repo / "hello.py").read_text() == before
    assert (repo / "taken.txt").read_text() == "already here\n"


def test_binary_target_refused(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    (repo / "img.png").write_bytes(b"\x89PNG\x00\x00binary")
    with pytest.raises(PolicyError, match="binary"):
        apply_patch(
            policy, _wrap("*** Update File: img.png", "@@", "-x", "+y")
        )


def test_over_cap_input_refused(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    huge = "*** Begin Patch\n" + ("+x\n" * 100_000) + "*** End Patch\n"
    with pytest.raises(PatchSyntaxError, match="exceeds"):
        apply_patch(policy, huge)


def test_write_disabled_refuses(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=False)
    with pytest.raises(PolicyError, match="write"):
        apply_patch(
            policy,
            _wrap("*** Update File: hello.py", "@@", "-    return 1", "+    return 2"),
        )


def test_add_over_existing_refused(repo: Path) -> None:
    policy = Policy(root=repo, allow_write=True)
    with pytest.raises(PolicyError, match="already exists"):
        apply_patch(policy, _wrap("*** Add File: hello.py", "+nope"))
