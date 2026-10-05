"""Tests for importing files from a trusted inbox into the repo."""

from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from local_terminal_mcp import executor
from local_terminal_mcp.policy import Policy, PolicyError

PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


@pytest.fixture()
def dirs(tmp_path: Path):
    root = tmp_path / "repo"
    inbox = tmp_path / "inbox"
    root.mkdir()
    inbox.mkdir()
    return root, inbox


def test_list_inbox_images_only(dirs) -> None:
    root, inbox = dirs
    (inbox / "a.png").write_bytes(PNG)
    (inbox / "notes.txt").write_text("hi")
    out = executor.list_inbox(inbox, images_only=True, limit=10)
    assert "a.png" in out
    assert "notes.txt" not in out


def test_import_recent_images_copies(dirs) -> None:
    root, inbox = dirs
    (inbox / "old.png").write_bytes(PNG)
    time.sleep(0.01)
    (inbox / "new.png").write_bytes(PNG)
    # bump mtime to make ordering deterministic
    os.utime(inbox / "new.png", None)

    policy = Policy(root=root, allow_write=True)
    msg = executor.import_recent_images(policy, inbox, "assets", count=1)
    assert "new.png" in msg
    assert (root / "assets" / "new.png").read_bytes() == PNG
    # copy (default) leaves the original in place
    assert (inbox / "new.png").exists()


def test_import_recent_images_move(dirs) -> None:
    root, inbox = dirs
    (inbox / "x.png").write_bytes(PNG)
    policy = Policy(root=root, allow_write=True)
    executor.import_recent_images(policy, inbox, "assets", count=1, move=True)
    assert (root / "assets" / "x.png").exists()
    assert not (inbox / "x.png").exists()


def test_import_requires_write(dirs) -> None:
    root, inbox = dirs
    (inbox / "x.png").write_bytes(PNG)
    policy = Policy(root=root, allow_write=False)
    with pytest.raises(PolicyError, match="write operations are disabled"):
        executor.import_recent_images(policy, inbox, "assets")


def test_import_file_rejects_escape_from_inbox(dirs, tmp_path: Path) -> None:
    root, inbox = dirs
    secret = tmp_path / "secret.png"
    secret.write_bytes(PNG)
    policy = Policy(root=root, allow_write=True)
    with pytest.raises(PolicyError, match="outside the inbox"):
        executor.import_file(policy, inbox, "../secret.png", "assets/s.png")


def test_import_file_dest_contained(dirs) -> None:
    root, inbox = dirs
    (inbox / "x.png").write_bytes(PNG)
    policy = Policy(root=root, allow_write=True)
    with pytest.raises(PolicyError):
        executor.import_file(policy, inbox, "x.png", "../escape.png")


def test_import_no_images(dirs) -> None:
    root, inbox = dirs
    policy = Policy(root=root, allow_write=True)
    with pytest.raises(PolicyError, match="no images"):
        executor.import_recent_images(policy, inbox, "assets")
