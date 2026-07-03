# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""POST /api/chat end-to-end over a live server with a FAKE runner.

Covers the SSE frame sequence, session continuity (--resume semantics), the
per-profile read-only hard door, actor threading and run-log accounting.
"""

from __future__ import annotations

import json

from conftest import make_config, post_chat, read_run_log
from rtime_chat_runtime.tool_policy import READONLY_ALLOWED, READONLY_PERMISSION_MODE


def make_fake_run(answer="你好，答案。", new_sid="cli-sess-1", chunks=None, calls=None):
    """A run_claude stand-in that streams ``chunks`` then returns ``answer``."""

    async def fake_run(prompt, **kwargs):
        if calls is not None:
            calls.append({"prompt": prompt, **kwargs})
        on_chunk = kwargs.get("on_text_chunk")
        for chunk in chunks if chunks is not None else [answer]:
            if on_chunk:
                on_chunk(chunk)
        return answer, new_sid, False

    return fake_run


# --- frame sequence ----------------------------------------------------------
def test_stream_frame_sequence(live_server):
    base = live_server(fake_run=make_fake_run(answer="一二三", chunks=["一", "二", "三"]))
    status, events = post_chat(base, {"profile": "owner", "message": "问题"})
    assert status == 200
    types = [ev["type"] for ev in events]
    assert types[0] == "status"
    assert types[-1] == "done"
    deltas = [ev["text"] for ev in events if ev["type"] == "delta"]
    assert deltas == ["一", "二", "三"]
    done = events[-1]
    assert done["answer"] == "一二三"
    assert "".join(deltas) == done["answer"]
    assert done["profile"] == "owner"
    assert "error" not in types


def test_done_generates_session_id_when_absent(live_server):
    base = live_server(fake_run=make_fake_run())
    _, events = post_chat(base, {"profile": "owner", "message": "hi"})
    done = events[-1]
    assert done["type"] == "done"
    assert isinstance(done["session_id"], str) and len(done["session_id"]) == 32


def test_done_echoes_client_session_id(live_server):
    base = live_server(fake_run=make_fake_run())
    _, events = post_chat(
        base, {"profile": "owner", "message": "hi", "session_id": "conv-42"}
    )
    assert events[-1]["session_id"] == "conv-42"


def test_default_profile_when_omitted(live_server):
    base = live_server(fake_run=make_fake_run())
    _, events = post_chat(base, {"message": "hi"})
    assert events[-1]["profile"] == "owner"  # first configured profile is default


def test_error_frame_on_runner_failure(live_server):
    async def boom(prompt, **kwargs):
        raise RuntimeError("模型执行超时")

    base = live_server(fake_run=boom)
    status, events = post_chat(base, {"profile": "owner", "message": "hi"})
    assert status == 200  # SSE already started; the error rides the stream
    types = [ev["type"] for ev in events]
    assert "error" in types and "done" not in types
    error = next(ev for ev in events if ev["type"] == "error")
    assert "RuntimeError" in error["message"]


def test_tool_use_generic_status_once(live_server):
    async def fake_run(prompt, **kwargs):
        on_tool = kwargs.get("on_tool_use")
        on_tool("Read", {})
        on_tool("Grep", {"pattern": "x"})
        return "ok", None, False

    base = live_server(fake_run=fake_run)
    _, events = post_chat(base, {"profile": "owner", "message": "hi"})
    statuses = [ev["text"] for ev in events if ev["type"] == "status"]
    assert statuses.count("查阅中…") == 1  # generic, deduped, no tool names
    assert not any("Read" in s or "Grep" in s for s in statuses)


def test_tool_use_detailed_status_opt_in(live_server, tmp_path):
    async def fake_run(prompt, **kwargs):
        kwargs["on_tool_use"]("Read", {})
        return "ok", None, False

    cfg = make_config(tmp_path, show_tool_calls=True)
    base = live_server(cfg, fake_run=fake_run)
    _, events = post_chat(base, {"profile": "owner", "message": "hi"})
    statuses = [ev["text"] for ev in events if ev["type"] == "status"]
    assert any("Read" in s for s in statuses)


def test_fresh_session_fallback_flagged(live_server):
    async def fake_run(prompt, **kwargs):
        return "ok", "new-sid", True

    base = live_server(fake_run=fake_run)
    _, events = post_chat(base, {"profile": "owner", "message": "hi"})
    assert events[-1]["used_fresh_session_fallback"] is True


# --- session continuity ------------------------------------------------------
def test_second_turn_resumes_cli_session(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(new_sid="cli-abc", calls=calls))
    post_chat(base, {"profile": "owner", "message": "第一问", "session_id": "s1"})
    post_chat(base, {"profile": "owner", "message": "第二问", "session_id": "s1"})
    assert calls[0]["session_id"] is None  # fresh conversation
    assert calls[1]["session_id"] == "cli-abc"  # resumed from the stored sid


def test_sessions_isolated_by_session_id(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(new_sid="cli-abc", calls=calls))
    post_chat(base, {"profile": "owner", "message": "a", "session_id": "s1"})
    post_chat(base, {"profile": "owner", "message": "b", "session_id": "s2"})
    assert calls[1]["session_id"] is None  # different conversation, no resume


def test_sessions_isolated_by_profile(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(new_sid="cli-abc", calls=calls))
    post_chat(base, {"profile": "owner", "message": "a", "session_id": "s1"})
    post_chat(base, {"profile": "studentunion", "message": "b", "session_id": "s1"})
    # same conversation id but another profile must NOT resume owner's CLI session
    assert calls[1]["session_id"] is None


def test_session_store_keyed_by_anonymous_actor(live_server, tmp_path):
    cfg = make_config(tmp_path)
    base = live_server(cfg, fake_run=make_fake_run(new_sid="cli-abc"))
    post_chat(base, {"profile": "owner", "message": "a", "session_id": "s1"})
    stored = json.loads(
        (tmp_path / "state" / "sessions" / "sessions.json").read_text(encoding="utf-8")
    )
    assert list(stored) == ["web:anonymous:owner:s1"]
    assert stored["web:anonymous:owner:s1"]["session_id"] == "cli-abc"


# --- profile -> behavior (minimal T5a wiring) ---------------------------------
def test_read_only_profile_hard_door(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(calls=calls))
    post_chat(base, {"profile": "studentunion", "message": "东区办事流程？"})
    call = calls[0]
    assert call["permission_mode"] == READONLY_PERMISSION_MODE
    assert call["allowed_tools"] == list(READONLY_ALLOWED)
    for tool in ("Edit", "Write", "Task", "Agent"):
        assert tool in call["disallowed_tools"]


def test_owner_profile_keeps_default_mode(live_server, tmp_path):
    calls: list[dict] = []
    cfg = make_config(tmp_path, permission_mode="bypassPermissions")
    base = live_server(cfg, fake_run=make_fake_run(calls=calls))
    post_chat(base, {"profile": "owner", "message": "你好"})
    call = calls[0]
    assert call["permission_mode"] == "bypassPermissions"
    assert call["allowed_tools"] is None  # plain text => all tools
    assert "Task" in call["disallowed_tools"]  # web blocks subagents like QQ


def test_profile_selects_system_prompt(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(calls=calls))
    post_chat(base, {"profile": "studentunion", "message": "hi"})
    post_chat(base, {"profile": "owner", "message": "hi"})
    assert "学生会" in calls[0]["system_prompt"]
    assert "学生会" not in calls[1]["system_prompt"]


def test_runtime_hints_appended_to_prompt(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=make_fake_run(calls=calls))
    post_chat(base, {"profile": "owner", "message": "帮我搜索一下今天的新闻"})
    prompt = calls[0]["prompt"]
    assert prompt.startswith("帮我搜索一下今天的新闻")
    assert "运行环境提示" in prompt  # web-intent hint appended by the shared policy


def test_echo_mode_without_cli(live_server, tmp_path):
    cfg = make_config(tmp_path, claude_cli="")
    base = live_server(cfg)  # no fake needed: run_claude must not be called
    status, events = post_chat(base, {"profile": "owner", "message": "回声测试"})
    assert status == 200
    assert events[-1]["type"] == "done"
    assert events[-1]["answer"] == "(echo) 回声测试"


# --- run log ------------------------------------------------------------------
def test_run_log_started_and_completed(live_server, tmp_path):
    base = live_server(fake_run=make_fake_run(answer="答案正文"))
    post_chat(base, {"profile": "studentunion", "message": "问题", "session_id": "s9"})
    events = read_run_log(tmp_path)
    kinds = [ev["event"] for ev in events]
    assert kinds == ["run_started", "run_completed"]
    started, completed = events
    assert started["entry"] == "web" and completed["entry"] == "web"
    assert started["profile"] == "studentunion"
    assert started["read_only"] is True
    assert started["actor_hash"] == completed["actor_hash"]
    assert started["actor_hash"].startswith("sha256:")  # hashed, never the raw actor
    assert completed["reply_preview"] == "答案正文"
    assert completed["output_chars"] == 4


def test_run_log_failure(live_server, tmp_path):
    async def boom(prompt, **kwargs):
        raise ValueError("bad")

    base = live_server(fake_run=boom)
    post_chat(base, {"profile": "owner", "message": "问题"})
    events = read_run_log(tmp_path)
    kinds = [ev["event"] for ev in events]
    assert kinds == ["run_started", "run_failed"]
    assert events[1]["error_type"] == "ValueError"
