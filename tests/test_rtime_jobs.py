# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for the rtime-jobs local queue + worker (P7 long-task isolation).

Covers the contract the maintainability plan asked for: submit a job -> a worker
executes it -> status flows pending->running->succeeded/failed -> the result is
queryable. Plus the safety property that a job cannot bypass owner approval, and
the real ``index-rebuild`` handler end to end on a tiny brain.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-jobs" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rtime_jobs import cli, handlers  # noqa: E402
from rtime_jobs.runner import run_pending, worker_loop  # noqa: E402
from rtime_jobs.store import JobStore, default_db_path  # noqa: E402


def _store(tmp_path: Path) -> JobStore:
    return JobStore(tmp_path / "jobs.sqlite")


# -- store: submit / get / list ----------------------------------------------
def test_submit_creates_pending_and_get_roundtrips(tmp_path):
    store = _store(tmp_path)
    job = store.submit("echo", {"a": 1})
    assert job.status == "pending"
    assert job.created_at
    fetched = store.get(job.id)
    assert fetched is not None
    assert fetched.type == "echo"
    assert fetched.params == {"a": 1}
    assert fetched.status == "pending"
    assert store.get("nope") is None


def test_list_filters_by_status_and_reports_counts(tmp_path):
    store = _store(tmp_path)
    a = store.submit("echo", {"i": 1})
    store.submit("echo", {"i": 2})  # distinct params -> a genuinely second job (not deduped)
    store.complete(a.id, {"ok": True})
    assert store.counts() == {"pending": 1, "succeeded": 1}
    pend = store.list(status="pending")
    assert [j.id for j in pend] == [j.id for j in pend if j.status == "pending"]
    assert all(j.status == "pending" for j in pend)
    succ = store.list(status="succeeded")
    assert len(succ) == 1 and succ[0].id == a.id
    assert len(store.list()) == 2  # both, unfiltered


# -- store: atomic claim ------------------------------------------------------
def test_claim_next_is_atomic_and_fifo(tmp_path):
    store = _store(tmp_path)
    first = store.submit("echo", {"n": 1})
    second = store.submit("echo", {"n": 2})

    claimed = store.claim_next("worker-A")
    assert claimed is not None
    assert claimed.id == first.id  # FIFO by insertion order
    assert claimed.status == "running"
    assert claimed.attempts == 1
    assert claimed.worker == "worker-A"
    # the claimed job is no longer pending in storage
    assert store.get(first.id).status == "running"

    # a second claim returns the *next* pending job, never the one already running
    claimed2 = store.claim_next("worker-B")
    assert claimed2 is not None and claimed2.id == second.id

    # queue now empty -> None
    assert store.claim_next("worker-C") is None


# -- runner: success / failure / unknown type --------------------------------
def test_run_pending_runs_echo_to_succeeded(tmp_path):
    store = _store(tmp_path)
    job = store.submit("echo", {"hello": "world"})
    summary = run_pending(store)
    assert summary["count"] == 1
    assert summary["processed"][0]["status"] == "succeeded"
    done = store.get(job.id)
    assert done.status == "succeeded"
    assert done.result == {"ok": True, "echo": {"hello": "world"}}
    assert done.error is None
    assert done.finished_at is not None


def test_run_pending_marks_handler_exception_failed(tmp_path):
    store = _store(tmp_path)
    job = store.submit("boom", {})

    def boom(_params):
        raise RuntimeError("kaboom")

    summary = run_pending(store, handlers={"boom": boom})
    assert summary["processed"][0]["status"] == "failed"
    failed = store.get(job.id)
    assert failed.status == "failed"
    assert "kaboom" in failed.error
    assert failed.result is None


def test_run_pending_unknown_type_marks_failed(tmp_path):
    store = _store(tmp_path)
    job = store.submit("does-not-exist", {})
    run_pending(store, handlers={})  # empty registry
    failed = store.get(job.id)
    assert failed.status == "failed"
    assert "no handler" in failed.error


