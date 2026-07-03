# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The job contract: a single :class:`Job` record and its status vocabulary.

Kept deliberately tiny (the P7 "minimal job contract"): the required fields are
``id / type / params / status / created_at / result / error``; the rest
(``started_at / finished_at / attempts / worker``) are cheap metadata that
directly serve the stated goals — recovery, audit, and retry.

Status lifecycle:  ``pending`` -> ``running`` -> ``succeeded`` | ``failed``.
"""

from __future__ import annotations

import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

# All timestamps are Beijing local time (UTC+8), matching the deploy/bin tools.
BEIJING = timezone(timedelta(hours=8))

STATUS_PENDING = "pending"
STATUS_RUNNING = "running"
STATUS_SUCCEEDED = "succeeded"
STATUS_FAILED = "failed"

TERMINAL_STATUSES = frozenset({STATUS_SUCCEEDED, STATUS_FAILED})
ALL_STATUSES = frozenset(
    {STATUS_PENDING, STATUS_RUNNING, STATUS_SUCCEEDED, STATUS_FAILED}
)

JsonObject = dict[str, Any]


def now_iso() -> str:
    """Current Beijing time as an ISO-8601 string at seconds resolution."""
    return datetime.now(BEIJING).isoformat(timespec="seconds")


def new_job_id() -> str:
    """A collision-free job id, e.g. ``job-3f2a...``."""
    return f"job-{uuid.uuid4().hex}"


@dataclass
class Job:
    """One unit of deferred work.

    ``params`` and ``result`` are plain JSON objects (serialized to TEXT in the
    store). ``result`` is whatever the handler returns on success; ``error`` is a
    short human string on failure. Exactly one of them is set once the job reaches
    a terminal status.
    """

    id: str
    type: str
    params: JsonObject = field(default_factory=dict)
    status: str = STATUS_PENDING
    created_at: str = field(default_factory=now_iso)
    started_at: str | None = None
    finished_at: str | None = None
    attempts: int = 0
    result: JsonObject | None = None
    error: str | None = None
    worker: str | None = None

    def to_dict(self) -> JsonObject:
        return asdict(self)

    @property
    def is_terminal(self) -> bool:
        return self.status in TERMINAL_STATUSES
