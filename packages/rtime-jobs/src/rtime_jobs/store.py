# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""SQLite-backed job queue.

Why SQLite (not append-only JSONL): a queue needs atomic *claim* semantics so two
worker processes never run the same job, plus in-place status transitions. SQLite
(stdlib ``sqlite3``, offline) gives both for free: a single file, WAL mode for a
concurrent reader (the gateway polling status) while the worker writes, and a
``BEGIN IMMEDIATE`` transaction around select-then-update for a race-free claim.
It also matches the repo's existing state convention (the BM25 index is SQLite
under the same ``~/.local/state/rtime-assistant`` tree).

The DB file lives OUTSIDE the repo (machine-local runtime state) and is resolved
exactly like the brain index: ``RTIME_JOBS_DB`` env wins, else
``$XDG_STATE_HOME/rtime-assistant/jobs/jobs.sqlite`` (``~/.local/state`` default).
"""

from __future__ import annotations

import json
import os
import socket
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path

from .schema import (
    BEIJING,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    Job,
    JsonObject,
    new_job_id,
    now_iso,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    id          TEXT PRIMARY KEY,
    type        TEXT NOT NULL,
    params      TEXT NOT NULL,
    status      TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    started_at  TEXT,
    finished_at TEXT,
    attempts    INTEGER NOT NULL DEFAULT 0,
    result      TEXT,
    error       TEXT,
    worker      TEXT
);
CREATE INDEX IF NOT EXISTS idx_jobs_status ON jobs(status);
"""


def default_db_path() -> Path:
    """Resolve the queue DB path (mirrors ``brain-library`` index resolution).

    ``RTIME_JOBS_DB`` overrides; otherwise the standard machine-local state dir.
    The file is never inside the repo, so it never lands in git.
    """
    raw = os.environ.get("RTIME_JOBS_DB")
    if raw:
        return Path(raw).expanduser()
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return state / "rtime-assistant" / "jobs" / "jobs.sqlite"


def default_worker_id() -> str:
    """A stable-ish worker identity for the ``worker`` column: ``host:pid``."""
    try:
        host = socket.gethostname()
    except OSError:  # pragma: no cover - hostname lookup is effectively always fine
        host = "?"
    return f"{host}:{os.getpid()}"


