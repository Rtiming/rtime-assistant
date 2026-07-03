# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Server-side CLI handover handling for the Python bridge candidate."""

from __future__ import annotations


async def handle_handover_request(
    store,
    feishu,
    session_id: str,
    cwd: str,
    model: str,
    target_user: str = "",
    target_chat: str = "",
) -> dict:
    """Switch the Feishu current session and notify the target user."""
    user_id = target_user or store.find_primary_user()
    if not user_id:
        return {"ok": False, "error": "no user found in sessions, pass user_id param"}

    chat_id = target_chat or user_id
    result = await store.handover_session(user_id, chat_id, session_id, cwd=cwd, model=model)

    cur = await store.get_current_raw(user_id, chat_id)
    display_cwd = cur.get("cwd", "~")
    display_model = cur.get("model", "unknown")
    display_mode = cur.get("permission_mode", "bypassPermissions")
    old_summary = result.get("old_summary", "")
    old_note = f"\n上个会话：「{old_summary}」" if old_summary else ""

    notify_text = (
        f"**CLI 会话已接入**\n"
        f"Session: `{session_id[:12]}...`\n"
        f"目录: `{display_cwd}`\n"
        f"模型: `{display_model}`\n"
        f"模式: `{display_mode}`{old_note}\n\n"
        f"直接发消息即可继续对话。"
    )

    try:
        await feishu.send_card_to_user(user_id, content=notify_text, loading=False)
    except Exception as exc:
        print(f"[handover] 推送通知失败: {exc}", flush=True)

    print(f"[handover] session={session_id[:8]}... cwd={display_cwd}", flush=True)
    return {"ok": True, "user_id": user_id, "session_id": session_id}
