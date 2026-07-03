# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The worker side: claim pending jobs, run their handler, record the outcome.

Two entry shapes:
- :func:`run_pending` — drain the currently-pending jobs once and return a
  summary. Ideal for a cron/systemd-timer tick and for tests (deterministic).
- :func:`worker_loop` — long-running: drain, then poll for more; optionally
  self-exit after an idle window (so a systemd ``Restart=always`` unit recycles
  cleanly instead of blocking forever).

A handler that raises is caught here and the job is marked ``failed`` — one bad
job never takes the worker (or the rest of the queue) down with it.
"""

from __future__ import annotations

import time
from typing import Any, Callable

from .handlers import HANDLERS, JobError
from .store import JobStore, default_worker_id

JsonObject = dict[str, Any]
HandlerMap = dict[str, Callable[[JsonObject], JsonObject]]


def run_pending(
    store: JobStore,
    *,
    handlers: HandlerMap | None = None,
    worker_id: str | None = None,
    max_jobs: int | None = None,
    recover_stale_seconds: float | None = None,
    max_attempts: int = 3,
) -> JsonObject:
    """Claim-and-run pending jobs until the queue drains (or ``max_jobs`` reached).

    Returns ``{"ok", "processed": [{"id","type","status",...}], "count", "db_path"}``
    (``db_path`` so a gateway/worker queue-path mismatch is diagnosable). Never
    raises for a job-level failure: the bad job is recorded ``failed`` and the
    drain continues. For a standalone/cron tick, pass ``recover_stale_seconds`` to
    rescue jobs a previous crashed tick left ``running`` before draining.
    """
    handlers = handlers if handlers is not None else HANDLERS
    worker_id = worker_id or default_worker_id()
    recovered: JsonObject | None = None
    if recover_stale_seconds is not None:
        recovered = store.recover_stale_running(
            max_attempts=max_attempts, stale_seconds=recover_stale_seconds
        )
    processed: list[JsonObject] = []
    while max_jobs is None or len(processed) < max_jobs:
        job = store.claim_next(worker_id)
        if job is None:
            break
        entry: JsonObject = {"id": job.id, "type": job.type}
        try:
            handler = handlers.get(job.type)
            if handler is None:
                raise JobError(f"no handler registered for job type: {job.type}")
            result = handler(job.params)
            if not isinstance(result, dict):
                result = {"value": result}
            store.complete(job.id, result)
            entry["status"] = "succeeded"
        except Exception as exc:  # noqa: BLE001 — isolate one bad job from the rest
            store.fail(job.id, str(exc)[:2000])
            entry["status"] = "failed"
            entry["error"] = str(exc)[:500]
        processed.append(entry)
    summary: JsonObject = {
        "ok": True,
        "processed": processed,
        "count": len(processed),
        "db_path": str(store.path),
    }
    if recovered is not None:
        summary["recovered"] = recovered
    return summary


def worker_loop(
    store: JobStore,
    *,
    handlers: HandlerMap | None = None,
    worker_id: str | None = None,
    poll_seconds: float = 2.0,
    idle_exit_seconds: float | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> JsonObject:
    """Drain, then poll for more work; self-exit after ``idle_exit_seconds`` with
    no work (``None`` = run forever). Returns a final summary on exit.

    On startup it first rescues any job left ``running`` by a crashed predecessor
    (a fresh worker has claimed nothing yet, so a ``running`` row is orphaned).
    ``sleep`` is injectable so tests can drive the loop without real waiting.
    """
    worker_id = worker_id or default_worker_id()
    recovered = store.recover_stale_running(max_attempts=max_attempts)
    total = 0
    idle_for = 0.0
    while True:
        summary = run_pending(
            store, handlers=handlers, worker_id=worker_id, max_attempts=max_attempts
        )
        n = summary["count"]
        total += n
        if n:
            idle_for = 0.0
            continue
        if idle_exit_seconds is not None and idle_for >= idle_exit_seconds:
            return {
                "ok": True,
                "total_processed": total,
                "exit": "idle",
                "recovered": recovered,
                "db_path": str(store.path),
            }
        sleep(poll_seconds)
        idle_for += poll_seconds
