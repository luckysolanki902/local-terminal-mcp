"""Background job execution: run long commands without blocking the MCP request.

A synchronous ``run_command`` is bounded by the connector's own request timeout
— ChatGPT drops a tool call after a minute or two — so anything slow (a pip
install, an asset build, a long extraction) must run detached. :class:`JobManager`
spawns such commands in their own process group, captures their combined
output, enforces a wall-clock timeout, and lets the model poll, list and cancel
them. Jobs run in parallel; each is independent.

Authorization is identical to ``run_command``: the command is structure-checked
and authorized (allowlist / approval) *before* anything is spawned, so a
background job can never bypass the policy.
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
import uuid
from dataclasses import dataclass, field

from .allowlist import DynamicAllowlist
from .approvals import Approver
from .executor import _authorize
from .policy import Policy, PolicyError

# Default per-job wall-clock timeout: 10 minutes.
DEFAULT_JOB_TIMEOUT = 600
# Hard ceiling so a bad `timeout_seconds` can't pin a process forever. A job may
# request up to 12 hours; anything larger is clamped to this.
MAX_JOB_TIMEOUT = 12 * 3600
# How much combined output to retain per job. Long builds emit a lot; we keep
# the most recent bytes (the tail), where errors and final status surface.
MAX_CAPTURE_BYTES = 2_000_000
# Grace period between SIGTERM and SIGKILL when stopping a job.
_KILL_GRACE_SECONDS = 5.0
# Bound on tracked jobs, so finished jobs are eventually reaped from memory.
MAX_JOBS = 100

# Statuses that mean the job is no longer running.
TERMINAL = frozenset({"succeeded", "failed", "timed_out", "cancelled", "error"})


@dataclass
class Job:
    """One background command, its process, and its captured output."""

    id: str
    command: str
    cwd: str
    timeout: int
    status: str = "running"
    returncode: int | None = None
    pid: int | None = None
    started_at: float = field(default_factory=time.time)
    ended_at: float | None = None
    _buf: bytearray = field(default_factory=bytearray, repr=False)
    _total: int = 0
    _proc: subprocess.Popen | None = field(default=None, repr=False)
    _cancel: bool = False
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False)

    # -- output buffering --------------------------------------------------

    def append(self, chunk: bytes) -> None:
        with self._lock:
            self._total += len(chunk)
            self._buf += chunk
            excess = len(self._buf) - MAX_CAPTURE_BYTES
            if excess > 0:
                del self._buf[:excess]

    def output_tail(self, max_bytes: int) -> tuple[str, int, int]:
        """Return (text, shown_bytes, total_bytes) for the most recent output."""
        with self._lock:
            data = bytes(self._buf)
            total = self._total
        if max_bytes and len(data) > max_bytes:
            data = data[-max_bytes:]
        text = data.decode("utf-8", "replace")
        return text, len(data), total

    # -- derived views -----------------------------------------------------

    def elapsed(self) -> float:
        return (self.ended_at or time.time()) - self.started_at

    @property
    def is_running(self) -> bool:
        return self.status not in TERMINAL


class JobManager:
    """Spawns and tracks background jobs. Thread-safe; jobs run in parallel."""

    def __init__(self) -> None:
        self._jobs: dict[str, Job] = {}
        self._mlock = threading.Lock()

    # -- lifecycle ---------------------------------------------------------

    def start(
        self,
        policy: Policy,
        command: str,
        cwd: str,
        timeout: int,
        store: DynamicAllowlist | None,
        approver: Approver | None,
    ) -> Job:
        """Authorize ``command`` and launch it detached. Raises PolicyError.

        Authorization (allowlist / approval) happens here, before the process
        is spawned, exactly as for ``run_command``.
        """
        argv = policy.parse_structure(command)
        if "/" in argv[0]:
            raise PolicyError(f"program {argv[0]!r} must be a bare name, not a path")
        _authorize(policy, argv, command, store, approver)

        workdir = policy.resolve_path(cwd) if cwd else policy.root
        if not workdir.is_dir():
            raise PolicyError(f"working directory {cwd!r} is not a directory")

        secs = int(timeout) if timeout else DEFAULT_JOB_TIMEOUT
        secs = max(1, min(secs, MAX_JOB_TIMEOUT))

        job = Job(
            id=uuid.uuid4().hex[:12],
            command=command,
            cwd=str(workdir),
            timeout=secs,
        )
        try:
            proc = subprocess.Popen(  # noqa: S603 - argv validated, shell=False
                argv,
                cwd=str(workdir),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                start_new_session=True,  # own process group → killable as a unit
                bufsize=0,
            )
        except FileNotFoundError:
            raise PolicyError(f"program {argv[0]!r} was not found on PATH") from None
        except OSError as exc:
            raise PolicyError(f"could not start job: {exc}") from None

        job._proc = proc
        job.pid = proc.pid
        with self._mlock:
            self._prune_locked()
            self._jobs[job.id] = job
        threading.Thread(target=self._pump, args=(job, proc), daemon=True).start()
        threading.Thread(target=self._monitor, args=(job, proc), daemon=True).start()
        return job

    def get(self, job_id: str) -> Job | None:
        return self._jobs.get(job_id)

    def list(self) -> list[Job]:
        with self._mlock:
            jobs = list(self._jobs.values())
        return sorted(jobs, key=lambda j: j.started_at, reverse=True)

    def cancel(self, job_id: str) -> Job | None:
        """Terminate a running job's process group. Returns the Job, or None."""
        job = self._jobs.get(job_id)
        if job is None or not job.is_running or job._proc is None:
            return job
        job._cancel = True
        self._kill(job._proc)
        return job

    def wait(self, job_id: str, timeout: float) -> Job | None:
        """Block until the job finishes or ``timeout`` seconds elapse."""
        job = self._jobs.get(job_id)
        if job is None:
            return None
        deadline = time.monotonic() + max(0.0, timeout)
        while job.is_running and time.monotonic() < deadline:
            time.sleep(0.25)
        return job

    # -- internals ---------------------------------------------------------

    def _pump(self, job: Job, proc: subprocess.Popen) -> None:
        """Drain the process's combined output into the job buffer until EOF."""
        stream = proc.stdout
        if stream is None:
            return
        try:
            while True:
                chunk = stream.read(4096)
                if not chunk:
                    break
                job.append(chunk)
        except (OSError, ValueError):
            pass
        finally:
            try:
                stream.close()
            except OSError:
                pass

    def _monitor(self, job: Job, proc: subprocess.Popen) -> None:
        """Wait for the process, enforcing the timeout, then set final status."""
        try:
            rc = proc.wait(timeout=job.timeout)
            timed_out = False
        except subprocess.TimeoutExpired:
            self._kill(proc)
            rc = proc.wait()
            timed_out = True
        job.returncode = rc
        job.ended_at = time.time()
        if job._cancel:
            job.status = "cancelled"
        elif timed_out:
            job.status = "timed_out"
            job.append(f"\n[job timed out after {job.timeout}s]\n".encode())
        else:
            job.status = "succeeded" if rc == 0 else "failed"

    def _kill(self, proc: subprocess.Popen) -> None:
        """SIGTERM the process group, then SIGKILL if it doesn't exit in time."""
        try:
            pgid = os.getpgid(proc.pid)
        except (ProcessLookupError, OSError):
            return
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            return
        deadline = time.time() + _KILL_GRACE_SECONDS
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.1)
        try:
            os.killpg(pgid, signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    def _prune_locked(self) -> None:
        """Drop oldest finished jobs once the table is full (caller holds lock)."""
        if len(self._jobs) < MAX_JOBS:
            return
        finished = sorted(
            (j for j in self._jobs.values() if not j.is_running),
            key=lambda j: j.ended_at or 0.0,
        )
        for job in finished:
            if len(self._jobs) < MAX_JOBS:
                break
            self._jobs.pop(job.id, None)
