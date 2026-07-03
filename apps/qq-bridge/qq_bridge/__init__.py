# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ (OneBot v11) bridge for rtime-assistant.

Reverse-WebSocket OneBot endpoint for NapCat, reusing ``rtime_chat_runtime`` for
model execution, sessions, run logs, and shared testing harnesses. Access is tiered
in ``qq_bridge.app._actor_tier``; public profiles may open group Q&A and private
friend/temporary chats without changing the friend-request gate.
"""

from __future__ import annotations

from . import (
    _runtime_path,  # noqa: F401 — side effect: put rtime_chat_runtime on sys.path
)

__all__ = ["_runtime_path"]
