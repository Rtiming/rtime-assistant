# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ bridge session store — re-exports the shared core store.

Promoted into ``packages/rtime-chat-runtime`` as the unified slim session store
(channel-unification P1). This thin re-export keeps the ``qq_bridge.sessions`` import
path stable while the implementation lives in the shared core (no behavior change).
"""

from __future__ import annotations

from rtime_chat_runtime.session_store import Session, SessionStore

__all__ = ["Session", "SessionStore"]
