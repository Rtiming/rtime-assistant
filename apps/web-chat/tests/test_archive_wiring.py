# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A1.5 web-chat 接通道无关归档层(spec-a15-archive-wiring §五)。

归档先于一切业务判定:一次 /api/chat POST → raw/web 分片一行(body 原样);
root 未配置 => 零归档,行为与现状完全一致。
"""

from __future__ import annotations

import json

from conftest import make_config, post_chat
from test_chat_stream import make_fake_run


def test_chat_post_archived(tmp_path, live_server):
    arch = tmp_path / "arch"
    cfg = make_config(tmp_path, archive_root=str(arch))
    base = live_server(cfg, fake_run=make_fake_run())
    status, _events = post_chat(base, {"message": "你好,归档测试", "profile": "studentunion"})
    assert status == 200
    shards = list(arch.glob("raw/web/*/*/*/events.jsonl"))
    assert len(shards) == 1
    row = json.loads(shards[0].read_text(encoding="utf-8").splitlines()[0])
    assert row["channel"] == "web" and row["schema_version"] == 1
    assert row["raw"]["endpoint"] == "/api/chat"
    assert row["raw"]["body"]["message"] == "你好,归档测试"


def test_invalid_request_still_archived(tmp_path, live_server):
    # 归档在校验之前:空 message 被 400 拒,但请求已入证据层(与"归档≠回复"合同一致)。
    arch = tmp_path / "arch"
    cfg = make_config(tmp_path, archive_root=str(arch))
    base = live_server(cfg, fake_run=make_fake_run())
    status, _ = post_chat(base, {"message": "   "})
    assert status == 400
    assert len(list(arch.glob("raw/web/*/*/*/events.jsonl"))) == 1


def test_no_root_no_archive(tmp_path, live_server):
    cfg = make_config(tmp_path)  # archive_root=None
    base = live_server(cfg, fake_run=make_fake_run())
    status, _ = post_chat(base, {"message": "无归档", "profile": "studentunion"})
    assert status == 200
    assert list(tmp_path.rglob("events.jsonl")) == []
