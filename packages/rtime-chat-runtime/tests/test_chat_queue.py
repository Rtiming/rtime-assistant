# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import asyncio
from types import SimpleNamespace

from rtime_chat_runtime.chat_queue import (
    ChatDebounceQueue,
    PendingChatMessage,
    cleanup_idle_debounce_queues,
    cleanup_idle_locks,
    get_chat_debounce_queue,
    get_chat_lock,
    merge_pending_messages,
)


def test_get_chat_lock_reuses_existing_lock():
    locks = {"chat_a": asyncio.Lock()}

    lock = get_chat_lock("chat_a", locks, max_locks=10)

    assert lock is locks["chat_a"]
    assert len(locks) == 1


def test_get_chat_lock_cleans_only_idle_locks_when_full():
    locks = {}
    held = asyncio.Lock()

    async def acquire():
        await held.acquire()

    asyncio.run(acquire())
    locks["held"] = held
    locks["idle_a"] = asyncio.Lock()
    locks["idle_b"] = asyncio.Lock()

    lock = get_chat_lock("new_chat", locks, max_locks=3)

    assert locks["held"] is held
    assert locks["new_chat"] is lock
    assert "idle_a" not in locks
    assert "idle_b" not in locks
    held.release()


def test_cleanup_idle_locks_noops_below_limit():
    locks = {"chat_a": asyncio.Lock()}

    removed = cleanup_idle_locks(locks, max_locks=2)

    assert removed == 0
    assert "chat_a" in locks


def test_debounce_queue_drains_with_max_messages():
    queue = ChatDebounceQueue()
    for index in range(3):
        queue.append(
            PendingChatMessage(
                user_id="user",
                chat_id="chat",
                is_group=False,
                message_id=f"msg_{index}",
                text=f"text {index}",
                raw_message=SimpleNamespace(),
            )
        )

    batch = queue.drain(max_messages=2)

    assert [item.text for item in batch] == ["text 0", "text 1"]
    assert len(queue) == 1


def test_merge_pending_messages_preserves_order_and_representative_message_id():
    messages = [
        PendingChatMessage("user", "chat", False, "msg_1", "第一条", SimpleNamespace()),
        PendingChatMessage("user", "chat", False, "msg_2", "第二条", SimpleNamespace()),
    ]

    merged, overflow = merge_pending_messages(messages, max_chars=0)

    assert overflow == 0
    assert merged.message_id == "msg_2"
    assert "[1] 第一条" in merged.text
    assert "[2] 第二条" in merged.text
    assert "只进行一次整体回复" in merged.text


def test_merge_pending_messages_truncates_when_needed():
    messages = [
        PendingChatMessage(
            "user", "chat", False, "msg_1", "a" * 100, SimpleNamespace()
        ),
    ]

    merged, overflow = merge_pending_messages(messages, max_chars=20)

    assert overflow > 0
    assert "已截断" in merged.text


def test_get_chat_debounce_queue_cleans_idle_queues_when_full():
    active = ChatDebounceQueue()
    active.worker_active = True
    queues = {
        "active": active,
        "idle": ChatDebounceQueue(),
    }

    queue = get_chat_debounce_queue("new", queues, max_queues=2)

    assert queues["active"] is active
    assert queues["new"] is queue
    assert "idle" not in queues


def test_cleanup_idle_debounce_queues_noops_below_limit():
    queues = {"chat": ChatDebounceQueue()}

    removed = cleanup_idle_debounce_queues(queues, max_queues=2)

    assert removed == 0
    assert "chat" in queues
