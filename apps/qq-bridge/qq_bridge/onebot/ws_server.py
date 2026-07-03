# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OneBot v11 reverse-WebSocket server (aiohttp).

In reverse-WS, the OneBot implementation (NapCat) is the *client* that dials out to
this server. A single long-lived connection carries events upstream and action
calls downstream, correlated by an ``echo`` field. This module owns the connection
loop and action sending; event processing is delegated to the shared
``QQEventPipeline`` seam (built from the injected message / request / notice
handlers, or passed in directly), so the live WS path and the simulation harness
run the IDENTICAL chain — the server is a thin consumer that forwards each
``OutboundAction`` as a OneBot action frame. A ``/healthz`` route is exposed for
container health checks.

aiohttp is used (already in the workspace env) rather than the ``websockets``
package, and doubles as the future HTTP health surface.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import Awaitable, Callable
from typing import Any

from aiohttp import WSMsgType, web

from ..events import OutboundAction, QQEventPipeline

log = logging.getLogger("qq_bridge.ws")

# request / notice handlers receive the raw event and an async ``call_action``.
ActionFunc = Callable[[str, dict[str, Any]], Awaitable[None]]
# A message handler receives the parsed message, an async ``reply(text)`` and the raw
# ``send_action`` (for outbound media via OneBot actions; optional 3rd positional arg).
ReplyFunc = Callable[[str], Awaitable[None]]
MessageHandler = Callable[..., Awaitable[None]]
EventHandler = Callable[[dict[str, Any], ActionFunc], Awaitable[None]]
# archive sink: synchronous, best-effort, called for every message/request/notice.
ArchiveFunc = Callable[[dict[str, Any]], None]

_ARCHIVED_POST_TYPES = ("message", "request", "notice")
_SEND_ACTIONS = {
    "send_private_msg",
    "send_group_msg",
    "upload_private_file",
    "upload_group_file",
}


