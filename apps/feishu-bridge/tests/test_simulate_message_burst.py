# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Feishu simulation-seam tests.

``simulate_message_burst`` now routes synthetic events through the REAL
``main.handle_message_async`` injection point with a ``FakeModelRunner`` and stub
Feishu client/store, so these assert on the gate decision + the parameters that
reach the model runner (the profile-independent 权限/模型/工具 faces for Feishu).
"""

from __future__ import annotations

import argparse
import asyncio

import simulate_message_burst


def _args(**kw) -> argparse.Namespace:
    base = dict(
        message=["第一条"],
        debounce=0.0,
        max_messages=20,
        max_chars=12000,
        preview_chars=80,
        user_id="user_sim",
        chat_id="user_sim",
        model="",
        permission_mode="bypassPermissions",
        respect_access=False,
    )
    base.update(kw)
    return argparse.Namespace(**base)


def test_simulated_message_reaches_the_model_and_replies():
    payload = asyncio.run(simulate_message_burst._run(_args()))
    assert payload["input_count"] == 1
    assert payload["process_count"] == 1  # the model runner was actually invoked
    assert payload["reply_count"] == 1
    assert payload["replies"][0].startswith("simulated reply:")


def test_runner_receives_pinned_model_and_permission_mode():
    payload = asyncio.run(
        simulate_message_burst._run(_args(model="ds", permission_mode="dontAsk"))
    )
    call = payload["model_calls"][0]
    assert call["model"] == "ds"
    assert call["permission_mode"] == "dontAsk"
    # Cron tools are always denied via the shared tool policy.
    assert "CronCreate" in call["disallowed_tools"]


def test_blocked_actor_never_reaches_the_model():
    # respect_access=True keeps the configured gate; a stranger not in ALLOWED_USERS
    # must be dropped before the runner is touched.
    import main

    main.config.ALLOWED_USERS = {"someone_else"}
    main.config.ALLOWED_CHATS = set()
    payload = asyncio.run(
        simulate_message_burst._run(_args(user_id="stranger", respect_access=True))
    )
    assert payload["process_count"] == 0
    assert payload["reply_count"] == 0
