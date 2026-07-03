# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OneBot v11 event parsing and action construction.

Keeps the wire format (ints for ids, CQ strings / segment arrays for messages) at
the edge and exposes a channel-neutral ``IncomingMessage`` to the rest of the
bridge. Identity mapping mirrors the design doc: private -> chat_id == user_id;
group -> chat_id == group_id.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .cqcode import MediaSegment, extract_media, extract_plain_text, mentioned_user_ids


@dataclass(frozen=True)
class IncomingMessage:
    self_id: str
    message_type: str  # "private" | "group"
    user_id: str
    group_id: str | None
    chat_id: str
    is_group: bool
    message_id: str
    text: str
    event_time: float | None = None
    sub_type: str = ""  # private: friend | group(temporary) | other; group varies
    mentions: list[str] = field(default_factory=list)
    media: list[MediaSegment] = field(
        default_factory=list
    )  # image/sticker/face/file/voice
    raw: dict[str, Any] = field(default_factory=dict)


def is_message_event(payload: dict[str, Any]) -> bool:
    return payload.get("post_type") == "message"


def parse_message_event(payload: dict[str, Any]) -> IncomingMessage:
    """Normalize a OneBot ``message`` event into an ``IncomingMessage``."""
    message_type = str(payload.get("message_type", "private"))
    is_group = message_type == "group"
    user_id = str(payload.get("user_id", ""))
    group_id = str(payload["group_id"]) if payload.get("group_id") is not None else None
    chat_id = group_id if (is_group and group_id) else user_id
    raw_message = payload.get("message", payload.get("raw_message", ""))
    return IncomingMessage(
        self_id=str(payload.get("self_id", "")),
        message_type=message_type,
        user_id=user_id,
        group_id=group_id,
        chat_id=chat_id,
        is_group=is_group,
        message_id=str(payload.get("message_id", "")),
        text=extract_plain_text(raw_message),
        event_time=_event_time(payload),
        sub_type=str(payload.get("sub_type", "")),
        mentions=mentioned_user_ids(raw_message),
        media=extract_media(raw_message),
        raw=payload,
    )


def _event_time(payload: dict[str, Any]) -> float | None:
    """OneBot v11 message ``time`` is seconds since epoch. Missing/invalid => unknown."""
    value = payload.get("time")
    if value is None:
        return None
    try:
        ts = float(value)
    except (TypeError, ValueError):
        return None
    # Be tolerant of millisecond timestamps from nonstandard producers.
    if ts > 10_000_000_000:
        ts /= 1000.0
    return ts


def _as_int(value: str) -> int | str:
    """QQ ids are numeric; fall back to the raw string if it ever isn't."""
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _at_sender_text(msg: IncomingMessage, text: str) -> str:
    prefix = f"[CQ:at,qq={msg.user_id}] "
    return text if text.startswith(prefix) else f"{prefix}{text}"


def reply_action(
    msg: IncomingMessage, text: str, *, at_sender: bool = False
) -> tuple[str, dict[str, Any]]:
    """Build the (action, params) pair to send ``text`` back to ``msg``'s chat."""
    if msg.is_group and msg.group_id:
        return "send_group_msg", {
            "group_id": _as_int(msg.group_id),
            "message": _at_sender_text(msg, text) if at_sender else text,
        }
    return "send_private_msg", {"user_id": _as_int(msg.user_id), "message": text}


def image_send_action(
    msg: IncomingMessage, file_uri: str
) -> tuple[str, dict[str, Any]]:
    """Send an image back as a message segment. ``file_uri`` is ``base64://…`` (preferred,
    works across the napcat/bridge container boundary) or a ``file:///abs`` / http URL."""
    segment = [{"type": "image", "data": {"file": file_uri}}]
    if msg.is_group and msg.group_id:
        return "send_group_msg", {"group_id": _as_int(msg.group_id), "message": segment}
    return "send_private_msg", {"user_id": _as_int(msg.user_id), "message": segment}


def file_upload_action(
    msg: IncomingMessage, file_uri: str, name: str
) -> tuple[str, dict[str, Any]]:
    """Upload a file to the chat. NapCat accepts ``base64://…`` for ``file``."""
    if msg.is_group and msg.group_id:
        return "upload_group_file", {
            "group_id": _as_int(msg.group_id),
            "file": file_uri,
            "name": name,
        }
    return "upload_private_file", {
        "user_id": _as_int(msg.user_id),
        "file": file_uri,
        "name": name,
    }
