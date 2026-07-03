# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""风控/掉线 (D2): heartbeat online/offline detection + the lifecycle alerter."""

import asyncio
from types import SimpleNamespace

import qq_bridge.alerts as alerts_mod
from qq_bridge.alerts import build_lifecycle_alerter
from qq_bridge.onebot.ws_server import OneBotWSServer


async def _noop_handler(*args, **kwargs):
    pass


def _heartbeat(online: bool) -> dict:
    return {
        "post_type": "meta_event",
        "meta_event_type": "heartbeat",
        "status": {"online": online, "good": online},
    }


def test_heartbeat_transitions_emit_offline_then_online():
    events: list[str] = []

    async def capture(event, detail):
        events.append(event)

    async def run():
        srv = OneBotWSServer(
            host="127.0.0.1",
            port=0,
            path="/onebot",
            access_token=None,
            on_message=_noop_handler,
            on_lifecycle=capture,
        )
        srv._on_meta_event(_heartbeat(True))  # first heartbeat: record, no alert
        srv._on_meta_event(_heartbeat(False))  # online -> offline => alert
        srv._on_meta_event(_heartbeat(False))  # no change => no alert
        srv._on_meta_event(_heartbeat(True))  # offline -> online => alert
        await asyncio.sleep(0.05)  # let the spawned lifecycle tasks run

    asyncio.run(run())
    assert events == ["offline", "online"]


def test_alerter_logs_without_webhook(monkeypatch):
    logged: list[tuple] = []

    # Mirror the real append_run_event signature (first positional param is named
    # ``event``) so a positional/kwarg collision would surface here, not only at runtime.
    def fake_append(event, **fields):
        logged.append((event, fields.get("lifecycle")))
        return True

    monkeypatch.setattr(alerts_mod, "append_run_event", fake_append)
    cfg = SimpleNamespace(alert_webhook="")
    alerter = build_lifecycle_alerter(cfg)
    asyncio.run(alerter("offline", {}))
    assert ("qq_lifecycle", "offline") in logged