class JobStore:
    """A small queue over one SQLite file. Cheap to construct; opens a fresh
    connection per operation (autocommit) so it is safe to share a path across
    the gateway, the worker, and the CLI without holding a long-lived handle."""

    def __init__(self, path: str | os.PathLike[str] | None = None) -> None:
        self.path = Path(path) if path is not None else default_db_path()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Initialize schema once; executescript runs in its own transaction.
        conn = self._connect()
        try:
            conn.executescript(_SCHEMA)
        finally:
            conn.close()

    # -- connection -------------------------------------------------------
    def _connect(self) -> sqlite3.Connection:
        # isolation_level=None -> autocommit; we open explicit transactions only
        # where we need them (claim_next). WAL lets a reader and the worker
        # coexist; busy_timeout absorbs brief write contention instead of raising.
        conn = sqlite3.connect(str(self.path), timeout=30, isolation_level=None)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=30000")
        return conn

    # -- row <-> Job ------------------------------------------------------
    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> Job:
        return Job(
            id=row["id"],
            type=row["type"],
            params=json.loads(row["params"]) if row["params"] else {},
            status=row["status"],
            created_at=row["created_at"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            attempts=row["attempts"],
            result=json.loads(row["result"]) if row["result"] else None,
            error=row["error"],
            worker=row["worker"],
        )

    # -- writes -----------------------------------------------------------
    def submit(
        self, job_type: str, params: JsonObject | None = None, *, dedup: bool = True
    ) -> Job:
        """Enqueue a new ``pending`` job and return it.

        With ``dedup`` (default), an identical *pending* job (same type + same
        params) is reused instead of piling up a duplicate — so a retried submit
        or two callers asking for the same work ("rebuild the index") don't queue
        redundant runs. Params are stored canonically (sorted keys) so the match
        is key-order-independent, and the check+insert run inside one
        ``BEGIN IMMEDIATE`` so two concurrent submits can't both win. A job that
        has already started (``running``) or finished never blocks a fresh submit.
        """
        canonical = json.dumps(params or {}, ensure_ascii=False, sort_keys=True)
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                if dedup:
                    existing = conn.execute(
                        "SELECT * FROM jobs WHERE status=? AND type=? AND params=? "
                        "ORDER BY rowid LIMIT 1",
                        (STATUS_PENDING, job_type, canonical),
                    ).fetchone()
                    if existing is not None:
                        conn.execute("COMMIT")
                        return self._row_to_job(existing)
                job = Job(id=new_job_id(), type=job_type, params=params or {})
                conn.execute(
                    "INSERT INTO jobs (id, type, params, status, created_at, attempts) "
                    "VALUES (?, ?, ?, ?, ?, 0)",
                    (job.id, job.type, canonical, job.status, job.created_at),
                )
                conn.execute("COMMIT")
                return job
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        finally:
            conn.close()

    def claim_next(self, worker_id: str | None = None) -> Job | None:
        """Atomically claim the oldest ``pending`` job, flipping it to
        ``running``. Returns the claimed job, or ``None`` when the queue is empty.

        The ``BEGIN IMMEDIATE`` transaction takes the write lock before the
        SELECT, so two concurrent workers serialize here and the guarded
        ``UPDATE ... WHERE status='pending'`` makes a double-claim impossible
        (the loser updates 0 rows and retries). FIFO order is by ``rowid``
        (monotonic insertion order), robust even when several jobs share a
        seconds-resolution ``created_at``.
        """
        worker_id = worker_id or default_worker_id()
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                row = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY rowid LIMIT 1",
                    (STATUS_PENDING,),
                ).fetchone()
                if row is None:
                    conn.execute("COMMIT")
                    return None
                ts = now_iso()
                cur = conn.execute(
                    "UPDATE jobs SET status=?, started_at=?, attempts=attempts+1, "
                    "worker=? WHERE id=? AND status=?",
                    (STATUS_RUNNING, ts, worker_id, row["id"], STATUS_PENDING),
                )
                conn.execute("COMMIT")
            except Exception:
                # Best-effort rollback; if COMMIT already failed sqlite may have
                # auto-rolled-back, so a "no transaction" error here must not mask
                # the original exception.
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
            if cur.rowcount != 1:  # lost the race; let the caller poll again
                return None
            job = self._row_to_job(row)
            job.status = STATUS_RUNNING
            job.started_at = ts
            job.attempts = row["attempts"] + 1
            job.worker = worker_id
            return job
        finally:
            conn.close()

    def recover_stale_running(
        self, *, max_attempts: int = 3, stale_seconds: float | None = None
    ) -> dict[str, int]:
        """Rescue jobs wedged in ``running`` because a worker died mid-job.

        ``stale_seconds=None`` treats every ``running`` row as orphaned — correct
        at worker startup, where a freshly-started worker implies any ``running``
        job is from a crashed predecessor. With ``stale_seconds`` set, only rows
        whose ``started_at`` is older than that window are touched, so a job a
        concurrent worker is genuinely running is left alone. A job still under
        ``max_attempts`` is requeued (``running`` -> ``pending``, worker/started_at
        cleared) so it runs again; at or over it is marked ``failed`` so a poison
        job can never loop forever. Returns ``{"requeued": n, "failed": m}``.
        """
        cutoff = (
            datetime.now(BEIJING) - timedelta(seconds=stale_seconds)
            if stale_seconds is not None
            else None
        )
        requeued = failed = 0
        conn = self._connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            try:
                rows = conn.execute(
                    "SELECT id, attempts, started_at FROM jobs WHERE status=?",
                    (STATUS_RUNNING,),
                ).fetchall()
                for row in rows:
                    if cutoff is not None and row["started_at"]:
                        try:
                            started = datetime.fromisoformat(row["started_at"])
                        except ValueError:
                            started = None
                        if started is not None and started > cutoff:
                            continue  # within the grace window — still running
                    if row["attempts"] < max_attempts:
                        conn.execute(
                            "UPDATE jobs SET status=?, worker=NULL, started_at=NULL "
                            "WHERE id=? AND status=?",
                            (STATUS_PENDING, row["id"], STATUS_RUNNING),
                        )
                        requeued += 1
                    else:
                        conn.execute(
                            "UPDATE jobs SET status=?, error=?, finished_at=? "
                            "WHERE id=? AND status=?",
                            (
                                STATUS_FAILED,
                                "worker exited before completion (recovered; max attempts reached)",
                                now_iso(),
                                row["id"],
                                STATUS_RUNNING,
                            ),
                        )
                        failed += 1
                conn.execute("COMMIT")
            except Exception:
                try:
                    conn.execute("ROLLBACK")
                except sqlite3.OperationalError:
                    pass
                raise
        finally:
            conn.close()
        return {"requeued": requeued, "failed": failed}

    def complete(self, job_id: str, result: JsonObject) -> None:
        """Mark a job ``succeeded`` and store its result."""
        self._finish(job_id, "succeeded", result=result, error=None)

    def fail(self, job_id: str, error: str) -> None:
        """Mark a job ``failed`` and store the error string."""
        self._finish(job_id, "failed", result=None, error=error)

    def _finish(
        self,
        job_id: str,
        status: str,
        *,
        result: JsonObject | None,
        error: str | None,
    ) -> None:
        conn = self._connect()
        try:
            conn.execute(
                "UPDATE jobs SET status=?, result=?, error=?, finished_at=? WHERE id=?",
                (
                    status,
                    json.dumps(result, ensure_ascii=False) if result is not None else None,
                    error,
                    now_iso(),
                    job_id,
                ),
            )
        finally:
            conn.close()

    # -- reads ------------------------------------------------------------
    def get(self, job_id: str) -> Job | None:
        conn = self._connect()
        try:
            row = conn.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
            return self._row_to_job(row) if row is not None else None
        finally:
            conn.close()

    def list(self, *, status: str | None = None, limit: int = 50) -> list[Job]:
        """Most-recent-first list of jobs, optionally filtered by status."""
        if limit < 1:
            limit = 1
        conn = self._connect()
        try:
            if status:
                rows = conn.execute(
                    "SELECT * FROM jobs WHERE status=? ORDER BY rowid DESC LIMIT ?",
                    (status, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM jobs ORDER BY rowid DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [self._row_to_job(r) for r in rows]
        finally:
            conn.close()

    def counts(self) -> dict[str, int]:
        """Job counts grouped by status (e.g. ``{"pending": 2, "succeeded": 5}``)."""
        conn = self._connect()
        try:
            rows = conn.execute(
                "SELECT status, COUNT(*) AS n FROM jobs GROUP BY status"
            ).fetchall()
            return {r["status"]: r["n"] for r in rows}
        finally:
            conn.close()
