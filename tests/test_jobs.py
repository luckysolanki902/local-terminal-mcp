"""Tests for the background job manager."""

from __future__ import annotations

import time
from pathlib import Path

import pytest

from local_terminal_mcp.jobs import (
    DEFAULT_JOB_TIMEOUT,
    MAX_JOB_TIMEOUT,
    JobManager,
)
from local_terminal_mcp.policy import Policy, PolicyError

ALLOWED = frozenset({"printf", "sleep", "sh", "echo"})


def _policy(tmp_path: Path) -> Policy:
    return Policy(root=tmp_path, allowed_commands=ALLOWED, max_output_bytes=100_000)


def _await(job, mgr, timeout=5.0):
    mgr.wait(job.id, timeout)
    return job


def test_success_captures_output(tmp_path):
    mgr = JobManager()
    job = mgr.start(_policy(tmp_path), 'printf "hello\\nworld\\n"', "", 10, None, None)
    _await(job, mgr)
    assert job.status == "succeeded"
    assert job.returncode == 0
    text, _, _ = job.output_tail(1000)
    assert text == "hello\nworld\n"


def test_nonzero_exit_is_failed(tmp_path):
    mgr = JobManager()
    job = mgr.start(_policy(tmp_path), 'sh -c "exit 3"', "", 10, None, None)
    _await(job, mgr)
    assert job.status == "failed"
    assert job.returncode == 3


def test_timeout_kills_job(tmp_path):
    mgr = JobManager()
    job = mgr.start(_policy(tmp_path), "sleep 30", "", 1, None, None)
    _await(job, mgr, timeout=6.0)
    assert job.status == "timed_out"
    text, _, _ = job.output_tail(1000)
    assert "timed out" in text


def test_cancel_running_job(tmp_path):
    mgr = JobManager()
    job = mgr.start(_policy(tmp_path), "sleep 30", "", 60, None, None)
    time.sleep(0.5)
    mgr.cancel(job.id)
    _await(job, mgr, timeout=6.0)
    assert job.status == "cancelled"


def test_jobs_run_in_parallel(tmp_path):
    mgr = JobManager()
    started = time.monotonic()
    jobs = [
        mgr.start(_policy(tmp_path), "sleep 1", "", 10, None, None) for _ in range(4)
    ]
    for j in jobs:
        mgr.wait(j.id, 5.0)
    # Four 1s sleeps in parallel finish in ~1s, not ~4s.
    assert time.monotonic() - started < 3.0
    assert all(j.status == "succeeded" for j in jobs)


def test_unknown_program_refused(tmp_path):
    mgr = JobManager()
    with pytest.raises(PolicyError):
        mgr.start(_policy(tmp_path), "definitely_not_a_real_cmd", "", 10, None, None)


def test_disallowed_command_refused(tmp_path):
    mgr = JobManager()
    with pytest.raises(PolicyError):
        # 'rm' is not in ALLOWED and there is no approver.
        mgr.start(_policy(tmp_path), "rm -rf x", "", 10, None, None)


def test_shell_operators_refused(tmp_path):
    mgr = JobManager()
    with pytest.raises(PolicyError):
        mgr.start(_policy(tmp_path), "echo hi && echo bye", "", 10, None, None)


def test_timeout_is_clamped(tmp_path):
    mgr = JobManager()
    # Request absurd timeout -> clamped to the 12h ceiling.
    job = mgr.start(_policy(tmp_path), "sleep 0", "", 99_999_999, None, None)
    assert job.timeout == MAX_JOB_TIMEOUT
    # Zero / unset -> default.
    job2 = mgr.start(_policy(tmp_path), "sleep 0", "", 0, None, None)
    assert job2.timeout == DEFAULT_JOB_TIMEOUT


def test_list_and_get(tmp_path):
    mgr = JobManager()
    job = mgr.start(_policy(tmp_path), "sleep 0", "", 10, None, None)
    _await(job, mgr)
    assert any(j.id == job.id for j in mgr.list())
    assert mgr.get(job.id) is job
    assert mgr.get("nope") is None


def test_cwd_outside_root_refused(tmp_path):
    mgr = JobManager()
    with pytest.raises(PolicyError):
        mgr.start(_policy(tmp_path), "sleep 0", "../..", 10, None, None)
