# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
集成测试：模拟完整的消息处理流程。
Mock 掉飞书 API 和 Claude CLI，验证从收到消息到发送回复的完整链路。
"""
import asyncio
import json
import os
import sys

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


# ── helpers ──────────────────────────────────────────────────

def _make_event(
    user_id: str = "user_001",
    chat_id: str = "user_001",
    chat_type: str = "p2p",
    text: str = "hello",
    message_id: str = "msg_001",
    mentions: list = None,
    message_type: str = "text",
    content: dict | None = None,
):
    """构造一个模拟的飞书消息事件"""
    event = MagicMock()
    event.event.sender.sender_id.open_id = user_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.message_type = message_type
    event.event.message.content = json.dumps(content if content is not None else {"text": text})
    event.event.message.message_id = message_id
    event.event.message.mentions = mentions
    return event


def _make_claude_output(text: str, session_id: str = "sid_abc123") -> list[bytes]:
    """构造 Claude CLI 的 stream-json 输出行"""
    lines = [
        json.dumps({"type": "system", "session_id": session_id}).encode() + b"\n",
    ]
    # 分成小块模拟流式
    chunk_size = 20
    for i in range(0, len(text), chunk_size):
        chunk = text[i:i + chunk_size]
        lines.append(json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": chunk},
            },
        }).encode() + b"\n")
    lines.append(json.dumps({
        "type": "result",
        "session_id": session_id,
        "result": text,
    }).encode() + b"\n")
    return lines


def _make_tool_use_output(
    tool_name: str,
    tool_input: dict,
    result_text: str,
    session_id: str = "sid_abc123",
) -> list[bytes]:
    """构造包含工具调用的 Claude CLI 输出"""
    lines = [
        json.dumps({"type": "system", "session_id": session_id}).encode() + b"\n",
        # tool_use start
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": tool_name},
            },
        }).encode() + b"\n",
        # tool input delta
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps(tool_input),
                },
            },
        }).encode() + b"\n",
        # tool_use stop
        json.dumps({
            "type": "stream_event",
            "event": {"type": "content_block_stop"},
        }).encode() + b"\n",
        # text output
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": result_text},
            },
        }).encode() + b"\n",
        # result
        json.dumps({
            "type": "result",
            "session_id": session_id,
            "result": result_text,
        }).encode() + b"\n",
    ]
    return lines


def _post_content(paragraphs: list[list[dict]]) -> dict:
    """Build a Feishu post payload with the common zh_cn wrapper."""
    return {
        "post": {
            "zh_cn": {
                "title": "",
                "content": paragraphs,
            }
        }
    }


class FakeProc:
    """模拟 asyncio.create_subprocess_exec 返回的进程"""
    def __init__(self, stdout_lines: list[bytes], returncode: int = 0):
        self.stdin = MagicMock()
        self.stdin.write = MagicMock()
        self.stdin.drain = AsyncMock()
        self.stdin.close = MagicMock()
        self._lines = list(stdout_lines)
        self._index = 0
        self.stderr = MagicMock()
        self.stderr.read = AsyncMock(return_value=b"")
        self.returncode = returncode

    @property
    def stdout(self):
        return self

    async def readline(self):
        if self._index >= len(self._lines):
            return b""
        line = self._lines[self._index]
        self._index += 1
        return line

    async def wait(self):
        return self.returncode

    def kill(self):
        pass


def _capture_rendered_messages(mock_feishu):
    """Capture normal rendered assistant body messages."""
    post_messages = []
    text_messages = []
    mock_feishu.send_markdown_to_user = AsyncMock(
        side_effect=lambda _open_id, text: post_messages.append(text) or "msg_post_001"
    )
    mock_feishu.reply_markdown = AsyncMock(
        side_effect=lambda _message_id, text: post_messages.append(text) or "msg_post_001"
    )
    mock_feishu.send_post_to_user = AsyncMock()
    mock_feishu.reply_post = AsyncMock()
    mock_feishu.send_text_to_user = AsyncMock(
        side_effect=lambda _open_id, text: text_messages.append(text) or "msg_text_001"
    )
    mock_feishu.reply_text = AsyncMock(
        side_effect=lambda _message_id, text: text_messages.append(text) or "msg_text_001"
    )
    mock_feishu.send_image_to_user = AsyncMock(return_value="msg_image_001")
    mock_feishu.reply_image = AsyncMock(return_value="msg_image_001")
    return post_messages, text_messages


# ── 测试：私聊完整流程 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_private_chat_full_flow():
    """私聊消息 → Claude 回复 → 卡片更新的完整流程"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(text="你好，帮我看看代码")
    claude_lines = _make_claude_output("代码看起来没问题，测试也通过了。")
    proc = FakeProc(claude_lines)

    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        # Mock feishu client
        mock_feishu.send_card_to_user = AsyncMock(return_value="card_msg_001")
        mock_feishu.update_card = AsyncMock(side_effect=lambda mid, content: card_updates.append(content))
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        # Mock store
        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    # 验证：发送了占位卡片
    mock_feishu.send_card_to_user.assert_called_once()
    assert mock_feishu.send_card_to_user.call_args[1].get("loading") is True

    # 验证：正文作为单独消息发出，卡片只保留状态
    assert any("代码看起来没问题" in m for m in post_messages)
    assert text_messages == []
    assert card_updates[-1] == "✅ 已完成"

    # 验证：session 状态被更新
    mock_store.on_claude_response.assert_called_once()


