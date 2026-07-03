# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A1 P0 归档覆盖:raw archive 必须先于准入/@bot 触发判定,与"是否回复"完全分离。

设计: docs/design/chat-archive-storage-2026-07.zh-CN.md(处理顺序 §"QQ live path")。
锁定的保证:
  - 群聊未 @bot 的普通闲聊 → 落 raw、不回复、不进模型;
  - blocked 用户、陌生人私聊 → 落 raw、被拒;
  - request/notice → 落 raw;meta_event(心跳) → 不落(_ARCHIVED_POST_TYPES 白名单);
  - 归档在 _dispatch 顶部执行,业务层怎么 return 都丢不了记录。
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from qq_bridge.app import build_pipeline
from qq_bridge.archive import make_archiver
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.ws_server import OneBotWSServer
from rtime_chat_runtime.testing import FakeModelRunner, make_qq_group_at, make_qq_private

BOT = "479"
ADMIN = "111"
GROUP = "600"


def _server(tmp_path, **cfg_kw):
    cfg_kw.setdefault("owner_ids", frozenset({ADMIN}))
    cfg = QQBridgeConfig(
        claude_cli="/x/claude", sessions_dir=str(tmp_path / "sessions"), **cfg_kw
    )
    runner = FakeModelRunner("答案")
    archive_path = tmp_path / "messages.jsonl"
    server = OneBotWSServer(
        host="127.0.0.1",
        port=0,
        path="/onebot",
        access_token=None,
        pipeline=build_pipeline(cfg, model_runner=runner),
        archive=make_archiver(str(archive_path)),
    )
    sent: list[tuple[str, dict]] = []

    async def _record(ws, action, params):
        sent.append((action, params))
        return None

    server.send_action = _record  # type: ignore[method-assign] — 出站动作改记录不发网络
    return server, runner, archive_path, sent


def _dispatch(server, payload):
    async def go():
        await server._dispatch(None, payload)
        if server._handler_tasks:
            await asyncio.gather(*server._handler_tasks)

    asyncio.run(go())


def _lines(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(x) for x in path.read_text(encoding="utf-8").splitlines()]


def test_group_plain_chatter_archived_but_silent(tmp_path):
    # 群聊没 @bot、群也不在白名单:业务层完全沉默,但 raw 必须在
    server, runner, archive, sent = _server(tmp_path)
    event = make_qq_group_at(GROUP, "333", "今晚谁去自习", at_bot=False, self_id=BOT)
    _dispatch(server, event)
    rows = _lines(archive)
    assert len(rows) == 1
    assert rows[0]["group_id"] == int(GROUP)
    assert rows[0]["raw_message"] == "今晚谁去自习"
    assert runner.calls == [] and sent == []


def test_blocked_user_archived_but_rejected(tmp_path):
    server, runner, archive, sent = _server(
        tmp_path,
        open_public=True,
        blocked_users=frozenset({"222"}),
    )
    event = make_qq_group_at(GROUP, "222", "问题", self_id=BOT)
    _dispatch(server, event)
    assert len(_lines(archive)) == 1
    assert runner.calls == [] and sent == []


def test_private_stranger_archived_but_rejected(tmp_path):
    server, runner, archive, sent = _server(tmp_path)
    event = make_qq_private("999", "在吗", self_id=BOT)
    _dispatch(server, event)
    assert len(_lines(archive)) == 1
    assert runner.calls == []


def test_request_and_notice_archived_meta_event_not(tmp_path):
    server, runner, archive, sent = _server(tmp_path)
    _dispatch(server, {"post_type": "request", "request_type": "friend", "user_id": 42})
    _dispatch(server, {"post_type": "notice", "notice_type": "group_increase", "group_id": 1})
    _dispatch(
        server,
        {"post_type": "meta_event", "meta_event_type": "heartbeat", "status": {"online": True}},
    )
    rows = _lines(archive)
    assert [r["post_type"] for r in rows] == ["request", "notice"]


