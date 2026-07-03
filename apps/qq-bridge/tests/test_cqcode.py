# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Plain-text extraction and @-mention parsing from OneBot messages."""

from qq_bridge.onebot.cqcode import extract_plain_text, mentioned_user_ids


def test_string_message_strips_cq_codes():
    assert extract_plain_text("hello[CQ:face,id=1]world") == "helloworld"


def test_string_message_strips_at_and_keeps_text():
    assert extract_plain_text("[CQ:at,qq=123] 复习配分函数").strip() == "复习配分函数"


def test_string_message_unescapes_special_chars():
    assert extract_plain_text("a&#91;b&#93;c&amp;d") == "a[b]c&d"


def test_array_message_concatenates_text_segments():
    msg = [
        {"type": "at", "data": {"qq": "123"}},
        {"type": "text", "data": {"text": "热统"}},
        {"type": "image", "data": {"file": "x.png"}},
        {"type": "text", "data": {"text": "配分函数"}},
    ]
    assert extract_plain_text(msg) == "热统配分函数"


def test_mentions_from_string_and_array():
    assert mentioned_user_ids("[CQ:at,qq=10001] hi [CQ:at,qq=20002]") == [
        "10001",
        "20002",
    ]
    assert mentioned_user_ids([{"type": "at", "data": {"qq": "777"}}]) == ["777"]


def test_non_message_inputs_are_empty():
    assert extract_plain_text(None) == ""
    assert extract_plain_text(42) == ""
    assert mentioned_user_ids(None) == []