@pytest.mark.asyncio
async def test_private_post_message_extracts_text_for_model():
    """富文本 post 应抽成普通文本交给模型，而不是走 unsupported fallback。"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        message_type="post",
        content=_post_content([
            [
                {"tag": "text", "text": "你好，"},
                {"tag": "a", "text": "帮我看代码", "href": "https://example.invalid"},
            ],
            [{"tag": "text", "text": "第二段补充"}],
        ]),
    )
    seen = {}

    async def fake_run_and_display(*args, **kwargs):
        seen["text"] = args[3]

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("main._run_and_display", new=fake_run_and_display):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_msg_001")
        mock_feishu.send_text_to_user = AsyncMock(return_value="text_msg")

        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)

        await handle_message_async(event)

    assert seen["text"].startswith("你好，帮我看代码\n第二段补充")
    mock_feishu.send_text_to_user.assert_not_called()
    mock_feishu.send_card_to_user.assert_called_once()


@pytest.mark.asyncio
async def test_qq_code_model_tool_is_owner_private_only(monkeypatch):
    """自然语言补码请求只在 owner 私聊里给模型窄工具。"""
    from main import handle_message_async, _chat_locks
    from tool_policy import QQ_CODE_ALLOWED_TOOLS

    monkeypatch.setattr("main.config.ADMIN_USERS", {"owner"})
    monkeypatch.setattr("main.config.ALLOWED_USERS", {"owner", "friend"})
    _chat_locks.clear()

    seen = []

    async def fake_run_and_display(*args, **kwargs):
        seen.append(
            {
                "user_id": args[0],
                "text": args[3],
                "allowed_tools": kwargs.get("allowed_tools"),
            }
        )

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("main._run_and_display", new=fake_run_and_display):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_msg_001")
        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)

        await handle_message_async(
            _make_event(user_id="owner", chat_id="owner", text="我 QQ 小号掉线了，帮我把码发过来")
        )
        await handle_message_async(
            _make_event(user_id="friend", chat_id="friend", text="我 QQ 小号掉线了，帮我把码发过来")
        )

    assert seen[0]["allowed_tools"] == QQ_CODE_ALLOWED_TOOLS
    assert "rtime-qq-code request" in seen[0]["text"]
    assert seen[1]["allowed_tools"] is None
    assert "rtime-qq-code request" not in seen[1]["text"]


@pytest.mark.asyncio
async def test_private_chat_sends_text_as_message_not_streaming_card():
    """普通正文应该作为独立消息发出，不再作为工具卡片流式细节展示"""
    from main import handle_message_async, _chat_locks
    import bot_config as config

    _chat_locks.clear()
    # 生成一段比 STREAM_CHUNK_SIZE 长的文本，确保触发中间推送
    long_text = "x" * (config.STREAM_CHUNK_SIZE * 3)
    event = _make_event(text="写段代码")
    claude_lines = _make_claude_output(long_text)
    proc = FakeProc(claude_lines)

    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock(side_effect=lambda mid, content: card_updates.append(content))
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "existing_sid"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    assert post_messages == [long_text]
    assert text_messages == []
    assert card_updates[-1] == "✅ 已完成"


@pytest.mark.asyncio
async def test_private_chat_markdown_body_uses_card_markdown_and_normalizes_table():
    """Markdown body should be sent as Feishu card markdown, not plain text."""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    markdown = "## 总体结论\n| 课程 | 状态 |\n| --- | --- |\n| 热统 | 有 |"
    event = _make_event(text="检查资料")
    proc = FakeProc(_make_claude_output(markdown))

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock()
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "existing_sid"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    mock_feishu.send_markdown_to_user.assert_awaited_once()
    mock_feishu.send_post_to_user.assert_not_awaited()
    assert post_messages == [
        "## 总体结论\n\n| 课程 | 状态 |\n| --- | --- |\n| 热统 | 有 |"
    ]
    assert text_messages == []


@pytest.mark.asyncio
async def test_private_chat_latex_body_renders_unicode_in_card():
    """LaTeX math renders to inline Unicode in the card — never an image, never lossy drop."""
    from main import handle_message_async, _chat_locks
    from latex_unicode import render_math_for_feishu

    _chat_locks.clear()
    markdown = "## 行内公式\n能量 $E = mc^2$。\n\n$$\n\\int e^{-x^2} dx = \\sqrt{\\pi}\n$$"
    expected = render_math_for_feishu(markdown)

    event = _make_event(text="发公式")
    proc = FakeProc(_make_claude_output(markdown))

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock()
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "existing_sid"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    # No image fallback; rendered once as a markdown card.
    mock_feishu.send_image_to_user.assert_not_awaited()
    mock_feishu.send_markdown_to_user.assert_awaited_once()
    assert post_messages == [expected]
    # Math is rendered to Unicode, not left as TeX source.
    assert "mc²" in expected and "√(π)" in expected
    assert "$$" not in expected and "\\int" not in expected
    assert text_messages == []


@pytest.mark.asyncio
async def test_private_chat_card_markdown_failure_falls_back_to_text(tmp_path, monkeypatch):
    """A card markdown API failure should fall back visibly and leave an audit event."""
    from main import handle_message_async, _chat_locks

    log_path = tmp_path / "run-log.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(log_path))
    _chat_locks.clear()
    markdown = "**粗体**\n\n| A | B |\n| --- | --- |"
    event = _make_event(text="发 Markdown")
    proc = FakeProc(_make_claude_output(markdown))
    text_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock()
        mock_feishu.send_markdown_to_user = AsyncMock(side_effect=RuntimeError("card markdown unavailable"))
        mock_feishu.reply_markdown = AsyncMock(side_effect=RuntimeError("card markdown unavailable"))
        mock_feishu.send_text_to_user = AsyncMock(
            side_effect=lambda _open_id, text: text_messages.append(text) or "msg_text_001"
        )
        mock_feishu.reply_text = AsyncMock()

        mock_session = MagicMock()
        mock_session.session_id = "existing_sid"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    assert text_messages == [markdown]
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert any(record.get("event") == "feishu_render_fallback" for record in records)


@pytest.mark.asyncio
async def test_image_message_downloads_and_passes_path_to_claude():
    """图片消息应该下载资源，把本地路径传给 Claude，而不是静默忽略"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        message_type="image",
        message_id="img_msg_001",
        content={"image_key": "img_key_001"},
    )
    claude_lines = _make_claude_output("图片里是一张流程图。")

    captured_stdin = []

    class CapturingProc(FakeProc):
        def __init__(self, lines):
            super().__init__(lines)
            self.stdin = MagicMock()
            self.stdin.drain = AsyncMock()
            self.stdin.close = MagicMock()

            def capture_write(data):
                captured_stdin.append(data)

            self.stdin.write = capture_write

    proc = CapturingProc(claude_lines)
    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_img")
        mock_feishu.update_card = AsyncMock(side_effect=lambda _mid, content: card_updates.append(content))
        mock_feishu.download_image = AsyncMock(return_value="/tmp/feishu-img-test.png")
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    mock_feishu.download_image.assert_awaited_once_with("img_msg_001", "img_key_001")
    assert any("图片已下载" in update for update in card_updates)
    assert captured_stdin
    sent_text = captured_stdin[0].decode("utf-8")
    assert "/tmp/feishu-img-test.png" in sent_text
    assert any("图片里是一张流程图" in message for message in post_messages)
    assert text_messages == []


