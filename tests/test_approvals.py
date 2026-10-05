"""Tests for the approval flow and its integration with the executor."""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from local_terminal_mcp import executor
from local_terminal_mcp.allowlist import DynamicAllowlist
from local_terminal_mcp.approvals import (
    AutoDenyApprover,
    Decision,
    FileApprovalQueue,
    parse_decision,
)
from local_terminal_mcp.policy import Policy, PolicyError

# -- decision parsing ------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("a", Decision.ALWAYS),
        ("always", Decision.ALWAYS),
        ("o", Decision.ONCE),
        ("yes", Decision.ONCE),
        ("", Decision.DENY),
        ("d", Decision.DENY),
        ("nonsense", Decision.DENY),
    ],
)
def test_parse_decision(text: str, expected: Decision) -> None:
    assert parse_decision(text) == expected


# -- file queue ------------------------------------------------------------


def test_file_queue_once(tmp_path: Path) -> None:
    queue = FileApprovalQueue(tmp_path, timeout_seconds=5)
    result: dict[str, Decision] = {}

    def ask() -> None:
        result["d"] = queue.request("python3", "python3 gen.py")

    t = threading.Thread(target=ask)
    t.start()
    # Wait for the pending request to appear, then approve it once.
    for _ in range(50):
        pending = queue.list_pending()
        if pending:
            break
        time.sleep(0.05)
    assert len(pending) == 1
    queue.decide(pending[0].id, Decision.ONCE)
    t.join(timeout=5)
    assert result["d"] == Decision.ONCE


def test_file_queue_times_out_to_deny(tmp_path: Path) -> None:
    queue = FileApprovalQueue(tmp_path, timeout_seconds=0.3)
    assert queue.request("python3", "python3 x") == Decision.DENY


# -- executor integration --------------------------------------------------


def test_unknown_command_denied_without_approver(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"echo"}))
    with pytest.raises(PolicyError, match="not on the allowlist"):
        executor.run_command(policy, "env")


def test_autodeny_approver_refuses(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"echo"}))
    with pytest.raises(PolicyError, match="approval denied"):
        executor.run_command(policy, "env", approver=AutoDenyApprover())


class _StubApprover:
    def __init__(self, decision: Decision) -> None:
        self.decision = decision
        self.asked: list[str] = []

    def request(self, program: str, command: str) -> Decision:
        self.asked.append(program)
        return self.decision


def test_approve_once_runs_but_does_not_persist(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"echo"}))
    store = DynamicAllowlist(tmp_path / "allow.json")
    approver = _StubApprover(Decision.ONCE)

    out = executor.run_command(
        policy, "echo hi", store=store, approver=approver
    )
    assert "hi" in out
    # echo is static-allowed, so the approver is never consulted.
    assert approver.asked == []

    # 'true' is not allowed → approver consulted, allowed once, not persisted.
    out = executor.run_command(
        policy, "true", store=store, approver=approver
    )
    assert approver.asked == ["true"]
    assert store.contains("true") is False


def test_approve_always_persists(tmp_path: Path) -> None:
    policy = Policy(root=tmp_path, allowed_commands=frozenset({"echo"}))
    store = DynamicAllowlist(tmp_path / "allow.json")
    approver = _StubApprover(Decision.ALWAYS)

    executor.run_command(policy, "true", store=store, approver=approver)
    assert store.contains("true") is True

    # Second time it is already allowed: no approval needed.
    approver2 = _StubApprover(Decision.DENY)
    executor.run_command(policy, "true", store=store, approver=approver2)
    assert approver2.asked == []  # served from the persisted allowlist
