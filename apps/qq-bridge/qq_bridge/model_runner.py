# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ bridge model runner — re-exports the shared core runner.

The CLI model runner was promoted into ``packages/rtime-chat-runtime`` as the unified
single source (``CliModelRunner``, per docs/channel-unification-plan.zh-CN.md, P0/P1).
This thin re-export keeps the ``qq_bridge.model_runner`` import path stable while the
implementation lives in the shared core (no behavior change).
"""

from __future__ import annotations

from rtime_chat_runtime.model_runner import run_claude

__all__ = ["run_claude"]