@pytest.mark.asyncio
async def test_image_message_without_key_replies_error():
    """图片事件缺少 image_key 时必须可见回复，不能无声 return"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        message_type="image",
        message_id="img_msg_missing_key",
        content={},
    )

    with patch("main.feishu") as mock_feishu:
        mock_feishu.send_card_to_user = AsyncMock(return_value="card_img")
        mock_feishu.send_text_to_user = AsyncMock(return_value="text_msg")
        await handle_message_async(event)

    mock_feishu.send_text_to_user.assert_awaited_once()
    assert "没有 image_key" in mock_feishu.send_text_to_user.call_args.args[1]


@pytest.mark.asyncio
async def test_file_message_downloads_and_passes_path_to_claude():
    """文件消息应该下载资源，把文件路径和文件名传给 Claude"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        message_type="file",
        message_id="file_msg_001",
        content={"file_key": "file_key_001", "file_name": "second-classroom.pdf"},
    )
    claude_lines = _make_claude_output("我已看到这个 PDF。")

    captured_stdin = []

    class CapturingProc(FakeProc):
        def __init__(self, lines):
            super().__init__(lines)
            self.stdin = MagicMock()
            self.stdin.drain = AsyncMock()
            self.stdin.close = MagicMock()

            def capture_write(data):
                captured_stdin.append(data)

            self.stdin.write = capture_write

    proc = CapturingProc(claude_lines)
    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_file")
        mock_feishu.update_card = AsyncMock(side_effect=lambda _mid, content: card_updates.append(content))
        mock_feishu.download_file = AsyncMock(return_value="/tmp/feishu-file-test.pdf")
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    mock_feishu.download_file.assert_awaited_once_with(
        "file_msg_001",
        "file_key_001",
        "second-classroom.pdf",
    )
    assert any("文件已下载" in update for update in card_updates)
    assert captured_stdin
    sent_text = captured_stdin[0].decode("utf-8")
    assert "/tmp/feishu-file-test.pdf" in sent_text
    assert "second-classroom.pdf" in sent_text
    assert any("我已看到这个 PDF" in message for message in post_messages)
    assert text_messages == []


