# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A1 P1 通道无关归档层:envelope、按日分片、mode、doctor 计数、best-effort。"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path

from rtime_chat_runtime.archive import (
    ShardedArchiveWriter,
    archive_doctor,
    make_envelope,
)


def test_envelope_fields_and_raw_untouched():
    raw = {"post_type": "message", "group_id": 600, "raw_message": "闲聊"}
    env = make_envelope("qq", raw, source_event_key="qq:bot:group:600:1")
    assert env["schema_version"] == 1
    assert env["channel"] == "qq"
    assert env["archive_id"].startswith("rta_raw_")
    assert env["source_event_key"] == "qq:bot:group:600:1"
    assert env["raw"] == raw  # 原样,一个字段不动
    expected = hashlib.sha256(
        json.dumps(raw, ensure_ascii=False, sort_keys=True).encode()
    ).hexdigest()
    assert env["raw_sha256"] == f"sha256:{expected}"


def test_sharded_writer_daily_layout(tmp_path):
    w = ShardedArchiveWriter(str(tmp_path), "qq", mode="events")
    assert w.enabled
    w.append({"post_type": "message", "n": 1})
    w.append({"post_type": "notice", "n": 2})
    files = list(tmp_path.glob("raw/qq/*/*/*/events.jsonl"))
    assert len(files) == 1  # 同一天同一分片
    rows = [json.loads(x) for x in files[0].read_text().splitlines()]
    assert [r["raw"]["n"] for r in rows] == [1, 2]
    # 路径即 UTC 日期
    y, m, d = files[0].parts[-4], files[0].parts[-3], files[0].parts[-2]
    assert rows[0]["received_at"].startswith(f"{y}-{m}-{d}")


def test_mode_off_and_unknown_mode_disable(tmp_path):
    off = ShardedArchiveWriter(str(tmp_path), "qq", mode="off")
    bogus = ShardedArchiveWriter(str(tmp_path), "qq", mode="whatever")
    off.append({"x": 1})
    bogus.append({"x": 1})
    assert not off.enabled and not bogus.enabled
    assert list(tmp_path.glob("raw/**/*.jsonl")) == []


def test_full_mode_writes_raw_layer(tmp_path):
    w = ShardedArchiveWriter(str(tmp_path), "feishu", mode="full")
    w.append({"event": "msg"})
    assert len(list(tmp_path.glob("raw/feishu/*/*/*/events.jsonl"))) == 1


def test_append_never_raises_on_unwritable_root(tmp_path):
    blocked = tmp_path / "blocked"
    blocked.write_text("a file, not a dir")  # makedirs 会炸 → 必须吞掉
    w = ShardedArchiveWriter(str(blocked), "qq", mode="events")
    w.append({"x": 1})  # 不抛即过


def test_doctor_counts_and_malformed(tmp_path):
    w = ShardedArchiveWriter(str(tmp_path), "qq", mode="events")
    w.append({"a": 1})
    w.append({"a": 2})
    ShardedArchiveWriter(str(tmp_path), "feishu", mode="events").append({"b": 1})
    # 手工塞一条坏行
    shard = next(iter(tmp_path.glob("raw/qq/*/*/*/events.jsonl")))
    with open(shard, "a", encoding="utf-8") as fh:
        fh.write("not-json\n")
    report = archive_doctor(str(tmp_path))
    assert report["total_events"] == 3
    assert report["channels"]["qq"]["raw_events"] == 2
    assert report["channels"]["qq"]["malformed"] == 1
    assert report["channels"]["feishu"]["raw_events"] == 1
    assert report["ok"] is False  # 有坏行
    only_feishu = archive_doctor(str(tmp_path), channel="feishu")
    assert list(only_feishu["channels"]) == ["feishu"] and only_feishu["ok"] is True


def test_doctor_empty_root_ok(tmp_path):
    report = archive_doctor(str(tmp_path / "nothing"))
    assert report["ok"] is True and report["total_events"] == 0
