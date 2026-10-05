"""Human-in-the-loop approval for commands not on the allowlist.

When a command's program is not already allowed, the server asks the user —
locally, on the machine the server runs on — whether to run it. This mirrors
Claude Code:

- **deny**  -> the command is refused.
- **once**  -> the command runs this time only; nothing is persisted.
- **always** -> the command runs *and* the program is appended to the JSON
  allowlist so it is permitted without asking next time.

Two approvers are provided:

- :class:`TTYApprover` prompts on the server's terminal (stdin). Use when you
  run the server attached to a terminal.
- :class:`FileApprovalQueue` coordinates through a directory so approvals work
  even when the server runs backgrounded (e.g. behind a tunnel). A separate
  ``local-terminal-mcp approve/deny`` CLI writes the decision.

A remote caller can only *request*; the decision is always made locally.
"""

from __future__ import annotations

import enum
import json
import os
import sys
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class Decision(enum.Enum):
    DENY = "deny"
    ONCE = "once"
    ALWAYS = "always"


def parse_decision(text: str) -> Decision:
    """Map a single-key / word answer to a :class:`Decision` (default DENY)."""
    t = text.strip().lower()
    if t in {"a", "always", "all"}:
        return Decision.ALWAYS
    if t in {"o", "y", "yes", "once"}:
        return Decision.ONCE
    return Decision.DENY


class Approver(Protocol):
    def request(self, program: str, command: str) -> Decision: ...


class AutoDenyApprover:
    """Default approver: refuse anything not already allowed (fail closed)."""

    def request(self, program: str, command: str) -> Decision:  # noqa: ARG002
        return Decision.DENY


class TTYApprover:
    """Prompt for a decision on the server's own terminal."""

    def request(self, program: str, command: str) -> Decision:
        prompt = (
            f"\n[local-terminal-mcp] approval needed\n"
            f"  command: {command}\n"
            f"  program: {program!r} (not on the allowlist)\n"
            f"  [o]nce / [a]lways / [d]eny (default deny): "
        )
        try:
            sys.stderr.write(prompt)
            sys.stderr.flush()
            answer = sys.stdin.readline()
        except (EOFError, OSError):
            return Decision.DENY
        return parse_decision(answer)


@dataclass
class PendingApproval:
    id: str
    program: str
    command: str
    created_at: float


class FileApprovalQueue:
    """Approval coordinated through a directory shared with the CLI.

    The server writes a request file and polls for a decision file; the
    ``approve``/``deny`` CLI (running locally) writes the decision.
    """

    def __init__(
        self, directory: str | os.PathLike[str], timeout_seconds: float = 60.0
    ) -> None:
        self.dir = Path(directory).expanduser()
        self.requests = self.dir / "requests"
        self.decisions = self.dir / "decisions"
        self.timeout_seconds = timeout_seconds
        self.poll_interval = 0.3
        self.requests.mkdir(parents=True, exist_ok=True)
        self.decisions.mkdir(parents=True, exist_ok=True)

    # -- server side -------------------------------------------------------

    def request(self, program: str, command: str) -> Decision:
        req_id = uuid.uuid4().hex[:12]
        req_path = self.requests / f"{req_id}.json"
        dec_path = self.decisions / f"{req_id}.json"
        req_path.write_text(
            json.dumps(
                {
                    "id": req_id,
                    "program": program,
                    "command": command,
                    "created_at": time.time(),
                }
            ),
            encoding="utf-8",
        )
        try:
            deadline = time.monotonic() + self.timeout_seconds
            while time.monotonic() < deadline:
                if dec_path.exists():
                    try:
                        data = json.loads(dec_path.read_text(encoding="utf-8"))
                        return parse_decision(str(data.get("decision", "deny")))
                    except (json.JSONDecodeError, OSError):
                        return Decision.DENY
                time.sleep(self.poll_interval)
            return Decision.DENY  # timed out
        finally:
            for p in (req_path, dec_path):
                try:
                    p.unlink()
                except OSError:
                    pass

    # -- CLI side ----------------------------------------------------------

    def list_pending(self) -> list[PendingApproval]:
        items: list[PendingApproval] = []
        for p in sorted(self.requests.glob("*.json")):
            try:
                d = json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                continue
            items.append(
                PendingApproval(
                    id=d.get("id", p.stem),
                    program=d.get("program", "?"),
                    command=d.get("command", "?"),
                    created_at=d.get("created_at", 0.0),
                )
            )
        return items

    def decide(self, req_id: str, decision: Decision) -> None:
        (self.decisions / f"{req_id}.json").write_text(
            json.dumps({"decision": decision.value}), encoding="utf-8"
        )