@pytest.mark.asyncio
async def test_unsupported_message_type_replies_instead_of_silent_return():
    """未知消息类型应该给用户可见反馈，避免表现为机器人没收到"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        message_type="sticker",
        message_id="sticker_msg_001",
        content={"key": "sticker_key"},
    )

    with patch("main.feishu") as mock_feishu:
        mock_feishu.send_text_to_user = AsyncMock(return_value="text_msg")
        await handle_message_async(event)

    mock_feishu.send_text_to_user.assert_awaited_once()
    assert "暂时还不能处理" in mock_feishu.send_text_to_user.call_args.args[1]


@pytest.mark.asyncio
async def test_segmented_output_keeps_final_option_buttons():
    """多消息正文模式下，最终选项仍保留在状态卡按钮里"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    final_text = "需要你确认下一步：\n\n1. 继续执行\n2. 暂停"
    event = _make_event(text="下一步怎么处理")
    proc = FakeProc(_make_claude_output(final_text))

    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock()
        mock_feishu.update_card_with_buttons = AsyncMock()
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "existing_sid"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    assert post_messages == [final_text]
    assert text_messages == []
    mock_feishu.update_card.assert_not_called()
    mock_feishu.update_card_with_buttons.assert_awaited_once()
    _, content, buttons = mock_feishu.update_card_with_buttons.call_args[0]
    assert content == "请选择："
    assert buttons == [
        {"text": "1. 继续执行", "value": {"reply": "1", "cid": "user_001"}},
        {"text": "2. 暂停", "value": {"reply": "2", "cid": "user_001"}},
    ]


