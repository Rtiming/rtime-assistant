# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Request admission queue for the assistant gateway.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Stdlib only, no gateway coupling — one running request plus a short FIFO wait
line, condition-based (no busy-wait). The primitive is provider-neutral and could
later be shared with packages/rtime-chat-runtime if the bridges grow a queue.
"""

from __future__ import annotations

import threading
import time


class RequestQueue:
    """One running request plus a short FIFO wait line (replaces busy-503).

    Condition-based, no busy-waiting. Streaming waiters pass a heartbeat
    callable: the client both sees queue progress and reveals disconnects —
    a failed heartbeat write dequeues the request without executing it.
    """

    def __init__(self, max_waiting: int):
        self._cond = threading.Condition()
        self._max_waiting = max_waiting
        self._active = False
        self._waiting: list[object] = []

    def try_enter(self) -> tuple[str, object | None]:
        """("run", None) slot taken now | ("wait", ticket) joined queue | ("full", None)."""
        with self._cond:
            if not self._active and not self._waiting:
                self._active = True
                return ("run", None)
            if len(self._waiting) >= self._max_waiting:
                return ("full", None)
            ticket = object()
            self._waiting.append(ticket)
            return ("wait", ticket)

    def wait_turn(
        self,
        ticket: object,
        *,
        timeout: float | None = None,
        heartbeat=None,
        heartbeat_interval: float = 3.0,
    ) -> bool:
        """Block until the run slot is ours. False = timed out or heartbeat
        failed (OSError); the ticket is removed and the caller must not run."""
        deadline = (time.monotonic() + timeout) if timeout is not None else None
        next_beat = (time.monotonic() + heartbeat_interval) if heartbeat else None
        while True:
            with self._cond:
                while True:
                    if not self._active and self._waiting and self._waiting[0] is ticket:
                        self._waiting.pop(0)
                        self._active = True
                        return True
                    now = time.monotonic()
                    if deadline is not None and now >= deadline:
                        self._drop_locked(ticket)
                        return False
                    if next_beat is not None and now >= next_beat:
                        break  # heartbeat outside the lock — socket I/O must not block peers
                    waits = [t - now for t in (deadline, next_beat) if t is not None]
                    self._cond.wait(min(waits) if waits else None)
            try:
                heartbeat()
            except OSError:
                with self._cond:
                    self._drop_locked(ticket)
                return False
            next_beat = time.monotonic() + heartbeat_interval

    def release(self) -> None:
        with self._cond:
            self._active = False
            self._cond.notify_all()

    def _drop_locked(self, ticket: object) -> None:
        if ticket in self._waiting:
            self._waiting.remove(ticket)
        self._cond.notify_all()  # head change may unblock another waiter
