# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Synthetic inbound-event constructors for the simulation harness.

Builds realistic wire-shaped events so tests exercise the bridges' REAL decode
path (OneBot v11 segment arrays via ``qq_bridge.onebot.protocol.parse_message_event``;
Feishu ``P2ImMessageReceiveV1``-shaped namespaces via ``main.handle_message_async``)
instead of hand-rolled ``IncomingMessage`` objects that skip the parser.

Shapes are copied from live NapCat traffic / existing test fixtures:
  - QQ events carry ints for ids on the wire (the parser normalizes to str),
    ``message`` as a NapCat-default segment array plus the CQ-string
    ``raw_message``, and a ``sender`` block;
  - Feishu events mirror ``apps/feishu-bridge/simulate_message_burst.py`` (the
    seed this module upgrades): ``event.event.sender.sender_id.open_id`` +
    ``event.event.message`` with chat_type/content/mentions.

stdlib only — safe to import in production installs.
"""

from __future__ import annotations

import json
import time as _time
from collections.abc import Sequence
from types import SimpleNamespace
from typing import Any

_DEFAULT_SELF_ID = "99999"


def _as_int(value: str | int) -> int | str:
    try:
        return int(value)
    except (TypeError, ValueError):
        return value


def _qq_base_event(
    *,
    message_type: str,
    sub_type: str,
    user_id: str | int,
    self_id: str | int,
    message_id: int,
    message: list[dict[str, Any]],
    raw_message: str,
    sender: dict[str, Any],
    event_time: int | None,
) -> dict[str, Any]:
    return {
        "post_type": "message",
        "message_type": message_type,
        "sub_type": sub_type,
        "message_id": message_id,
        "user_id": _as_int(user_id),
        "self_id": _as_int(self_id),
        "time": int(event_time if event_time is not None else _time.time()),
        "message_format": "array",
        "message": message,
        "raw_message": raw_message,
        "font": 14,
        "sender": sender,
    }


def make_qq_private(
    user_id: str | int = "10001",
    text: str = "你好",
    *,
    self_id: str | int = _DEFAULT_SELF_ID,
    message_id: int = 1000,
    sub_type: str = "friend",
    nickname: str = "同学",
    extra_segments: Sequence[dict[str, Any]] = (),
    event_time: int | None = None,
) -> dict[str, Any]:
    """A OneBot v11 private ``message`` event as NapCat delivers it.

    ``extra_segments`` appends raw OneBot segments (e.g. an image segment) after
    the text segment for multimodal cases.
    """
    segments: list[dict[str, Any]] = []
    if text:
        segments.append({"type": "text", "data": {"text": text}})
    segments.extend(dict(seg) for seg in extra_segments)
    return _qq_base_event(
        message_type="private",
        sub_type=sub_type,
        user_id=user_id,
        self_id=self_id,
        message_id=message_id,
        message=segments,
        raw_message=text,
        sender={"user_id": _as_int(user_id), "nickname": nickname, "card": ""},
        event_time=event_time,
    )


def make_qq_group_at(
    group_id: str | int = "600",
    user_id: str | int = "222",
    text: str = "东区班车几点",
    *,
    at_bot: bool = True,
    at_qq: str | int | None = None,
    self_id: str | int = _DEFAULT_SELF_ID,
    message_id: int = 1001,
    nickname: str = "同学",
    card: str = "",
    extra_segments: Sequence[dict[str, Any]] = (),
    event_time: int | None = None,
) -> dict[str, Any]:
    """A OneBot v11 group ``message`` event, optionally @-mentioning the bot.

    ``at_bot=True`` (default) prepends an at-segment targeting ``self_id`` — the
    shape that makes ``_group_message_triggered`` fire. ``at_bot=False`` yields
    plain group chatter; ``at_qq`` mentions someone else instead.
    """
    target = str(at_qq) if at_qq is not None else (str(self_id) if at_bot else None)
    segments: list[dict[str, Any]] = []
    raw_message = text
    if target is not None:
        segments.append({"type": "at", "data": {"qq": target}})
        # NapCat renders "@bot 问题" as an at segment + a text segment with the
        # leading space kept; parse strips it (IncomingMessage.text is trimmed).
        segments.append({"type": "text", "data": {"text": f" {text}"}})
        raw_message = f"[CQ:at,qq={target}] {text}"
    elif text:
        segments.append({"type": "text", "data": {"text": text}})
    segments.extend(dict(seg) for seg in extra_segments)
    event = _qq_base_event(
        message_type="group",
        sub_type="normal",
        user_id=user_id,
        self_id=self_id,
        message_id=message_id,
        message=segments,
        raw_message=raw_message,
        sender={
            "user_id": _as_int(user_id),
            "nickname": nickname,
            "card": card,
            "role": "member",
        },
        event_time=event_time,
    )
    event["group_id"] = _as_int(group_id)
    return event


def make_feishu_msg(
    text: str = "你好",
    *,
    user_id: str = "user_sim",
    chat_id: str | None = None,
    is_group: bool = False,
    message_id: str = "om_sim_1",
    message_type: str = "text",
    mention_keys: Sequence[str] = (),
    content: dict[str, Any] | None = None,
) -> SimpleNamespace:
    """A Feishu ``P2ImMessageReceiveV1``-shaped event namespace.

    Feeds ``main.handle_message_async`` directly (the Feishu bridge's injection
    point — same shape ``simulate_message_burst.py`` uses). ``mention_keys``
    become mention objects whose ``.key`` the bridge strips from group text;
    a non-empty list also satisfies the group @-mention requirement.
    """
    if chat_id is None:
        chat_id = "oc_sim_group" if is_group else user_id
    payload = content if content is not None else {"text": text}
    msg = SimpleNamespace(
        chat_type="group" if is_group else "p2p",
        chat_id=chat_id,
        message_type=message_type,
        content=json.dumps(payload, ensure_ascii=False),
        message_id=message_id,
        mentions=[
            SimpleNamespace(key=key, name=f"mention_{i}")
            for i, key in enumerate(mention_keys)
        ],
    )
    sender = SimpleNamespace(sender_id=SimpleNamespace(open_id=user_id))
    return SimpleNamespace(event=SimpleNamespace(sender=sender, message=msg))


__all__ = ["make_feishu_msg", "make_qq_group_at", "make_qq_private"]
