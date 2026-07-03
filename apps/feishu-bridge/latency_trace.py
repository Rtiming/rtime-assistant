# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Safe segmented latency tracing for the Feishu bridge."""

from __future__ import annotations

import time
from typing import Any

from rtime_chat_runtime.run_log import append_run_event, hash_value, new_run_id


def start_trace(
    *,
    user_id: str,
    chat_id: str,
    is_group: bool,
    message_type: str,
    chat_type: str,
) -> dict[str, Any]:
    return {
        "trace_id": new_run_id("feishu-trace"),
        "started_monotonic": time.monotonic(),
        "actor_hash": hash_value(user_id),
        "chat_hash": hash_value(chat_id),
        "is_group": is_group,
        "message_type": message_type,
        "chat_type": chat_type,
        "_seen": set(),
    }


def mark(trace: dict[str, Any] | None, stage: str, **fields: Any) -> None:
    if not trace:
        return
    elapsed_ms = int((time.monotonic() - float(trace.get("started_monotonic", time.monotonic()))) * 1000)
    payload = {
        "trace_id": trace.get("trace_id"),
        "stage": stage,
        "elapsed_ms": elapsed_ms,
        "actor_hash": trace.get("actor_hash"),
        "chat_hash": trace.get("chat_hash"),
        "is_group": trace.get("is_group"),
        "message_type": trace.get("message_type"),
        "chat_type": trace.get("chat_type"),
    }
    payload.update(fields)
    append_run_event("feishu_latency_trace", **payload)


def mark_once(trace: dict[str, Any] | None, key: str, stage: str, **fields: Any) -> None:
    if not trace:
        return
    seen = trace.setdefault("_seen", set())
    if key in seen:
        return
    seen.add(key)
    mark(trace, stage, **fields)
