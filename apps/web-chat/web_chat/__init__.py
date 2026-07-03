# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Browser Q&A entry for rtime-assistant (T5a/T5b).

A thin stdlib HTTP app: single static index.html + JSON-over-SSE chat endpoint,
running every turn through ``rtime_chat_runtime`` (tool_policy / session_store /
run_log / model_runner) — the same runtime path as the QQ and Feishu bridges,
NEVER a direct model/LiteLLM call. See
docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §5.
"""

from __future__ import annotations

from . import (
    _runtime_path,  # noqa: F401 — side effect: put rtime_chat_runtime on sys.path
)

__all__ = ["_runtime_path"]