def test_run_pending_isolates_one_bad_job_from_the_rest(tmp_path):
    store = _store(tmp_path)
    bad = store.submit("boom", {})
    good = store.submit("ok", {})

    def boom(_p):
        raise ValueError("x")

    def ok(_p):
        return {"ok": True}

    run_pending(store, handlers={"boom": boom, "ok": ok})
    assert store.get(bad.id).status == "failed"
    assert store.get(good.id).status == "succeeded"


def test_run_pending_respects_max_jobs(tmp_path):
    store = _store(tmp_path)
    for i in range(3):
        store.submit("echo", {"i": i})  # distinct params -> 3 jobs (dedup would collapse {})
    summary = run_pending(store, max_jobs=2)
    assert summary["count"] == 2
    assert store.counts().get("pending") == 1


# -- worker loop --------------------------------------------------------------
def test_worker_loop_self_exits_when_idle(tmp_path):
    store = _store(tmp_path)
    store.submit("echo", {})
    ticks = []

    def fake_sleep(seconds):
        ticks.append(seconds)

    out = worker_loop(
        store, poll_seconds=1.0, idle_exit_seconds=1.0, sleep=fake_sleep
    )
    assert out["exit"] == "idle"
    assert out["total_processed"] == 1
    assert store.counts().get("succeeded") == 1
    assert ticks  # it slept at least once before deciding to exit


# -- real handler: index-rebuild ---------------------------------------------
def test_index_rebuild_handler_builds_real_index(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "s.md").write_text(
        "# 仿星器\n仿星器 HTS 线圈 等离子体物理。\n", encoding="utf-8"
    )
    index = tmp_path / "idx.sqlite"
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))

    store = _store(tmp_path)
    job = store.submit(
        "index-rebuild", {"brain_root": str(brain), "index": str(index)}
    )
    summary = run_pending(store)
    assert summary["processed"][0]["status"] == "succeeded", store.get(job.id).error
    done = store.get(job.id)
    assert done.status == "succeeded"
    assert done.result.get("ok") is True
    assert index.is_file()


# -- safety: a job cannot bypass owner approval ------------------------------
def test_course_intake_apply_job_cannot_bypass_owner_approval(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    (brain / "_inbox").mkdir(parents=True)
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))

    store = _store(tmp_path)
    job = store.submit(
        "course-intake-apply", {"plan_sha": "deadbeef", "brain_root": str(brain)}
    )
    run_pending(store)
    failed = store.get(job.id)
    # The underlying owner-token gate refuses an unapproved plan, so the job fails
    # rather than silently performing a brain write.
    assert failed.status == "failed"
    assert failed.error and ("plan" in failed.error or "approval" in failed.error)


# -- CLI ----------------------------------------------------------------------
def _run_cli(args, capsys) -> dict:
    rc = cli.main(args)
    out = capsys.readouterr().out
    data = json.loads(out)
    return {"rc": rc, **data}


def test_cli_submit_rejects_unknown_type(tmp_path, capsys):
    db = str(tmp_path / "jobs.sqlite")
    res = _run_cli(["submit", "--type", "nonsense", "--db", db], capsys)
    assert res["rc"] == 1
    assert res["ok"] is False
    assert "unknown job type" in res["error"]
    assert "echo" in res["known_types"]


def test_cli_submit_get_list_roundtrip(tmp_path, capsys):
    db = str(tmp_path / "jobs.sqlite")
    sub = _run_cli(["submit", "--type", "echo", "--params", '{"x":1}', "--db", db], capsys)
    assert sub["ok"] is True and sub["status"] == "pending"
    jid = sub["job_id"]

    # drain via the worker subcommand (--db after the subcommand thanks to the
    # shared parent parser — same path the rtime-jobs-worker launcher uses)
    work = _run_cli(["worker", "--once", "--db", db], capsys)
    assert work["count"] == 1

    got = _run_cli(["get", jid, "--db", db], capsys)
    assert got["ok"] is True
    assert got["status"] == "succeeded"
    assert got["result"] == {"ok": True, "echo": {"x": 1}}

    listed = _run_cli(["list", "--db", db], capsys)
    assert listed["count"] == 1
    assert listed["counts"].get("succeeded") == 1


