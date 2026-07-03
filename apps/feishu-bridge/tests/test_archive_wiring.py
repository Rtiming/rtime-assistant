# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A1.5 飞书桥接通道无关归档层(spec-a15-archive-wiring §五)。

on_message_receive 顶部归档:入站事件在调度业务协程**之前**落 raw/feishu 分片;
序列化失败退化为最小元数据、绝不抛;root 未配置(默认)=零归档零行为变化。
"""

import asyncio
import json

import _shared_runtime  # noqa: F401
import main
from main import P2ImMessageReceiveV1
from rtime_chat_runtime.archive import make_archive_func


def _drain_bot_loop():
    """等 on_message_receive 调度的协程在 _bot_loop 上跑完。"""
    asyncio.run_coroutine_threadsafe(asyncio.sleep(0), main._bot_loop).result(timeout=5)


def test_inbound_event_archived_before_handling(tmp_path, monkeypatch):
    calls = []

    async def fake_handle(_data):
        calls.append(1)

    monkeypatch.setattr(main, "handle_message_async", fake_handle)
    monkeypatch.setattr(
        main, "_archive", make_archive_func(str(tmp_path), "feishu", "events")
    )
    main.on_message_receive(P2ImMessageReceiveV1())
    _drain_bot_loop()
    shards = list(tmp_path.glob("raw/feishu/*/*/*/events.jsonl"))
    assert len(shards) == 1
    row = json.loads(shards[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["channel"] == "feishu" and row["schema_version"] == 1
    assert calls == [1]  # 业务协程照常跑(归档不拦路)


def test_unserializable_event_degrades_not_raises(tmp_path, monkeypatch):
    # lark.JSON.marshal 对多数对象宽容;确定性地让它抛,验证 _event_raw 的降级路径。
    async def fake_handle(_data):
        pass

    monkeypatch.setattr(main, "handle_message_async", fake_handle)
    monkeypatch.setattr(
        main, "_archive", make_archive_func(str(tmp_path), "feishu", "events")
    )
    monkeypatch.setattr(
        main.lark.JSON, "marshal", staticmethod(lambda *_a, **_k: (_ for _ in ()).throw(TypeError("boom")))
    )
    main.on_message_receive(P2ImMessageReceiveV1())  # 不抛即基本合格
    _drain_bot_loop()
    shards = list(tmp_path.glob("raw/feishu/*/*/*/events.jsonl"))
    assert len(shards) == 1
    row = json.loads(shards[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["raw"].get("unserializable") == "P2ImMessageReceiveV1"


def test_no_archive_configured_is_noop(tmp_path, monkeypatch):
    async def fake_handle(_data):
        pass

    monkeypatch.setattr(main, "handle_message_async", fake_handle)
    monkeypatch.setattr(main, "_archive", None)  # 默认形态
    main.on_message_receive(P2ImMessageReceiveV1())
    _drain_bot_loop()
    assert list(tmp_path.rglob("events.jsonl")) == []