@pytest.mark.asyncio
async def test_tool_use_hidden_and_text_segments_sent_separately():
    """工具调用不展示给用户；工具前后的正文按段落消息发送"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(text="列出文件")
    claude_lines = [
        json.dumps({"type": "system", "session_id": "sid_abc123"}).encode() + b"\n",
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "我先检查一下。"},
            },
        }).encode() + b"\n",
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_start",
                "content_block": {"type": "tool_use", "name": "Bash"},
            },
        }).encode() + b"\n",
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {
                    "type": "input_json_delta",
                    "partial_json": json.dumps({"command": "ls -la"}),
                },
            },
        }).encode() + b"\n",
        json.dumps({
            "type": "stream_event",
            "event": {"type": "content_block_stop"},
        }).encode() + b"\n",
        json.dumps({
            "type": "stream_event",
            "event": {
                "type": "content_block_delta",
                "delta": {"type": "text_delta", "text": "文件列表如下..."},
            },
        }).encode() + b"\n",
        json.dumps({
            "type": "result",
            "session_id": "sid_abc123",
            "result": "我先检查一下。文件列表如下...",
        }).encode() + b"\n",
    ]
    proc = FakeProc(claude_lines)

    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock(side_effect=lambda mid, content: card_updates.append(content))
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "sid_123"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    assert post_messages == ["我先检查一下。", "文件列表如下..."]
    assert text_messages == []
    assert not any("ls -la" in u or "执行命令" in u for u in card_updates)
    assert card_updates[-1] == "✅ 已完成"


# ── 测试：群聊 @mention 过滤 ─────────────────────────────────

@pytest.mark.asyncio
async def test_group_chat_ignores_without_mention():
    """群聊消息没有 @机器人 时应该被忽略"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        user_id="user_001",
        chat_id="group_001",
        chat_type="group",
        text="这是一条普通群消息",
        mentions=None,  # 没有 @mention
    )

    with patch("main.feishu") as mock_feishu, \
         patch("main.store"):
        mock_feishu.send_card_to_user = AsyncMock()
        mock_feishu.reply_card = AsyncMock()

        await handle_message_async(event)

    # 不应该有任何 feishu 调用
    mock_feishu.send_card_to_user.assert_not_called()
    mock_feishu.reply_card.assert_not_called()


@pytest.mark.asyncio
async def test_group_chat_ignores_empty_mention_list():
    """群聊消息 mentions 为空列表时也应该被忽略"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(
        user_id="user_001",
        chat_id="group_001",
        chat_type="group",
        text="没人被 at",
        mentions=[],
    )

    with patch("main.feishu") as mock_feishu:
        await handle_message_async(event)

    mock_feishu.reply_card.assert_not_called()


@pytest.mark.asyncio
async def test_new_message_does_not_interrupt_active_run():
    """运行中的同一用户新消息不自动打断，靠 chat lock 接到下一轮处理"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(text="第二条补充")

    active_run = MagicMock()
    active_run.stop_requested = False

    with patch("main._active_runs.get_run", return_value=active_run), \
         patch("main.stop_run", new_callable=AsyncMock) as mock_stop, \
         patch("main._process_message", new_callable=AsyncMock) as mock_process:

        await handle_message_async(event)

    mock_stop.assert_not_awaited()
    mock_process.assert_awaited_once()


