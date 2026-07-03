# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import asyncio
import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


def _make_event(
    *,
    user_id: str = "user_001",
    chat_id: str = "user_001",
    chat_type: str = "p2p",
    text: str,
    message_id: str,
):
    event = MagicMock()
    event.event.sender.sender_id.open_id = user_id
    event.event.message.chat_type = chat_type
    event.event.message.chat_id = chat_id
    event.event.message.message_type = "text"
    event.event.message.content = json.dumps({"text": text}, ensure_ascii=False)
    event.event.message.message_id = message_id
    event.event.message.mentions = []
    return event


def _message_text(msg) -> str:
    return json.loads(msg.content)["text"]


@pytest.mark.asyncio
async def test_near_simultaneous_private_messages_are_debounced(monkeypatch):
    import main

    main._chat_locks.clear()
    main._chat_debounce_queues.clear()
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_SECONDS", 0.01)
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_MAX_MESSAGES", 20)
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_MAX_CHARS", 12000)

    captured = []

    async def fake_process(user_id, chat_id, is_group, msg):
        captured.append((user_id, chat_id, is_group, _message_text(msg), msg.message_id))

    events = [
        _make_event(text="第一条", message_id="msg_1"),
        _make_event(text="第二条", message_id="msg_2"),
        _make_event(text="第三条", message_id="msg_3"),
    ]

    with patch("main._process_message", new=fake_process):
        await asyncio.wait_for(
            asyncio.gather(*(main.handle_message_async(event) for event in events)),
            timeout=1,
        )

    assert len(captured) == 1
    assert captured[0][4] == "msg_3"
    assert "[1] 第一条" in captured[0][3]
    assert "[2] 第二条" in captured[0][3]
    assert "[3] 第三条" in captured[0][3]
    assert "只进行一次整体回复" in captured[0][3]


@pytest.mark.asyncio
async def test_followups_while_chat_is_busy_are_merged_after_active_run(monkeypatch):
    import main

    main._chat_locks.clear()
    main._chat_debounce_queues.clear()
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_SECONDS", 0.01)
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_MAX_MESSAGES", 20)
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_MAX_CHARS", 12000)

    first_started = asyncio.Event()
    release_first = asyncio.Event()
    captured = []

    async def fake_process(user_id, chat_id, is_group, msg):
        captured.append(_message_text(msg))
        if len(captured) == 1:
            first_started.set()
            await release_first.wait()

    with patch("main._process_message", new=fake_process):
        first_task = asyncio.create_task(
            main.handle_message_async(_make_event(text="第一条", message_id="msg_1"))
        )
        await asyncio.wait_for(first_started.wait(), timeout=1)
        await asyncio.gather(
            main.handle_message_async(_make_event(text="第二条", message_id="msg_2")),
            main.handle_message_async(_make_event(text="第三条", message_id="msg_3")),
        )
        release_first.set()
        await asyncio.wait_for(first_task, timeout=1)

    assert len(captured) == 2
    assert captured[0] == "第一条"
    assert "[1] 第二条" in captured[1]
    assert "[2] 第三条" in captured[1]


@pytest.mark.asyncio
async def test_slash_commands_bypass_debounce(monkeypatch):
    import main

    main._chat_locks.clear()
    main._chat_debounce_queues.clear()
    monkeypatch.setattr(main.config, "MESSAGE_DEBOUNCE_SECONDS", 0.05)

    with patch("main._process_message", new_callable=AsyncMock) as mock_process:
        await main.handle_message_async(_make_event(text="/new", message_id="msg_cmd"))

    mock_process.assert_awaited_once()
    assert main._chat_debounce_queues == {}
