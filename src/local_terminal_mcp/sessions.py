"""Lightweight persistent sessions: a working directory that survives calls.

This gives the ergonomics of a terminal (``cd`` once, then run commands from
there) **without** a real shell. Each session is just a remembered working
directory inside the root; commands still run one-at-a-time with ``shell=False``
and the allowlist, so none of the safety properties change.

Multiple named sessions are supported. A ``default`` session is created lazily
so callers that don't care about sessions still get persistent ``cd``.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class Session:
    id: str
    name: str
    cwd: Path
    env: dict[str, str] = field(default_factory=dict)


class SessionManager:
    """Thread-safe registry of sessions, each with its own working directory."""

    DEFAULT_ID = "default"

    def __init__(self, root: Path, max_sessions: int = 32) -> None:
        self.root = root
        self.max_sessions = max_sessions
        self._lock = threading.Lock()
        self._sessions: dict[str, Session] = {}

    def _ensure_default(self) -> Session:
        sess = self._sessions.get(self.DEFAULT_ID)
        if sess is None:
            sess = Session(self.DEFAULT_ID, "default", self.root)
            self._sessions[self.DEFAULT_ID] = sess
        return sess

    def get(self, session_id: str | None) -> Session:
        """Return the session, creating the default one on demand.

        Raises KeyError if a specific (non-default) id does not exist.
        """
        with self._lock:
            if not session_id or session_id == self.DEFAULT_ID:
                return self._ensure_default()
            sess = self._sessions.get(session_id)
            if sess is None:
                raise KeyError(session_id)
            return sess

    def open(self, name: str = "") -> Session:
        with self._lock:
            if len(self._sessions) >= self.max_sessions:
                raise RuntimeError(
                    f"too many sessions (max {self.max_sessions})"
                )
            sid = uuid.uuid4().hex[:8]
            sess = Session(sid, name or sid, self.root)
            self._sessions[sid] = sess
            return sess

    def close(self, session_id: str) -> bool:
        with self._lock:
            if session_id == self.DEFAULT_ID:
                # Reset the default session rather than deleting it.
                self._ensure_default().cwd = self.root
                return True
            return self._sessions.pop(session_id, None) is not None

    def list(self) -> list[Session]:
        with self._lock:
            return list(self._sessions.values())

    def set_cwd(self, session_id: str | None, cwd: Path) -> Session:
        sess = self.get(session_id)
        with self._lock:
            sess.cwd = cwd
            return sess
