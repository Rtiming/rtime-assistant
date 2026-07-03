# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from unittest.mock import AsyncMock, MagicMock

import pytest

from handover_service import handle_handover_request


@pytest.mark.asyncio
async def test_handover_returns_error_without_user():
    store = MagicMock()
    store.find_primary_user.return_value = ""

    result = await handle_handover_request(store, AsyncMock(), "sid_123", "/tmp", "model")

    assert result == {"ok": False, "error": "no user found in sessions, pass user_id param"}


@pytest.mark.asyncio
async def test_handover_switches_session_and_notifies_user():
    store = MagicMock()
    store.find_primary_user.return_value = "user_001"
    store.handover_session = AsyncMock(return_value={"old_summary": "old task"})
    store.get_current_raw = AsyncMock(
        return_value={
            "cwd": "/workspace",
            "model": "kimi",
            "permission_mode": "default",
        }
    )
    feishu = AsyncMock()

    result = await handle_handover_request(store, feishu, "sid_1234567890", "/workspace", "kimi")

    assert result == {"ok": True, "user_id": "user_001", "session_id": "sid_1234567890"}
    store.handover_session.assert_awaited_once_with(
        "user_001",
        "user_001",
        "sid_1234567890",
        cwd="/workspace",
        model="kimi",
    )
    feishu.send_card_to_user.assert_awaited_once()
