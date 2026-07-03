# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import asyncio
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bridge_runner import run_and_display
from latency_trace import mark, start_trace


class Store:
    async def on_claude_response(self, *args, **kwargs):
        return None

    async def set_permission_mode(self, *args, **kwargs):
        return None


class ActiveRuns:
    def start_run(self, user_id, card_msg_id):
        return SimpleNamespace(stop_requested=False)

    def attach_process(self, user_id, proc):
        return None

    def clear_run(self, user_id, active_run):
        return None


@pytest.mark.asyncio
async def test_latency_trace_marks_safe_fields(tmp_path, monkeypatch):
    log_path = tmp_path / "run-log.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(log_path))
    trace = start_trace(
        user_id="ou_secret_user",
        chat_id="oc_secret_chat",
        is_group=False,
        message_type="text",
        chat_type="p2p",
    )

    mark(trace, "webhook_received", message_chars=12)

    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["event"] == "feishu_latency_trace"
    assert record["stage"] == "webhook_received"
    assert record["actor_hash"].startswith("sha256:")
    assert "ou_secret_user" not in json.dumps(record)


@pytest.mark.asyncio
async def test_slow_model_sends_status_heartbeat_before_done(tmp_path, monkeypatch):
    log_path = tmp_path / "run-log.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(log_path))
    updates = []
    posts = []
    texts = []
    feishu = SimpleNamespace(
        update_card=AsyncMock(side_effect=lambda _mid, content: updates.append(content)),
        send_markdown_to_user=AsyncMock(side_effect=lambda _uid, text: posts.append(text) or "markdown-id"),
        reply_markdown=AsyncMock(),
        send_post_to_user=AsyncMock(),
        reply_post=AsyncMock(),
        send_text_to_user=AsyncMock(side_effect=lambda _uid, text: texts.append(text) or "text-id"),
        reply_text=AsyncMock(),
        update_card_with_buttons=AsyncMock(),
    )
    session = SimpleNamespace(
        session_id=None,
        model="claude-test",
        cwd="/tmp",
        permission_mode="bypassPermissions",
    )
    trace = start_trace(
        user_id="user-1",
        chat_id="chat-1",
        is_group=False,
        message_type="text",
        chat_type="p2p",
    )

    async def slow_model(**kwargs):
        kwargs["on_process_start"](SimpleNamespace())
        await asyncio.sleep(0.03)
        await kwargs["on_text_chunk"]("done")
        return "done", "sid-new", False

    await run_and_display(
        user_id="user-1",
        chat_id="chat-1",
        is_group=False,
        text="hello",
        card_msg_id="card-1",
        session=session,
        notify_msg_id="msg-1",
        feishu=feishu,
        store=Store(),
        active_runs=ActiveRuns(),
        run_claude_func=slow_model,
        stream_chunk_size=20,
        segmented_output=True,
        show_tool_calls=False,
        latency_trace=trace,
        status_heartbeat_seconds=0.01,
    )

    assert any("模型仍在处理中" in item for item in updates)
    assert posts == ["done"]
    assert texts == []
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    stages = [record.get("stage") for record in records if record.get("event") == "feishu_latency_trace"]
    assert "status_heartbeat" in stages
    assert "first_stdout" in stages
    assert "done" in stages


@pytest.mark.asyncio
async def test_segmented_output_sends_file_attachment_directive(tmp_path, monkeypatch):
    log_path = tmp_path / "run-log.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(log_path))
    artifact = tmp_path / "report.txt"
    artifact.write_text("artifact", encoding="utf-8")
    posts = []
    texts = []
    feishu = SimpleNamespace(
        update_card=AsyncMock(),
        send_markdown_to_user=AsyncMock(side_effect=lambda _uid, text: posts.append(text) or "markdown-id"),
        reply_markdown=AsyncMock(),
        send_post_to_user=AsyncMock(),
        reply_post=AsyncMock(),
        send_text_to_user=AsyncMock(side_effect=lambda _uid, text: texts.append(text) or "text-id"),
        reply_text=AsyncMock(),
        update_card_with_buttons=AsyncMock(),
        send_file_to_user=AsyncMock(return_value="file-id"),
        reply_file=AsyncMock(),
        send_image_to_user=AsyncMock(),
        reply_image=AsyncMock(),
    )
    session = SimpleNamespace(
        session_id=None,
        model="claude-test",
        cwd=str(tmp_path),
        permission_mode="bypassPermissions",
    )

    async def model_with_file(**kwargs):
        kwargs["on_process_start"](SimpleNamespace())
        return f"文件好了\n[[rtime-send-file:{artifact}]]", "sid-new", False

    await run_and_display(
        user_id="user-1",
        chat_id="chat-1",
        is_group=False,
        text="生成文件发给我",
        card_msg_id="card-1",
        session=session,
        notify_msg_id="msg-1",
        feishu=feishu,
        store=Store(),
        active_runs=ActiveRuns(),
        run_claude_func=model_with_file,
        stream_chunk_size=20,
        segmented_output=True,
        show_tool_calls=False,
    )

    assert posts == ["文件好了"]
    assert texts == []
    feishu.send_file_to_user.assert_awaited_once_with("user-1", str(artifact))
    feishu.send_image_to_user.assert_not_awaited()


@pytest.mark.asyncio
async def test_segmented_output_rejects_sensitive_attachment_directive(tmp_path, monkeypatch):
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(tmp_path / "run-log.jsonl"))
    artifact = tmp_path / "secret-token.txt"
    artifact.write_text("artifact", encoding="utf-8")
    posts = []
    texts = []
    feishu = SimpleNamespace(
        update_card=AsyncMock(),
        send_markdown_to_user=AsyncMock(side_effect=lambda _uid, text: posts.append(text) or "markdown-id"),
        reply_markdown=AsyncMock(),
        send_post_to_user=AsyncMock(),
        reply_post=AsyncMock(),
        send_text_to_user=AsyncMock(side_effect=lambda _uid, text: texts.append(text) or "text-id"),
        reply_text=AsyncMock(),
        update_card_with_buttons=AsyncMock(),
        send_file_to_user=AsyncMock(return_value="file-id"),
        reply_file=AsyncMock(),
        send_image_to_user=AsyncMock(),
        reply_image=AsyncMock(),
    )
    session = SimpleNamespace(
        session_id=None,
        model="claude-test",
        cwd=str(tmp_path),
        permission_mode="bypassPermissions",
    )

    async def model_with_secret(**kwargs):
        kwargs["on_process_start"](SimpleNamespace())
        return f"看这个\n[[rtime-send-file:{artifact}]]", "sid-new", False

    await run_and_display(
        user_id="user-1",
        chat_id="chat-1",
        is_group=False,
        text="发给我",
        card_msg_id="card-1",
        session=session,
        notify_msg_id="msg-1",
        feishu=feishu,
        store=Store(),
        active_runs=ActiveRuns(),
        run_claude_func=model_with_secret,
        stream_chunk_size=20,
        segmented_output=True,
        show_tool_calls=False,
    )

    assert posts == ["看这个"]
    assert any("附件未发送" in text for text in texts)
    feishu.send_file_to_user.assert_not_awaited()