def test_cli_submit_params_via_stdin(tmp_path, capsys, monkeypatch):
    db = str(tmp_path / "jobs.sqlite")
    import io

    monkeypatch.setattr("sys.stdin", io.StringIO('{"secret":"s"}'))
    sub = _run_cli(["submit", "--type", "echo", "--params-stdin", "--db", db], capsys)
    assert sub["ok"] is True
    store = JobStore(db)
    assert store.get(sub["job_id"]).params == {"secret": "s"}


# -- db path resolution -------------------------------------------------------
def test_default_db_path_env_precedence(monkeypatch, tmp_path):
    monkeypatch.setenv("RTIME_JOBS_DB", str(tmp_path / "custom.sqlite"))
    assert default_db_path() == tmp_path / "custom.sqlite"

    monkeypatch.delenv("RTIME_JOBS_DB", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    assert default_db_path() == tmp_path / "state" / "rtime-assistant" / "jobs" / "jobs.sqlite"


def test_known_types_are_registered():
    assert set(handlers.known_types()) == {"echo", "index-rebuild", "course-intake-apply"}


# -- followup: pending-job dedup ---------------------------------------------
def test_submit_dedups_identical_pending_jobs(tmp_path):
    store = _store(tmp_path)
    a = store.submit("echo", {"x": 1})
    b = store.submit("echo", {"x": 1})  # identical pending -> reused, not duplicated
    assert b.id == a.id
    assert store.counts() == {"pending": 1}
    # key order does not matter (params stored canonically / sorted)
    c = store.submit("merge", {"a": 1, "b": 2})
    d = store.submit("merge", {"b": 2, "a": 1})
    assert d.id == c.id
    # different params -> a genuinely new job
    assert store.submit("echo", {"x": 2}).id != a.id
    # dedup=False forces a fresh job even when identical
    assert store.submit("echo", {"x": 1}, dedup=False).id != a.id


def test_submit_does_not_dedup_against_running(tmp_path):
    store = _store(tmp_path)
    a = store.submit("echo", {"x": 1})
    store.claim_next("w")  # a -> running, no longer pending
    b = store.submit("echo", {"x": 1})  # a not pending -> a fresh job is allowed
    assert b.id != a.id and b.status == "pending"


# -- followup: worker-crash recovery -----------------------------------------
def test_recover_stale_running_requeues_then_fails_at_cap(tmp_path):
    store = _store(tmp_path)
    job = store.submit("echo", {"x": 1})
    store.claim_next("dead-worker")  # -> running, attempts=1
    out = store.recover_stale_running(max_attempts=3)
    assert out == {"requeued": 1, "failed": 0}
    again = store.get(job.id)
    assert again.status == "pending" and again.worker is None and again.started_at is None
    # claim + recover until attempts hit the cap -> failed (no infinite poison loop)
    store.claim_next("dead-worker")  # attempts=2
    store.recover_stale_running(max_attempts=3)  # 2 < 3 -> requeue
    store.claim_next("dead-worker")  # attempts=3
    assert store.recover_stale_running(max_attempts=3) == {"requeued": 0, "failed": 1}
    failed = store.get(job.id)
    assert failed.status == "failed" and "recovered" in failed.error


def test_recover_stale_seconds_leaves_fresh_running_alone(tmp_path):
    store = _store(tmp_path)
    store.submit("echo", {"x": 1})
    job = store.claim_next("w")  # just started running
    out = store.recover_stale_running(max_attempts=3, stale_seconds=3600)
    assert out == {"requeued": 0, "failed": 0}
    assert store.get(job.id).status == "running"


def test_worker_loop_recovers_orphaned_running_at_startup(tmp_path):
    store = _store(tmp_path)
    store.submit("echo", {"x": 1})
    store.claim_next("crashed-worker")  # leaves a job stuck 'running'
    out = worker_loop(store, idle_exit_seconds=0.0, sleep=lambda s: None)
    assert out["recovered"] == {"requeued": 1, "failed": 0}
    assert "db_path" in out  # queue path surfaced (gateway/worker mismatch is diagnosable)
    assert store.counts().get("succeeded") == 1
