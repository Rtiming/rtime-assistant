# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared helpers for Feishu card callback actions."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CardAction:
    action_type: str
    user_id: str
    chat_id: str
    clicked_msg_id: str
    mode: str = ""
    cmd_text: str = ""
    session_id: str = ""
    reply_text: str = ""


@dataclass(frozen=True)
class Toast:
    type: str
    content: str


def parse_card_action(
    user_id: str,
    value: dict,
    clicked_msg_id: str = "",
) -> CardAction:
    action_type = value.get("action", "")
    chat_id = value.get("cid", user_id)
    return CardAction(
        action_type=action_type,
        user_id=user_id,
        chat_id=chat_id,
        clicked_msg_id=clicked_msg_id or "",
        mode=value.get("mode", ""),
        cmd_text=value.get("cmd", ""),
        session_id=value.get("sid", ""),
        reply_text=value.get("reply", ""),
    )


def toast_for_action(action: CardAction) -> Toast:
    if action.action_type == "set_mode":
        return Toast("success", f"已切换: {action.mode}")
    if action.action_type == "run_cmd":
        return Toast("info", action.cmd_text)
    if action.action_type == "resume_session":
        return Toast("info", "正在恢复...")
    return Toast("info", f"已发送: {action.reply_text}")


def warning_toast(content: str) -> Toast:
    return Toast("warning", content)
