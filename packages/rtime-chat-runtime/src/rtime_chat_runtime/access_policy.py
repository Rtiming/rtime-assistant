# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Access policy helpers for the Python bridge candidate."""

from __future__ import annotations

from collections.abc import Set


def is_allowed_actor(
    user_id: str,
    chat_id: str,
    is_group: bool,
    allowed_users: Set[str],
    allowed_chats: Set[str],
) -> bool:
    """Return whether an incoming actor/chat is allowed to use the bridge."""
    if not allowed_users and not allowed_chats:
        return True
    if allowed_users and user_id not in allowed_users:
        return False
    if is_group and allowed_chats and chat_id not in allowed_chats:
        return False
    if is_group and not allowed_chats:
        return False
    return True
