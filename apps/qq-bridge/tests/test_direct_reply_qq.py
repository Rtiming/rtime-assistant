# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""块5 正则直答 integration: a rule hit answers WITHOUT the model, a miss goes to
the model, the owner gate still applies, and the full fake-NapCat reverse-WS
round-trip delivers the direct answer. Bus parsing itself is covered by
packages/rtime-chat-runtime/tests/test_direct_reply.py (offline fixture)."""

import asyncio
import json
from pathlib import Path

import aiohttp
import qq_bridge.app as app_mod
import rtime_chat_runtime.direct_reply as direct_reply_mod
from aiohttp import web
from qq_bridge.app import build_model_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.protocol import IncomingMessage
from qq_bridge.onebot.ws_server import OneBotWSServer

OWNER = "111"
REPO_ROOT = Path(__file__).resolve().parents[3]
BUS_FIXTURE = (
    REPO_ROOT
    / "packages"
    / "rtime-chat-runtime"
    / "tests"
    / "fixtures"
    / "bus_timetable_dongqu_workday.html"
)


def _run(coro):
    return asyncio.run(coro)


def _replies():
    out: list[str] = []

    async def reply(t):
        out.append(t)

    return out, reply


def _msg(user_id=OWNER, text="在吗"):
    return IncomingMessage(
        self_id="479",
        message_type="private",
        user_id=user_id,
        group_id=None,
        chat_id=user_id,
        is_group=False,
        message_id="1",
        text=text,
    )


def _rules_file(tmp_path, rules=None) -> str:
    rules = (
        rules
        if rules is not None
        else [
            {
                "name": "faq-ping",
                "patterns": ["^在吗$"],
                "type": "text",
                "reply": "在的。",
            }
        ]
    )
    path = tmp_path / "direct-rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _cfg(tmp_path, rules_path):
    return QQBridgeConfig(
        owner_ids=frozenset({OWNER}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path / "sessions"),
        stream_output=False,
        direct_rules_path=rules_path,
    )


def _forbid_model(monkeypatch):
    async def explode(*a, **k):
        raise AssertionError("model must NOT be called on a direct-reply hit")

    monkeypatch.setattr(app_mod, "run_claude", explode)


# --- handler-level integration ---
def test_hit_answers_without_model(monkeypatch, tmp_path):
    _forbid_model(monkeypatch)
    handler = build_model_handler(_cfg(tmp_path, _rules_file(tmp_path)))
    out, reply = _replies()
    _run(handler(_msg(text="在吗"), reply))
    assert out == ["在的。"]


def test_miss_goes_to_model(monkeypatch, tmp_path):
    async def fake_run(*a, **k):
        return ("模型答案", "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    handler = build_model_handler(_cfg(tmp_path, _rules_file(tmp_path)))
    out, reply = _replies()
    _run(handler(_msg(text="讲讲热统配分函数"), reply))
    assert out == ["模型答案"]


def test_stranger_gets_no_direct_reply(monkeypatch, tmp_path):
    _forbid_model(monkeypatch)
    handler = build_model_handler(_cfg(tmp_path, _rules_file(tmp_path)))
    out, reply = _replies()
    _run(handler(_msg(user_id="999", text="在吗"), reply))
    assert out == []  # owner gate fires BEFORE direct reply


def test_no_rules_env_means_engine_off(monkeypatch, tmp_path):
    async def fake_run(*a, **k):
        return ("模型答案", "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    handler = build_model_handler(_cfg(tmp_path, ""))  # QQ_DIRECT_RULES unset
    out, reply = _replies()
    _run(handler(_msg(text="在吗"), reply))
    assert out == ["模型答案"]  # everything goes to the model


def test_bus_rule_hit_via_bridge(monkeypatch, tmp_path):
    _forbid_model(monkeypatch)
    html = BUS_FIXTURE.read_text(encoding="utf-8")
    # The engine binds the fetcher at build time — patch the module default first.
    monkeypatch.setattr(direct_reply_mod, "_fetch_html", lambda url: html)
    rules = [{"name": "campus-bus", "patterns": ["班车"], "type": "bus_timetable"}]
    handler = build_model_handler(_cfg(tmp_path, _rules_file(tmp_path, rules)))
    out, reply = _replies()
    _run(handler(_msg(text="班车时刻表"), reply))
    assert len(out) == 1
    assert "东区→南区" in out[0] and "7:30" in out[0]


def test_bus_fetch_failure_falls_back_to_model(monkeypatch, tmp_path):
    async def fake_run(*a, **k):
        return ("模型兜底答案", "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)

    def boom(url):
        raise OSError("network down")

    monkeypatch.setattr(direct_reply_mod, "_fetch_html", boom)
    rules = [{"name": "campus-bus", "patterns": ["班车"], "type": "bus_timetable"}]
    handler = build_model_handler(_cfg(tmp_path, _rules_file(tmp_path, rules)))
    out, reply = _replies()
    _run(handler(_msg(text="班车时刻表"), reply))
    assert out == ["模型兜底答案"]  # graceful fallback, no crash, no silence


def test_config_reads_qq_direct_rules_env(monkeypatch):
    monkeypatch.setenv("QQ_DIRECT_RULES", "/qq-state/direct-rules.json")
    assert QQBridgeConfig.from_env().direct_rules_path == "/qq-state/direct-rules.json"
    monkeypatch.delenv("QQ_DIRECT_RULES")
    assert QQBridgeConfig.from_env().direct_rules_path == ""


# --- full reverse-WS round-trip (fake NapCat, mirrors test_roundtrip.py) ---
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


def test_roundtrip_direct_reply_over_ws(monkeypatch, tmp_path):
    _forbid_model(monkeypatch)
    config = _cfg(tmp_path, _rules_file(tmp_path))

    async def scenario():
        server = OneBotWSServer(
            host="127.0.0.1",
            port=0,
            path=config.ws_path,
            access_token=None,
            on_message=build_model_handler(config),
        )
        runner = web.AppRunner(server.build_app())
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        try:
            host, port = runner.addresses[0][:2]
            async with aiohttp.ClientSession() as sess:
                async with sess.ws_connect(
                    f"http://{host}:{port}" + config.ws_path
                ) as ws:
                    await ws.send_str(_private_event(OWNER, "在吗"))
                    msg = await asyncio.wait_for(ws.receive(), timeout=5)
                    return json.loads(msg.data)
        finally:
            await runner.cleanup()

    action = asyncio.run(scenario())
    assert action["action"] == "send_private_msg"
    assert action["params"]["user_id"] == int(OWNER)
    assert action["params"]["message"] == "在的。"