class OneBotWSServer:
    def __init__(
        self,
        *,
        host: str,
        port: int,
        path: str,
        access_token: str | None,
        on_message: MessageHandler | None = None,
        on_request: EventHandler | None = None,
        on_notice: EventHandler | None = None,
        pipeline: QQEventPipeline | None = None,
        on_lifecycle: Callable[[str, dict[str, Any]], Awaitable[None]] | None = None,
        archive: ArchiveFunc | None = None,
        replay_grace_seconds: float = 5.0,
        suppress_sends_when_offline: bool = True,
    ) -> None:
        self.host = host
        self.port = port
        self.path = path
        self.access_token = access_token
        # The shared processing chain (T4 seam). Pass a prebuilt pipeline (e.g. from
        # QQBridgeApp), or the individual handlers and one is assembled here — either
        # way _dispatch runs the exact chain the simulation harness runs.
        if pipeline is None:
            pipeline = QQEventPipeline(
                on_message=on_message, on_request=on_request, on_notice=on_notice
            )
        self.pipeline = pipeline
        self.on_lifecycle = on_lifecycle
        self.archive = archive
        self.replay_grace_seconds = max(0.0, float(replay_grace_seconds))
        self.suppress_sends_when_offline = bool(suppress_sends_when_offline)
        self._echo_seq = 0
        self._connections = 0
        self._account_online: bool | None = None  # last heartbeat status.online
        # Message handlers run as concurrent tasks (one slow run must not block intake);
        # keep refs so they aren't GC'd, and serialize ws writes across those tasks.
        self._handler_tasks: set[asyncio.Task] = set()
        self._lifecycle_tasks: set[asyncio.Task] = set()
        self._send_lock = asyncio.Lock()

    @staticmethod
    def _payload_time(payload: dict[str, Any]) -> float | None:
        value = payload.get("time")
        if value is None:
            return None
        try:
            ts = float(value)
        except (TypeError, ValueError):
            return None
        if ts > 10_000_000_000:
            ts /= 1000.0
        return ts

    def _is_replayed_message(
        self, payload: dict[str, Any], *, connection_started_at: float | None
    ) -> bool:
        """Drop NapCat replay/backlog messages after a fresh reverse-WS connect.

        Raw archive has already run before this check. This only prevents replies/model
        runs for message events whose original OneBot timestamp predates this WS
        connection, which is what NapCat emits after QR re-login while the bridge was
        offline.
        """
        if not self.replay_grace_seconds or connection_started_at is None:
            return False
        event_ts = self._payload_time(payload)
        if event_ts is None:
            return False
        if event_ts < connection_started_at - self.replay_grace_seconds:
            log.info(
                "drop replayed qq message: message_id=%s event_time=%.3f "
                "connection_started_at=%.3f grace=%.1fs",
                payload.get("message_id", ""),
                event_ts,
                connection_started_at,
                self.replay_grace_seconds,
            )
            return True
        return False

    def _emit_lifecycle(self, event: str, detail: dict[str, Any] | None = None) -> None:
        """Fire a connection/account lifecycle alert without blocking the conn loop."""
        if self.on_lifecycle is None:
            return

        async def run() -> None:
            try:
                await self.on_lifecycle(event, detail or {})
            except Exception:  # noqa: BLE001
                log.exception("lifecycle handler crashed (%s)", event)

        task = asyncio.create_task(run())
        self._lifecycle_tasks.add(task)
        task.add_done_callback(self._lifecycle_tasks.discard)

    # -- action sending ----------------------------------------------------
    async def send_action(
        self, ws: web.WebSocketResponse, action: str, params: dict[str, Any]
    ) -> str:
        """Send a OneBot action frame (fire-and-forget) and return its echo id."""
        self._echo_seq += 1
        echo = f"qqbr-{self._echo_seq}"
        frame = {"action": action, "params": params, "echo": echo}
        if (
            self.suppress_sends_when_offline
            and self._account_online is False
            and action in _SEND_ACTIONS
        ):
            log.warning("drop outbound %s while qq account is offline", action)
            return echo
        async with self._send_lock:  # concurrent handler tasks share one ws writer
            await ws.send_str(json.dumps(frame, ensure_ascii=False))
        # A2 出站捕获(design chat-archive-storage:全量保存可关联的出站动作):
        # 发出后落 raw 证据层,post_type=rtime_outbound 与入站区分;best-effort。
        if self.archive is not None:
            self.archive(
                {
                    "post_type": "rtime_outbound",
                    "action": action,
                    "params": params,
                    "echo": echo,
                    "sent_at": time.time(),
                }
            )
        return echo

    # -- connection handling ----------------------------------------------
    def _authorized(self, request: web.Request) -> bool:
        if not self.access_token:
            return True
        return request.headers.get("Authorization", "") == f"Bearer {self.access_token}"

    async def _ws_handler(self, request: web.Request) -> web.WebSocketResponse:
        if not self._authorized(request):
            raise web.HTTPUnauthorized()
        ws = web.WebSocketResponse(heartbeat=30)
        await ws.prepare(request)
        self._connections += 1
        connection_started_at = time.time()
        self._emit_lifecycle("connect")
        try:
            async for msg in ws:
                if msg.type is not WSMsgType.TEXT:
                    continue
                try:
                    payload = json.loads(msg.data)
                except (ValueError, TypeError):
                    continue
                if isinstance(payload, dict):
                    await self._dispatch(
                        ws, payload, connection_started_at=connection_started_at
                    )
        finally:
            self._connections -= 1
            self._emit_lifecycle("disconnect")
        return ws

    async def _dispatch(
        self,
        ws: web.WebSocketResponse,
        payload: dict[str, Any],
        *,
        connection_started_at: float | None = None,
    ) -> None:
        post_type = payload.get("post_type")
        if self.archive and post_type in _ARCHIVED_POST_TYPES:
            self.archive(payload)

        async def emit(outbound: OutboundAction) -> None:
            await self.send_action(ws, outbound.action, outbound.params)

        if post_type == "message":
            if self._is_replayed_message(
                payload, connection_started_at=connection_started_at
            ):
                return
            # Run as a task so a slow model run doesn't block reading the next frame;
            # per-chat ordering is enforced downstream by the handler's per-chat lock.
            task = asyncio.create_task(self._run_message(payload, emit))
            self._handler_tasks.add(task)
            task.add_done_callback(self._handler_tasks.discard)
        elif post_type in ("request", "notice"):
            await self.pipeline.process_event(payload, emit=emit)
        elif post_type == "meta_event":
            self._on_meta_event(payload)
        # action responses (have no post_type): nothing to do.

    def _on_meta_event(self, payload: dict[str, Any]) -> None:
        """Track account online/offline from heartbeat status (风控 踢下线 detection)."""
        if payload.get("meta_event_type") != "heartbeat":
            return
        status = payload.get("status") or {}
        online = status.get("online")
        if online is None:
            return
        online = bool(online)
        if self._account_online is None:  # first heartbeat: record, don't alert
            self._account_online = online
            return
        if online != self._account_online:
            self._account_online = online
            self._emit_lifecycle("online" if online else "offline", {"status": status})

    async def _run_message(self, payload, emit) -> None:
        """Crash-safe wrapper for a spawned message-processing task — a handler
        exception must never bubble into the connection loop and drop the ws."""
        try:
            await self.pipeline.process_event(payload, emit=emit)
        except Exception:
            log.exception("qq message handler crashed")

    async def _health(self, request: web.Request) -> web.Response:
        return web.json_response(
            {"status": "ok", "onebot_connections": self._connections}
        )

    # -- lifecycle ---------------------------------------------------------
    def build_app(self) -> web.Application:
        app = web.Application()
        app.router.add_get(self.path, self._ws_handler)
        app.router.add_get("/healthz", self._health)
        return app

    async def serve_forever(self) -> None:
        runner = web.AppRunner(self.build_app())
        await runner.setup()
        site = web.TCPSite(runner, self.host, self.port)
        await site.start()
        try:
            await asyncio.Future()  # run until cancelled
        finally:
            await runner.cleanup()


__all__ = [
    "OneBotWSServer",
    "MessageHandler",
    "EventHandler",
    "ReplyFunc",
    "ActionFunc",
    "ArchiveFunc",
]
