"""Tests for the persistent JSON allowlist."""

from __future__ import annotations

import json
from pathlib import Path

from local_terminal_mcp.allowlist import DynamicAllowlist


def test_empty_when_no_file(tmp_path: Path) -> None:
    store = DynamicAllowlist(tmp_path / "allow.json")
    assert store.contains("git") is False
    assert store.all() == frozenset()


def test_add_persists_to_disk(tmp_path: Path) -> None:
    path = tmp_path / "allow.json"
    store = DynamicAllowlist(path)
    assert store.add("python3") is True
    assert store.contains("python3") is True

    on_disk = json.loads(path.read_text())
    assert on_disk == {"version": 1, "commands": ["python3"]}


def test_add_is_idempotent(tmp_path: Path) -> None:
    store = DynamicAllowlist(tmp_path / "allow.json")
    assert store.add("node") is True
    assert store.add("node") is False  # already present


def test_reload_reads_existing(tmp_path: Path) -> None:
    path = tmp_path / "allow.json"
    DynamicAllowlist(path).add("convert")
    reloaded = DynamicAllowlist(path)
    assert reloaded.contains("convert") is True


def test_corrupt_file_is_treated_as_empty(tmp_path: Path) -> None:
    path = tmp_path / "allow.json"
    path.write_text("not json {{{")
    store = DynamicAllowlist(path)
    assert store.all() == frozenset()
    # and it can still add without crashing
    assert store.add("git") is True


def test_blank_program_not_added(tmp_path: Path) -> None:
    store = DynamicAllowlist(tmp_path / "allow.json")
    assert store.add("   ") is False
