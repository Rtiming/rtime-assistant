# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Group-join gate (invite reject / auto-leave) and the chat archiver."""

import asyncio
import json

from qq_bridge.app import build_notice_handler, build_request_handler
from qq_bridge.archive import make_archiver
from qq_bridge.config import QQBridgeConfig


def _run(coro):
    return asyncio.run(coro)


def _recorder():
    calls: list[tuple[str, dict]] = []

    async def call_action(action, params):
        calls.append((action, params))

    return calls, call_action


def test_invite_rejected_by_default():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}), group_invite_policy="reject")
    calls, ca = _recorder()
    _run(
        build_request_handler(cfg)(
            {
                "request_type": "group",
                "sub_type": "invite",
                "user_id": 999,
                "group_id": 555,
                "flag": "f1",
            },
            ca,
        )
    )
    assert calls == [
        (
            "set_group_add_request",
            {
                "flag": "f1",
                "sub_type": "invite",
                "approve": False,
                "reason": "bot 不自动入群",
            },
        )
    ]


def test_invite_owner_policy_approves_owner():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}), group_invite_policy="owner")
    calls, ca = _recorder()
    _run(
        build_request_handler(cfg)(
            {
                "request_type": "group",
                "sub_type": "invite",
                "user_id": 111,
                "group_id": 555,
                "flag": "f2",
            },
            ca,
        )
    )
    assert calls[0][1]["approve"] is True


def test_invite_owner_policy_rejects_stranger():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}), group_invite_policy="owner")
    calls, ca = _recorder()
    _run(
        build_request_handler(cfg)(
            {
                "request_type": "group",
                "sub_type": "invite",
                "user_id": 222,
                "group_id": 555,
                "flag": "f3",
            },
            ca,
        )
    )
    assert calls[0][1]["approve"] is False


def test_non_group_request_ignored():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}))
    calls, ca = _recorder()
    _run(build_request_handler(cfg)({"request_type": "friend", "flag": "x"}, ca))
    assert calls == []


def test_notice_autoleaves_non_allowlisted_group():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}), group_allowlist=frozenset())
    calls, ca = _recorder()
    _run(
        build_notice_handler(cfg)(
            {
                "notice_type": "group_increase",
                "self_id": 479,
                "user_id": 479,
                "group_id": 600,
            },
            ca,
        )
    )
    assert calls == [("set_group_leave", {"group_id": 600})]


def test_notice_keeps_allowlisted_group():
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}), group_allowlist=frozenset({"600"})
    )
    calls, ca = _recorder()
    _run(
        build_notice_handler(cfg)(
            {
                "notice_type": "group_increase",
                "self_id": 479,
                "user_id": 479,
                "group_id": 600,
            },
            ca,
        )
    )
    assert calls == []


def test_notice_ignores_other_user_join():
    cfg = QQBridgeConfig(owner_ids=frozenset({"111"}))
    calls, ca = _recorder()
    _run(
        build_notice_handler(cfg)(
            {
                "notice_type": "group_increase",
                "self_id": 479,
                "user_id": 888,
                "group_id": 600,
            },
            ca,
        )
    )
    assert calls == []


def test_archiver_writes_jsonl(tmp_path):
    p = tmp_path / "arch.jsonl"
    arch = make_archiver(str(p))
    assert arch is not None
    arch({"post_type": "message", "message": "你好"})
    arch({"post_type": "request"})
    lines = p.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["message"] == "你好"


def test_archiver_disabled_returns_none():
    assert make_archiver(None) is None
    assert make_archiver("") is None