@pytest.mark.asyncio
async def test_disallowed_private_user_is_ignored(monkeypatch):
    """配置 ALLOWED_USERS 后，非授权私聊不会进入处理流程"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(user_id="bad_user", chat_id="bad_user", text="hello")
    monkeypatch.setattr("main.config.ALLOWED_USERS", {"allowed_user"})
    monkeypatch.setattr("main.config.ALLOWED_CHATS", set())

    with patch("main._process_message", new_callable=AsyncMock) as mock_process:
        await handle_message_async(event)

    mock_process.assert_not_awaited()


def test_group_denied_without_allowed_chat(monkeypatch):
    """v1 默认个人私聊；群聊必须显式配置 ALLOWED_CHATS 才允许"""
    from main import _is_allowed_actor

    monkeypatch.setattr("main.config.ALLOWED_USERS", {"allowed_user"})
    monkeypatch.setattr("main.config.ALLOWED_CHATS", set())

    assert not _is_allowed_actor("allowed_user", "group_001", True)
    assert _is_allowed_actor("allowed_user", "allowed_user", False)


@pytest.mark.asyncio
async def test_group_chat_responds_with_mention():
    """群聊消息有 @机器人 时应该回复"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()

    # 模拟 @mention 对象
    mention = MagicMock()
    mention.key = "@_user_1"

    event = _make_event(
        user_id="user_001",
        chat_id="group_001",
        chat_type="group",
        text="@_user_1 你好",
        message_id="group_msg_001",
        mentions=[mention],
    )

    claude_lines = _make_claude_output("你好！有什么可以帮你的？")
    proc = FakeProc(claude_lines)

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.reply_card = AsyncMock(return_value="reply_card_001")
        mock_feishu.update_card = AsyncMock()
        _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    # 群聊应该用 reply_card 而不是 send_card_to_user
    mock_feishu.reply_card.assert_called()
    # 第一次调用是占位卡片
    first_call = mock_feishu.reply_card.call_args_list[0]
    assert first_call[0][0] == "group_msg_001"  # reply to original message
    assert first_call[1].get("loading") is True


@pytest.mark.asyncio
async def test_group_chat_strips_mention_placeholder():
    """群聊应该去掉 @mention 占位符后再发给 Claude"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()

    mention = MagicMock()
    mention.key = "@_user_1"

    event = _make_event(
        user_id="user_001",
        chat_id="group_001",
        chat_type="group",
        text="@_user_1 帮我看看这段代码",
        message_id="group_msg_002",
        mentions=[mention],
    )

    captured_stdin = []
    claude_lines = _make_claude_output("代码没问题")

    class CapturingProc(FakeProc):
        def __init__(self, lines):
            super().__init__(lines)
            self.stdin = MagicMock()
            self.stdin.drain = AsyncMock()
            self.stdin.close = MagicMock()
            def capture_write(data):
                captured_stdin.append(data)
            self.stdin.write = capture_write

    proc = CapturingProc(claude_lines)

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", return_value=proc):

        mock_feishu.reply_card = AsyncMock(return_value="reply_card_002")
        mock_feishu.update_card = AsyncMock()
        _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = None
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    # 验证发给 Claude 的文本不包含 @mention 占位符
    assert len(captured_stdin) > 0
    sent_text = captured_stdin[0].decode("utf-8")
    assert "@_user_1" not in sent_text
    assert "帮我看看这段代码" in sent_text


# ── 测试：群聊斜杠命令 ──────────────────────────────────────

@pytest.mark.asyncio
async def test_group_chat_slash_command_with_mention():
    """群聊中 @机器人 + 斜杠命令应该正常工作"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()

    mention = MagicMock()
    mention.key = "@_user_1"

    event = _make_event(
        user_id="user_001",
        chat_id="group_001",
        chat_type="group",
        text="@_user_1 /help",
        message_id="group_msg_003",
        mentions=[mention],
    )

    with patch("main.feishu") as mock_feishu, \
         patch("main.store"):

        mock_feishu.reply_card = AsyncMock(return_value="reply_card_003")

        await handle_message_async(event)

    # /help 应该通过 reply_card 回复
    mock_feishu.reply_card.assert_called()
    # 回复内容应该包含帮助文本
    call_args = mock_feishu.reply_card.call_args
    assert "可用命令" in call_args[1].get("content", "")


# ── 测试：session 隔离（端到端）──────────────────────────────

