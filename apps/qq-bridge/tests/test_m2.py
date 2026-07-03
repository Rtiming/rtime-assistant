# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M2 model pipeline: output splitting, session store, tool policy, model handler."""

import asyncio

import qq_bridge.app as app_mod
from qq_bridge.app import build_model_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.protocol import IncomingMessage
from qq_bridge.output_qq import split_for_qq, strip_markdown_for_qq
from qq_bridge.sessions import SessionStore
from qq_bridge.tool_policy import (
    add_runtime_policy_hints,
    allowed_tools_for_text,
    disallowed_tools_for_text,
)


def _run(coro):
    return asyncio.run(coro)


def _replies():
    out: list[str] = []

    async def reply(t):
        out.append(t)

    return out, reply


def _msg(user_id="111", text="讲讲热统配分函数"):
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


# --- output_qq ---
def test_split_short_and_empty():
    assert split_for_qq("hi") == ["hi"]
    assert split_for_qq("") == []


def test_split_long_preserves_content():
    chunks = split_for_qq("a" * 6000, max_chars=2500)
    assert chunks and all(len(c) <= 2500 for c in chunks)
    assert "".join(chunks) == "a" * 6000


def test_strip_markdown_downgrades_to_plain_text():
    # 虚构人物+假邮箱:测试夹具不得指向真实个人
    out = strip_markdown_for_qq(
        "## 核学院\n"
        "- **教学秘书**：王示例\n"
        "- 邮箱：`office-test@example.edu`\n"
        "> 来源：[教务处](https://teach.ustc.edu.cn/x)\n"
        "---\n"
        "* 备注：先电话确认"
    )
    assert "**" not in out and "##" not in out and "`" not in out
    assert "> " not in out  # blockquote marker removed
    assert "教学秘书：王示例" in out
    assert "office-test@example.edu" in out
    assert "教务处（https://teach.ustc.edu.cn/x）" in out  # link -> label（url）
    assert "- 备注：先电话确认" in out  # '*' bullet normalized to '-'


def test_strip_markdown_preserves_plain_and_paths():
    # snake_case paths, single '*', bare urls and emoji must survive untouched
    src = "见 knowledge/institutions/ustc/a_b_c.md ，2*3=6，电话 63603982 📞"
    assert strip_markdown_for_qq(src) == src


# --- sessions ---
def test_session_roundtrip_and_persistence(tmp_path):
    s = SessionStore(str(tmp_path), default_model="kimi")
    assert s.get("u", "c").session_id is None
    s.on_response("u", "c", "sess-1")
    assert s.get("u", "c").session_id == "sess-1"
    s.set_model("u", "c", "opus")
    assert s.get("u", "c").model == "opus"
    s.reset("u", "c")
    assert s.get("u", "c").session_id is None
    assert SessionStore(str(tmp_path)).get("u", "c").model == "opus"  # persisted


# --- tool policy ---
def test_allowed_none_for_plain_text():
    assert (
        allowed_tools_for_text("讲讲热统配分函数") is None
    )  # all tools incl brain MCP


def test_allowed_web_for_web_intent():
    assert "WebSearch" in (allowed_tools_for_text("帮我上网搜索一下") or [])


def test_disallowed_blocks_cron():
    assert "CronCreate" in disallowed_tools_for_text("anything")


def test_hints_skip_slash_commands():
    assert add_runtime_policy_hints("/model opus") == "/model opus"


def test_hints_added_for_text():
    assert add_runtime_policy_hints("hi") != "hi"


# --- model handler (monkeypatched runner) ---
def test_handler_rejects_non_owner(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )

    async def fake_run(*a, **k):
        return ("ANSWER", "sess-9", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(user_id="999"), reply))
    assert out == []  # stranger gets nothing


def test_handler_answers_owner_and_strips_directive(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )

    async def fake_run(prompt, **k):
        return ("热统答案\n[[rtime-send-image:/x.png]]", "sess-9", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(), reply))
    assert any("热统答案" in o for o in out)
    assert all(
        "rtime-send-image" not in o for o in out
    )  # directive stripped (M3 sends it)
    assert (
        SessionStore(str(tmp_path)).get("111", "111").session_id == "sess-9"
    )  # session saved


def test_handler_new_command(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text="/new"), reply))
    assert any("新对话" in o for o in out)


def test_handler_empty_text_acks(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text=""), reply))
    assert (
        out and "空消息" in out[0]
    )  # truly-empty message (no text, no media) -> acked


def test_handler_streams_segments_and_tool_status(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=True,
        show_tool_calls=True,
    )

    async def fake_run(prompt, *, on_text_chunk=None, on_tool_use=None, **k):
        if on_tool_use:
            await on_tool_use("mcp__rtime-library-gateway__lib_search", {"q": "热统"})
        if on_text_chunk:
            await on_text_chunk("第一段答案。\n\n")
            await on_text_chunk("第二段答案。")
        return ("第一段答案。\n\n第二段答案。", "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text="讲讲热统"), reply))
    assert any("思考中" in o for o in out)  # immediate ack
    assert any("brain" in o for o in out)  # tool status line
    assert any("第一段答案" in o for o in out)  # streamed segment 1
    assert any("第二段答案" in o for o in out)  # streamed tail


def test_handler_non_streaming_sends_once(monkeypatch, tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path),
        stream_output=False,
    )

    async def fake_run(prompt, *, on_text_chunk=None, on_tool_use=None, **k):
        assert on_text_chunk is None and on_tool_use is None  # off -> no callbacks
        return ("最终答案", "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text="问题"), reply))
    assert out == ["最终答案"]  # no ack, single message


def test_stream_command_toggles_per_chat(tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text="/stream off"), reply))
    assert any("关闭" in o for o in out)
    assert SessionStore(str(tmp_path)).get("111", "111").stream is False


def test_handler_empty_owner_rejects_everyone(tmp_path):
    # owner-only hard gate: no owner configured => reject all (never allow-all on QQ)
    cfg = QQBridgeConfig(
        owner_ids=frozenset(), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(user_id="2000000001"), reply))  # 任意陌生号(假)
    assert out == []


def test_model_command_resolves_alias(tmp_path):
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), claude_cli="/x/claude", sessions_dir=str(tmp_path)
    )
    out, reply = _replies()
    _run(build_model_handler(cfg)(_msg(text="/model opus"), reply))
    stored = SessionStore(str(tmp_path)).get("111", "111").model
    assert stored.startswith("claude-opus")  # resolved via rtime-models, not raw "opus"