def test_sharded_envelope_mode_via_archive_root(tmp_path):
    # A1 P1:config.archive_root 设置 => build_archive_func 选 envelope 分片归档;
    # 群聊未@bot 闲聊照样先落档(与 legacy 同一 _dispatch 顶部接线)。
    from qq_bridge.archive import build_archive_func

    cfg_kw = dict(owner_ids=frozenset({ADMIN}), archive_root=str(tmp_path / "arch"))
    cfg = QQBridgeConfig(
        claude_cli="/x/claude", sessions_dir=str(tmp_path / "sessions"), **cfg_kw
    )
    runner = FakeModelRunner("答案")
    server = OneBotWSServer(
        host="127.0.0.1",
        port=0,
        path="/onebot",
        access_token=None,
        pipeline=build_pipeline(cfg, model_runner=runner),
        archive=build_archive_func(cfg),
    )
    event = make_qq_group_at(GROUP, "333", "今晚谁去自习", at_bot=False, self_id=BOT)
    _dispatch(server, event)
    shards = list((tmp_path / "arch").glob("raw/qq/*/*/*/events.jsonl"))
    assert len(shards) == 1
    row = json.loads(shards[0].read_text().splitlines()[0])
    assert row["channel"] == "qq" and row["schema_version"] == 1
    assert row["raw"]["group_id"] == int(GROUP)
    assert row["raw"]["raw_message"] == "今晚谁去自习"
    assert runner.calls == []


def test_archive_root_with_mode_off_disables_completely(tmp_path):
    from qq_bridge.archive import build_archive_func

    cfg = QQBridgeConfig(
        claude_cli="/x/claude",
        sessions_dir=str(tmp_path / "sessions"),
        owner_ids=frozenset({ADMIN}),
        archive_root=str(tmp_path / "arch"),
        archive_mode="off",
        archive_path=str(tmp_path / "legacy.jsonl"),
    )
    # 显式 off:不回落 legacy(off 是明确关,不是"换一种存")
    assert build_archive_func(cfg) is None


def test_archive_survives_handler_explosion(tmp_path):
    # 归档在 _dispatch 顶部:处理链爆炸也丢不了记录(证据层与业务层分离)
    server, runner, archive, sent = _server(tmp_path)

    async def boom(*a, **kw):
        raise RuntimeError("handler exploded")

    server.pipeline.process_event = boom  # type: ignore[method-assign]
    event = make_qq_group_at(GROUP, "333", "闲聊", at_bot=False, self_id=BOT)

    async def go():
        await server._dispatch(None, event)
        for t in list(server._handler_tasks):
            try:
                await t
            except RuntimeError:
                pass

    asyncio.run(go())
    assert len(_lines(archive)) == 1


def test_replayed_message_archived_but_not_processed(tmp_path):
    server, runner, archive, sent = _server(tmp_path, replay_grace_seconds=5.0)
    event = make_qq_private(ADMIN, "离线期间的旧消息", self_id=BOT)
    event["time"] = 100.0

    async def go():
        await server._dispatch(None, event, connection_started_at=120.0)
        if server._handler_tasks:
            await asyncio.gather(*server._handler_tasks)

    asyncio.run(go())
    assert len(_lines(archive)) == 1
    assert runner.calls == [] and sent == []


def test_fresh_message_after_reconnect_is_processed(tmp_path):
    server, runner, archive, sent = _server(tmp_path, replay_grace_seconds=5.0)
    event = make_qq_private(ADMIN, "重新连上后的新消息", self_id=BOT)
    event["time"] = 119.0

    async def go():
        await server._dispatch(None, event, connection_started_at=120.0)
        if server._handler_tasks:
            await asyncio.gather(*server._handler_tasks)

    asyncio.run(go())
    assert len(_lines(archive)) == 1
    assert len(runner.calls) == 1
    assert sent


def test_outbound_action_archived(tmp_path):
    """A2 出站捕获:send_action 发出后落 raw(post_type=rtime_outbound),可与入站关联。"""
    server, runner, archive, sent = _server(tmp_path)

    class _WS:
        async def send_str(self, _s):
            pass

    async def go():
        # _server 为隔离测试把实例上的 send_action 换成了记录器;这里显式走类上的
        # 真方法,验证真实出站路径的归档行为。
        await OneBotWSServer.send_action(
            server, _WS(), "send_group_msg", {"group_id": 600, "message": "答案"}
        )

    asyncio.run(go())
    rows = _lines(archive)
    assert len(rows) == 1
    assert rows[0]["post_type"] == "rtime_outbound"
    assert rows[0]["action"] == "send_group_msg"
    assert rows[0]["params"]["message"] == "答案"
    assert rows[0]["sent_at"] > 0