@pytest.mark.asyncio
async def test_group_sessions_isolated_end_to_end():
    """不同群聊发消息，各自的 session 独立"""
    from main import handle_message_async, _chat_locks
    from session_store import SessionStore

    _chat_locks.clear()

    mention = MagicMock()
    mention.key = "@_user_1"

    # 两个群分别发 /model 命令设置不同模型
    event_a = _make_event(
        user_id="user_001", chat_id="group_a", chat_type="group",
        text="@_user_1 /model opus", message_id="msg_a",
        mentions=[mention],
    )
    event_b = _make_event(
        user_id="user_001", chat_id="group_b", chat_type="group",
        text="@_user_1 /model haiku", message_id="msg_b",
        mentions=[mention],
    )

    real_store = SessionStore()

    with patch("main.feishu") as mock_feishu, \
         patch("main.store", real_store):

        mock_feishu.reply_card = AsyncMock(return_value="card_id")

        await handle_message_async(event_a)
        await handle_message_async(event_b)

    session_a = await real_store.get_current("user_001", "group_a")
    session_b = await real_store.get_current("user_001", "group_b")

    assert session_a.model == "claude-opus-4-6"
    assert session_b.model == "claude-haiku-4-5-20251001"


# ── 测试：_chat_locks 清理 ──────────────────────────────────

@pytest.mark.asyncio
async def test_chat_locks_cleanup():
    """当 _chat_locks 超过上限时应该清理"""
    from main import _chat_locks, _MAX_CHAT_LOCKS, handle_message_async

    _chat_locks.clear()

    # 填满锁到上限
    for i in range(_MAX_CHAT_LOCKS):
        _chat_locks[f"chat_{i}"] = asyncio.Lock()

    assert len(_chat_locks) == _MAX_CHAT_LOCKS

    # 发一条新 chat 的消息，应该触发清理
    event = _make_event(
        user_id="user_new",
        chat_id="brand_new_chat",
        chat_type="p2p",
        text="/help",
    )

    with patch("main.feishu") as mock_feishu:
        mock_feishu.send_card_to_user = AsyncMock(return_value="card_id")
        await handle_message_async(event)

    # 清理后只剩新加入的那个（私聊 chat_id = user_id）
    assert len(_chat_locks) <= 2
    assert "user_new" in _chat_locks


# ── 测试：fresh session fallback ─────────────────────────────

@pytest.mark.asyncio
async def test_fresh_session_fallback_shows_warning():
    """当旧 session 失败并自动切换新 session 时，应该显示警告"""
    from main import handle_message_async, _chat_locks

    _chat_locks.clear()
    event = _make_event(text="继续刚才的")

    # 第一次调用失败（returncode=1, no stderr, no output）
    first_proc = FakeProc([], returncode=1)
    # 第二次调用成功
    second_lines = _make_claude_output("好的，我来帮你")
    second_proc = FakeProc(second_lines)
    procs = iter([first_proc, second_proc])

    card_updates = []
    post_messages = []

    with patch("main.feishu") as mock_feishu, \
         patch("main.store") as mock_store, \
         patch("asyncio.create_subprocess_exec", side_effect=lambda *a, **kw: next(procs)):

        mock_feishu.send_card_to_user = AsyncMock(return_value="card_001")
        mock_feishu.update_card = AsyncMock(side_effect=lambda mid, content: card_updates.append(content))
        post_messages, text_messages = _capture_rendered_messages(mock_feishu)

        mock_session = MagicMock()
        mock_session.session_id = "old_sid_that_fails"
        mock_session.model = "claude-sonnet-4-6"
        mock_session.cwd = "/tmp"
        mock_session.permission_mode = "bypassPermissions"
        mock_store.get_current = AsyncMock(return_value=mock_session)
        mock_store.on_claude_response = AsyncMock()

        await handle_message_async(event)

    assert any("自动切换到新 session" in m for m in post_messages), \
        f"No fallback warning in messages: {post_messages}"
    assert any("好的，我来帮你" in m for m in post_messages)
    assert text_messages == []
