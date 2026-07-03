# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Per-chat lock management for queued bridge runs."""

from __future__ import annotations

import asyncio
from collections.abc import MutableMapping
from dataclasses import dataclass
from typing import Any


def cleanup_idle_locks(
    locks: MutableMapping[str, asyncio.Lock],
    max_locks: int,
) -> int:
    """Delete idle locks when the lock map reaches its configured limit."""
    if len(locks) < max_locks:
        return 0
    idle_keys = [key for key, lock in locks.items() if not lock.locked()]
    for key in idle_keys:
        del locks[key]
    return len(idle_keys)


def get_chat_lock(
    chat_id: str,
    locks: MutableMapping[str, asyncio.Lock],
    max_locks: int,
) -> asyncio.Lock:
    """Return the per-chat lock, creating it after safe idle cleanup if needed."""
    if chat_id not in locks:
        cleanup_idle_locks(locks, max_locks)
        locks[chat_id] = asyncio.Lock()
    return locks[chat_id]


@dataclass(slots=True)
class PendingChatMessage:
    user_id: str
    chat_id: str
    is_group: bool
    message_id: str
    text: str
    raw_message: Any


class ChatDebounceQueue:
    """Small per-chat pending buffer for near-simultaneous text messages."""

    def __init__(self) -> None:
        self.pending: list[PendingChatMessage] = []
        self.worker_active = False

    def append(self, item: PendingChatMessage) -> int:
        self.pending.append(item)
        return len(self.pending)

    def drain(self, max_messages: int) -> list[PendingChatMessage]:
        if not self.pending:
            return []
        if max_messages <= 0 or len(self.pending) <= max_messages:
            batch = self.pending
            self.pending = []
            return batch
        batch = self.pending[:max_messages]
        self.pending = self.pending[max_messages:]
        return batch

    def __len__(self) -> int:
        return len(self.pending)


def cleanup_idle_debounce_queues(
    queues: MutableMapping[str, ChatDebounceQueue],
    max_queues: int,
) -> int:
    if len(queues) < max_queues:
        return 0
    idle_keys = [
        key
        for key, queue in queues.items()
        if not queue.worker_active and not queue.pending
    ]
    for key in idle_keys:
        del queues[key]
    return len(idle_keys)


def get_chat_debounce_queue(
    chat_id: str,
    queues: MutableMapping[str, ChatDebounceQueue],
    max_queues: int,
) -> ChatDebounceQueue:
    if chat_id not in queues:
        cleanup_idle_debounce_queues(queues, max_queues)
        queues[chat_id] = ChatDebounceQueue()
    return queues[chat_id]


def merge_pending_messages(
    messages: list[PendingChatMessage],
    *,
    max_chars: int,
) -> tuple[PendingChatMessage, int]:
    """Return a representative message with merged text and overflow count."""
    if not messages:
        raise ValueError("messages must not be empty")
    first = messages[0]
    if len(messages) == 1:
        text = first.text
    else:
        parts = [
            f"[{index}] {item.text}"
            for index, item in enumerate(messages, start=1)
            if item.text.strip()
        ]
        text = (
            f"用户连续发送了 {len(parts)} 条消息。"
            "请把它们按顺序合并理解，只进行一次整体回复：\n\n" + "\n\n".join(parts)
        )
    overflow = 0
    if max_chars > 0 and len(text) > max_chars:
        overflow = len(text) - max_chars
        text = text[:max_chars].rstrip() + f"\n\n[已截断 {overflow} 个字符]"
    return (
        PendingChatMessage(
            user_id=first.user_id,
            chat_id=first.chat_id,
            is_group=first.is_group,
            message_id=messages[-1].message_id,
            text=text,
            raw_message=messages[-1].raw_message,
        ),
        overflow,
    )
