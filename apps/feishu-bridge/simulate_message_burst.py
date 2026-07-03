#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Simulate a burst of Feishu messages against the REAL processing chain.

T4 seam alignment (docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §3.1):
this used to monkeypatch ``main._process_message`` out entirely, so the debounce
scheduling was exercised but the access gate / mention rule / command parsing / tool
policy / model-runner-parameter path were all skipped. It now routes each synthetic
event through the SAME injection point the live bridge uses —
``main.handle_message_async(event)`` — with the model side supplied by
``rtime_chat_runtime.testing.FakeModelRunner`` (zero network, zero subprocess) and the
Feishu client / session store replaced by in-memory stubs so no card API is called.

What this covers now: gate decision, group @-mention requirement, and the exact
parameters that reach the model runner (model / permission_mode / allowed & disallowed
tools) — the "库scope + 模型选择 + 权限" faces of the harness, for Feishu.

Deferred (documented, not done this round): a fully structured ``OutboundAction`` list
like the QQ seam. Feishu's output layer is card-edit driven (reply_card / update_card /
markdown normalization) and tightly coupled to ``bridge_runner.run_and_display``;
extracting that into channel-neutral structured actions is a larger refactor tracked for
a later T4/T6 stage. Here the stub records the assistant body text instead.

Synthetic events come from ``rtime_chat_runtime.testing.synth.make_feishu_msg`` (the
same shape the SDK delivers).
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

import _shared_runtime  # noqa: E402,F401 — put rtime_chat_runtime on sys.path
import main  # noqa: E402
from rtime_chat_runtime.testing import FakeModelRunner  # noqa: E402
from rtime_chat_runtime.testing.synth import make_feishu_msg  # noqa: E402


class _StubSession:
    def __init__(self, model: str | None, permission_mode: str) -> None:
        self.session_id = None
        self.model = model
        self.cwd = None
        self.permission_mode = permission_mode


class _StubStore:
    """Minimal async session store: enough for handle_message_async + run_and_display."""

    def __init__(self, model: str | None, permission_mode: str) -> None:
        self._session = _StubSession(model, permission_mode)

    async def get_current(self, user_id: str, chat_id: str):
        return self._session

    async def on_claude_response(self, *args, **kwargs) -> None:
        return None

    async def set_permission_mode(self, *args, **kwargs) -> None:
        return None


class _StubFeishu:
    """Records assistant body text; every card/message call is a no-op returning an id.

    run_and_display renders the answer through send_markdown_to_user / reply_markdown /
    send_text_to_user / reply_text; capture those so the simulator still reports a reply.
    """

    def __init__(self) -> None:
        self.replies: list[str] = []

    def _capture(self, text: str) -> str:
        if text:
            self.replies.append(text)
        return "sim_msg_id"

    async def send_card_to_user(self, *args, **kwargs) -> str:
        return "sim_card_id"

    async def reply_card(self, *args, **kwargs) -> str:
        return "sim_card_id"

    async def update_card(self, *args, **kwargs) -> None:
        return None

    async def update_card_with_buttons(self, *args, **kwargs) -> None:
        return None

    async def send_markdown_to_user(self, _open_id, text) -> str:
        return self._capture(text)

    async def reply_markdown(self, _mid, text) -> str:
        return self._capture(text)

    async def send_text_to_user(self, _open_id, text) -> str:
        return self._capture(text)

    async def reply_text(self, _mid, text) -> str:
        return self._capture(text)

    async def send_image_to_user(self, *args, **kwargs) -> str:
        return "sim_img_id"

    async def reply_image(self, *args, **kwargs) -> str:
        return "sim_img_id"

    async def send_file_to_user(self, *args, **kwargs) -> str:
        return "sim_file_id"

    async def reply_file(self, *args, **kwargs) -> str:
        return "sim_file_id"


async def _run(args: argparse.Namespace) -> dict:
    main._chat_locks.clear()
    main._chat_debounce_queues.clear()
    if not args.respect_access:
        main.config.ALLOWED_USERS = {args.user_id}
        main.config.ALLOWED_CHATS = {args.chat_id}
        main.config.ADMIN_USERS = {args.user_id}
    main.config.MESSAGE_DEBOUNCE_SECONDS = args.debounce
    main.config.MESSAGE_DEBOUNCE_MAX_MESSAGES = args.max_messages
    main.config.MESSAGE_DEBOUNCE_MAX_CHARS = args.max_chars

    runner = FakeModelRunner(*(f"simulated reply: {t}" for t in args.message))
    stub_feishu = _StubFeishu()
    stub_store = _StubStore(
        model=args.model or None, permission_mode=args.permission_mode
    )

    saved = (main.run_claude, main.feishu, main.store)
    main.run_claude = runner
    main.feishu = stub_feishu
    main.store = stub_store
    try:
        events = [
            make_feishu_msg(
                text,
                user_id=args.user_id,
                chat_id=args.chat_id,
                message_id=f"msg_{index}",
            )
            for index, text in enumerate(args.message, start=1)
        ]
        await asyncio.gather(*(main.handle_message_async(event) for event in events))
    finally:
        main.run_claude, main.feishu, main.store = saved

    processed = [
        {
            "prompt": call.prompt,
            "model": call.model,
            "permission_mode": call.permission_mode,
            "allowed_tools": call.allowed_tools,
            "disallowed_tools": call.disallowed_tools,
        }
        for call in runner.calls
    ]
    return {
        "input_count": len(args.message),
        "process_count": len(runner.calls),
        "reply_count": len(stub_feishu.replies),
        "debounce_seconds": args.debounce,
        "model_calls": processed,
        "replies": [r[: args.preview_chars] for r in stub_feishu.replies],
    }


def main_cli() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "message",
        nargs="*",
        default=["第一条", "第二条", "第三条"],
        help="messages to simulate in the same private chat",
    )
    parser.add_argument("--debounce", type=float, default=0.05)
    parser.add_argument("--max-messages", type=int, default=20)
    parser.add_argument("--max-chars", type=int, default=12000)
    parser.add_argument("--preview-chars", type=int, default=500)
    parser.add_argument("--user-id", default="user_sim")
    parser.add_argument("--chat-id", default="user_sim")
    parser.add_argument(
        "--model", default="", help="pinned model to assert on the runner call"
    )
    parser.add_argument("--permission-mode", default="bypassPermissions")
    parser.add_argument(
        "--respect-access",
        action="store_true",
        help="use configured access gates instead of the simulated user whitelist",
    )
    result = asyncio.run(_run(parser.parse_args()))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main_cli())
