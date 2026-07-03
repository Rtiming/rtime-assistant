# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Structured run logging for the Python bridge candidate."""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any

_SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "app_secret",
    "identity",
    "id_card",
    "address",
)


def utc_timestamp() -> str:
    return (
        datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z")
    )


def new_run_id(prefix: str = "run") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


def hash_value(value: Any) -> str | None:
    if value is None:
        return None
    data = str(value).encode("utf-8", errors="replace")
    return "sha256:" + hashlib.sha256(data).hexdigest()[:16]


def summarize_text(text: str, limit: int = 160) -> str:
    collapsed = re.sub(r"\s+", " ", text or "").strip()
    if len(collapsed) <= limit:
        return collapsed
    return collapsed[: limit - 3] + "..."


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = (
                "[REDACTED]" if _is_sensitive_key(key_text) else _redact(item)
            )
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, tuple):
        return [_redact(item) for item in value]
    return value


def _default_log_path() -> str:
    return os.path.expanduser("~/.local/state/rtime-assistant/run-log.jsonl")


def _configured_log_path() -> str | None:
    raw = os.getenv("RTIME_ASSISTANT_RUN_LOG", "").strip()
    if raw.lower() in {"0", "false", "off", "none", "disabled"}:
        return None
    return os.path.expanduser(raw) if raw else _default_log_path()


def append_run_event(event: str, **fields: Any) -> bool:
    path = _configured_log_path()
    if not path:
        return False

    record = {
        "schema_version": 1,
        "event": event,
        "timestamp": utc_timestamp(),
    }
    record.update(_redact(fields))

    try:
        parent = os.path.dirname(path)
        if parent:
            os.makedirs(parent, exist_ok=True)
        with open(path, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
        return True
    except Exception as exc:
        print(f"[run_log] write failed: {type(exc).__name__}: {exc}", flush=True)
        return False
