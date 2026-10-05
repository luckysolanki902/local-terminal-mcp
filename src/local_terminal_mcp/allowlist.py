"""Persistent JSON allowlist of approved commands.

The built-in allowlist (from configuration) is the baseline. This store holds
programs that the user has chosen to *always allow* at runtime; they are
appended to a JSON file so the decision survives restarts, mirroring how
Claude Code persists an "always allow" into its settings.

File format::

    {"version": 1, "commands": ["git", "rg", "python3"]}

The module is dependency-free so it can be audited and tested in isolation.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
from pathlib import Path


class DynamicAllowlist:
    """A JSON-backed set of program names that persists across restarts."""

    def __init__(self, path: str | os.PathLike[str]) -> None:
        self.path = Path(path).expanduser()
        self._lock = threading.Lock()
        self._commands: set[str] = set()
        self._load()

    def _load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return
        except (json.JSONDecodeError, OSError):
            # A corrupt or unreadable file must not weaken the baseline: treat
            # it as empty rather than crashing the server.
            return
        commands = raw.get("commands", []) if isinstance(raw, dict) else []
        self._commands = {str(c).strip() for c in commands if str(c).strip()}

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"version": 1, "commands": sorted(self._commands)}
        # Atomic write: temp file in the same directory, then os.replace.
        fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
                fh.write("\n")
            os.replace(tmp, self.path)
        finally:
            if os.path.exists(tmp):
                os.unlink(tmp)

    def contains(self, program: str) -> bool:
        with self._lock:
            return program in self._commands

    def add(self, program: str) -> bool:
        """Persist ``program`` as always-allowed. Returns True if newly added."""
        program = program.strip()
        if not program:
            return False
        with self._lock:
            if program in self._commands:
                return False
            self._commands.add(program)
            self._save()
            return True

    def all(self) -> frozenset[str]:
        with self._lock:
            return frozenset(self._commands)
