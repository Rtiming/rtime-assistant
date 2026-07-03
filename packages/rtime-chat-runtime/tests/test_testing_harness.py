# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Unit tests for the simulation-harness helpers (FakeModelRunner + synth).

These lock the fakes' own contract so the bridge sim tests can trust them:
the runner must mirror ``run_claude``'s signature/return shape and record every
parameter; the synth constructors must produce events the REAL decoders accept.
"""

from __future__ import annotations

import asyncio

from rtime_chat_runtime.testing import (
    FakeModelRunner,
    ScriptedReply,
    make_feishu_msg,
    make_qq_group_at,
    make_qq_private,
)


def _run(coro):
    return asyncio.run(coro)


# --- FakeModelRunner -----------------------------------------------------
def test_fake_runner_returns_run_claude_shape():
    runner = FakeModelRunner("答案")
    full_text, sid, used_fresh = _run(runner("问题", cli="/x/claude"))
    assert (full_text, used_fresh) == ("答案", False)
    assert sid == "sess-fake"


def test_fake_runner_records_all_params():
    runner = FakeModelRunner("ok")
    _run(
        runner(
            "prompt-body",
            cli="/x/claude",
            permission_mode="dontAsk",
            session_id="s0",
            model="ds",
            cwd="/home/x",
            system_prompt="SYS",
            mcp_config='{"mcpServers":{}}',
            allowed_tools=["Read", "Grep"],
            disallowed_tools=["Write"],
            max_seconds=600.0,
        )
    )
    call = runner.last
    assert call.prompt == "prompt-body"
    assert call.permission_mode == "dontAsk"
    assert call.model == "ds"
    assert call.system_prompt == "SYS"
    assert call.mcp_config == '{"mcpServers":{}}'
    assert call.allowed_tools == ["Read", "Grep"]
    assert call.disallowed_tools == ["Write"]
    assert call.max_seconds == 600.0
    assert call.streaming is False


def test_fake_runner_streams_chunks_and_tool_calls():
    chunks: list[str] = []
    tools: list[tuple[str, dict]] = []

    async def on_chunk(c):
        chunks.append(c)

    async def on_tool(name, inp):
        tools.append((name, inp))

    reply = ScriptedReply(
        text="第一段\n\n第二段",
        chunks=("第一段\n\n", "第二段"),
        tool_calls=(("mcp__rtime-library-gateway__lib_search", {"q": "热统"}),),
    )
    runner = FakeModelRunner(reply)
    _run(runner("q", cli="/x", on_text_chunk=on_chunk, on_tool_use=on_tool))
    assert chunks == ["第一段\n\n", "第二段"]
    assert tools == [("mcp__rtime-library-gateway__lib_search", {"q": "热统"})]
    assert runner.last.streaming is True


def test_fake_runner_scripts_consecutive_turns_then_repeats_last():
    runner = FakeModelRunner("一", "二")
    assert _run(runner("a", cli="/x"))[0] == "一"
    assert _run(runner("b", cli="/x"))[0] == "二"
    assert _run(runner("c", cli="/x"))[0] == "二"  # last repeats
    assert len(runner.calls) == 3


# --- synth: QQ wire shapes (decode-through-parser lives in qq-bridge tests) ---
def test_make_qq_private_wire_shape():
    ev = make_qq_private("10001", "你好")
    assert ev["post_type"] == "message"
    assert ev["message_type"] == "private"
    assert ev["user_id"] == 10001  # ints on the wire; parser normalizes to str
    assert "group_id" not in ev
    assert ev["message"] == [{"type": "text", "data": {"text": "你好"}}]
    assert ev["raw_message"] == "你好"


def test_make_qq_group_at_bot_wire_shape():
    ev = make_qq_group_at("600", "222", "东区班车几点", at_bot=True, self_id="479")
    assert ev["message_type"] == "group"
    assert ev["group_id"] == 600
    seg_types = [s["type"] for s in ev["message"]]
    assert seg_types == ["at", "text"]
    assert ev["message"][0]["data"]["qq"] == "479"  # @bot targets self_id


def test_make_qq_group_without_at_has_no_at_segment():
    ev = make_qq_group_at("600", "222", "闲聊", at_bot=False)
    assert [s["type"] for s in ev["message"]] == ["text"]


def test_make_qq_group_at_someone_else_targets_them():
    ev = make_qq_group_at("600", "222", "hi", at_qq="888", self_id="479")
    assert ev["message"][0]["data"]["qq"] == "888"


def test_make_qq_private_extra_segments_appended():
    ev = make_qq_private(
        "1",
        "看图",
        extra_segments=[{"type": "image", "data": {"url": "http://x/a.png"}}],
    )
    assert [s["type"] for s in ev["message"]] == ["text", "image"]


# --- synth: Feishu shape matches the bridge's extractor ------------------
def test_make_feishu_msg_shape():
    import json

    ev = make_feishu_msg("你好", user_id="ou_x", is_group=False)
    assert ev.event.sender.sender_id.open_id == "ou_x"
    assert ev.event.message.chat_type == "p2p"
    assert json.loads(ev.event.message.content)["text"] == "你好"
    assert ev.event.message.mentions == []


def test_make_feishu_group_msg_with_mentions():
    ev = make_feishu_msg("hi", is_group=True, mention_keys=["@_user_1"])
    assert ev.event.message.chat_type == "group"
    assert ev.event.message.chat_id.startswith("oc_")
    assert [m.key for m in ev.event.message.mentions] == ["@_user_1"]
