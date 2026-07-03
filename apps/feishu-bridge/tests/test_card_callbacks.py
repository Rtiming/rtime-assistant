# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from card_callbacks import parse_card_action, toast_for_action, warning_toast


def test_parse_card_action_defaults_chat_to_user():
    action = parse_card_action("user_001", {"reply": "yes"}, "card_001")

    assert action.user_id == "user_001"
    assert action.chat_id == "user_001"
    assert action.clicked_msg_id == "card_001"
    assert action.reply_text == "yes"


def test_parse_card_action_extracts_command_fields():
    action = parse_card_action(
        "user_001",
        {"action": "run_cmd", "cid": "chat_001", "cmd": "/status"},
    )

    assert action.action_type == "run_cmd"
    assert action.chat_id == "chat_001"
    assert action.cmd_text == "/status"


def test_toast_for_actions():
    assert toast_for_action(parse_card_action("u", {"action": "set_mode", "mode": "plan"})).type == "success"
    assert toast_for_action(parse_card_action("u", {"action": "resume_session"})).content == "正在恢复..."
    assert warning_toast("无权限").content == "无权限"
