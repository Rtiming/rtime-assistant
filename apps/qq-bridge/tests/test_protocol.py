# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OneBot event parsing and reply-action construction."""

from qq_bridge.onebot.protocol import (
    is_message_event,
    parse_message_event,
    reply_action,
)


def _private_event(text="复习配分函数", user_id=10001):
    return {
        "post_type": "message",
        "message_type": "private",
        "sub_type": "friend",
        "user_id": user_id,
        "self_id": 99999,
        "message_id": 555,
        "message": text,
        "raw_message": text,
        "sender": {"user_id": user_id},
    }


def test_is_message_event():
    assert is_message_event(_private_event())
    assert not is_message_event(
        {"post_type": "meta_event", "meta_event_type": "lifecycle"}
    )


def test_parse_private_maps_chat_to_user():
    payload = _private_event()
    payload["time"] = 1783000000
    msg = parse_message_event(payload)
    assert msg.is_group is False
    assert msg.user_id == "10001"
    assert msg.chat_id == "10001"  # private: chat_id == user_id
    assert msg.group_id is None
    assert msg.text == "复习配分函数"
    assert msg.self_id == "99999"
    assert msg.sub_type == "friend"
    assert msg.event_time == 1783000000.0


def test_parse_group_maps_chat_to_group():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 10001,
        "group_id": 7001,
        "self_id": 99999,
        "message_id": 9,
        "message": "[CQ:at,qq=99999] hi",
    }
    msg = parse_message_event(payload)
    assert msg.is_group is True
    assert msg.chat_id == "7001"  # group: chat_id == group_id
    assert msg.group_id == "7001"
    assert msg.mentions == ["99999"]


def test_reply_action_private():
    msg = parse_message_event(_private_event())
    action, params = reply_action(msg, "ok")
    assert action == "send_private_msg"
    assert params == {"user_id": 10001, "message": "ok"}  # numeric id


def test_reply_action_group():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 1,
        "group_id": 7001,
        "message": "hi",
    }
    msg = parse_message_event(payload)
    action, params = reply_action(msg, "ok")
    assert action == "send_group_msg"
    assert params == {"group_id": 7001, "message": "ok"}


def test_reply_action_group_at_sender():
    payload = {
        "post_type": "message",
        "message_type": "group",
        "user_id": 12345,
        "group_id": 7001,
        "message": "hi",
    }
    msg = parse_message_event(payload)
    action, params = reply_action(msg, "ok", at_sender=True)
    assert action == "send_group_msg"
    assert params == {"group_id": 7001, "message": "[CQ:at,qq=12345] ok"}
