# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime-jobs: a minimal local job queue + worker (P7 long-task isolation).

The chat entry (Feishu/Obsidian via the library gateway) should only *submit* a
job and *query* its status/result; the actual heavy work (course intake, index
rebuild, OCR/DocPack) runs in a separate worker process. This keeps the chat
entry responsive, and makes the heavy work easy to recover, audit, and retry.

Public surface:
- :mod:`rtime_jobs.schema`   — the :class:`~rtime_jobs.schema.Job` contract.
- :mod:`rtime_jobs.store`    — the SQLite-backed :class:`~rtime_jobs.store.JobStore`.
- :mod:`rtime_jobs.handlers` — the job-type -> callable registry.
- :mod:`rtime_jobs.runner`   — drain pending jobs / the worker loop.
- :mod:`rtime_jobs.cli`      — the ``rtime-jobs`` command-line surface.
"""

from .schema import (
    ALL_STATUSES,
    STATUS_FAILED,
    STATUS_PENDING,
    STATUS_RUNNING,
    STATUS_SUCCEEDED,
    TERMINAL_STATUSES,
    Job,
)
from .store import JobStore, default_db_path

__all__ = [
    "Job",
    "JobStore",
    "default_db_path",
    "STATUS_PENDING",
    "STATUS_RUNNING",
    "STATUS_SUCCEEDED",
    "STATUS_FAILED",
    "TERMINAL_STATUSES",
    "ALL_STATUSES",
]
