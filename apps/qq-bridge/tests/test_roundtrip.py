# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Isolation verification: full reverse-WS round-trip without NapCat or an account.

Stands up the real ``OneBotWSServer`` in-process (aiohttp), connects a *fake NapCat*
client, and asserts:
  1. an owner private message is echoed back as a ``send_private_msg`` action;
  2. a non-owner message produces NO downstream action (the real
     ``is_allowed_actor`` gate rejects it silently);
  3. ``/healthz`` answers 200.

Async is driven with ``asyncio.run`` so the test needs no pytest-asyncio plugin.
"""

import asyncio
import json

import aiohttp
import pytest
from aiohttp import web
from qq_bridge.app import build_echo_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.ws_server import OneBotWSServer

OWNER = "10001"
STRANGER = "20002"


def _private_event(user_id: str, text: str) -> str:
    return json.dumps(
        {
            "post_type": "message",
            "message_type": "private",
            "sub_type": "friend",
            "user_id": int(user_id),
            "self_id": 99999,
            "message_id": 555,
            "message": text,
            "raw_message": text,
            "sender": {"user_id": int(user_id)},
        }
    )


def _lifecycle() -> str:
    return json.dumps(
        {
            "post_type": "meta_event",
            "meta_event_type": "lifecycle",
            "sub_type": "connect",
            "self_id": 99999,
        }
    )


async def _serve_and(client_coro, handler=None):
    """Run the bridge on an ephemeral port and invoke ``client_coro(base_url, path)``."""
    config = QQBridgeConfig(
        owner_ids=frozenset({OWNER}), ws_host="127.0.0.1", ws_port=0
    )
    server = OneBotWSServer(
        host="127.0.0.1",
        port=0,
        path=config.ws_path,
        access_token=None,
        on_message=handler or build_echo_handler(config),
    )
    runner = web.AppRunner(server.build_app())
    await runner.setup()
    site = web.TCPSite(runner, "127.0.0.1", 0)
    await site.start()
    try:
        host, port = runner.addresses[0][:2]
        return await client_coro(f"http://{host}:{port}", config.ws_path)
    finally:
        await runner.cleanup()


def test_owner_private_message_is_echoed():
    async def client(base_url, path):
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(base_url + path) as ws:
                await ws.send_str(_lifecycle())  # ignored by the bridge
                await ws.send_str(_private_event(OWNER, "复习配分函数"))
                msg = await asyncio.wait_for(ws.receive(), timeout=5)
                return json.loads(msg.data)

    reply = asyncio.run(_serve_and(client))
    assert reply["action"] == "send_private_msg"
    assert reply["params"]["user_id"] == int(OWNER)
    assert reply["params"]["message"] == "复习配分函数"
    assert reply["echo"]


def test_non_owner_message_is_silently_rejected():
    async def client(base_url, path):
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(base_url + path) as ws:
                await ws.send_str(_private_event(STRANGER, "let me in"))
                with pytest.raises(asyncio.TimeoutError):
                    await asyncio.wait_for(ws.receive(), timeout=1.0)
        return "rejected"

    assert asyncio.run(_serve_and(client)) == "rejected"


def test_crashing_handler_does_not_drop_connection():
    """A handler exception (it now runs as a spawned task) must not kill the ws / server."""

    async def boom(msg, reply, send_action=None):
        raise RuntimeError("boom")

    async def client(base_url, path):
        async with aiohttp.ClientSession() as sess:
            async with sess.ws_connect(base_url + path) as ws:
                await ws.send_str(_private_event(OWNER, "trigger crash"))
                await asyncio.sleep(0.2)  # let the spawned handler run + crash
            async with sess.get(base_url + "/healthz") as resp:  # server still alive
                return resp.status

    assert asyncio.run(_serve_and(client, handler=boom)) == 200


def test_healthz_returns_ok():
    async def client(base_url, path):
        async with aiohttp.ClientSession() as sess:
            async with sess.get(base_url + "/healthz") as resp:
                assert resp.status == 200
                return await resp.json()

    body = asyncio.run(_serve_and(client))
    assert body["status"] == "ok"


def test_send_action_suppresses_message_when_account_offline():
    class FakeWS:
        def __init__(self):
            self.sent = []

        async def send_str(self, data):
            self.sent.append(data)

    async def go():
        server = OneBotWSServer(
            host="127.0.0.1",
            port=0,
            path="/onebot",
            access_token=None,
            on_message=build_echo_handler(
                QQBridgeConfig(owner_ids=frozenset({OWNER}))
            ),
        )
        server._account_online = False
        ws = FakeWS()
        echo = await server.send_action(ws, "send_private_msg", {"message": "old"})
        return echo, ws.sent

    echo, sent = asyncio.run(go())
    assert echo == "qqbr-1"
    assert sent == []
