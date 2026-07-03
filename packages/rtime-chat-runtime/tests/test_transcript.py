# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A2 normalized transcript(spec-a2 §五):字段映射、幂等、legacy↔envelope 同构、
明文 QQ 号否定断言、坏行计数、出站归一。"""

from __future__ import annotations

import json

from rtime_chat_runtime.archive import ShardedArchiveWriter
from rtime_chat_runtime.testing import make_qq_group_at, make_qq_private
from rtime_chat_runtime.transcript import build_transcript, normalize_qq_event

BOT = "479"
USER = "2000000001"  # 真实形状的假 QQ 号,专门用于"不落明文"断言
GROUP = "100000001"  # 假群号:夹具不用真实群


def test_group_not_at_bot_fields():
    raw = make_qq_group_at(GROUP, USER, "今晚谁去自习", at_bot=False, self_id=BOT)
    ev = normalize_qq_event(raw)
    assert ev["event_type"] == "message"
    assert ev["message_class"] == "user_text"
    assert ev["chat_type"] == "group" and ev["is_group"] is True
    assert ev["direction"] == "inbound"
    assert ev["model_triggered"] is False and ev["trigger_reason"] == "none"
    assert ev["text"] == "今晚谁去自习"
    assert ev["sent_at"] and ev["sent_at"].endswith("+00:00")
    assert ev["event_id"].startswith("rta_evt_")


def test_group_at_bot_mentions_hashed():
    raw = make_qq_group_at(GROUP, USER, "班车几点", at_bot=True, self_id=BOT)
    ev = normalize_qq_event(raw)
    assert len(ev["mentions"]) == 1
    blob = json.dumps(ev, ensure_ascii=False)
    assert USER not in blob and GROUP not in blob  # 明文号绝不落 normalized


def test_private_friend_vs_temporary():
    friend = normalize_qq_event(make_qq_private(USER, "在吗", self_id=BOT))
    assert friend["chat_type"] == "private" and friend["is_group"] is False
    temp_raw = make_qq_private(USER, "在吗", self_id=BOT)
    temp_raw["sub_type"] = "group"
    temp = normalize_qq_event(temp_raw)
    assert temp["chat_type"] == "temporary"


def test_command_and_media_classes():
    cmd = normalize_qq_event(make_qq_private(USER, "/help", self_id=BOT))
    assert cmd["message_class"] == "command"
    img_raw = make_qq_group_at(
        GROUP, USER, "", at_bot=False, self_id=BOT,
        extra_segments=[{"type": "image", "data": {"url": "https://x/img"}}],
    )
    assert normalize_qq_event(img_raw)["message_class"] == "media_only"


def test_notice_request_and_unknown():
    notice = normalize_qq_event(
        {"post_type": "notice", "notice_type": "group_increase", "group_id": 1, "time": 1783070000}
    )
    assert notice["event_type"] == "notice" and notice["message_class"] == "system_notice"
    request = normalize_qq_event(
        {"post_type": "request", "request_type": "friend", "user_id": 42, "time": 1783070000}
    )
    assert request["event_type"] == "request" and request["chat_type"] == "private"
    assert normalize_qq_event({"no_post_type": 1}) is None


def test_outbound_normalization():
    raw = {
        "post_type": "rtime_outbound",
        "action": "send_group_msg",
        "params": {"group_id": int(GROUP), "message": f"[CQ:at,qq={USER}] 答案在此"},
        "echo": "qqbr-7",
        "sent_at": 1783070001.5,
    }
    ev = normalize_qq_event(raw)
    assert ev["direction"] == "outbound"
    assert ev["message_class"] == "bot_reply"
    assert ev["chat_type"] == "group"
    assert ev["sender_id_hash"] is None
    assert "答案在此" in ev["text"]


def test_legacy_and_envelope_same_event_identity(tmp_path):
    """同一条 raw:legacy 裸行与 envelope 包裹 → 同一 event_id(内容寻址);
    除 raw_archive_id/received_at 外字段同构。"""
    raw = make_qq_group_at(GROUP, USER, "同一条", at_bot=False, self_id=BOT)
    bare = normalize_qq_event(raw)
    w = ShardedArchiveWriter(str(tmp_path), "qq", mode="events")
    w.append(raw)
    shard = next(iter(tmp_path.glob("raw/qq/*/*/*/events.jsonl")))
    envelope = json.loads(shard.read_text(encoding="utf-8").splitlines()[0])
    wrapped = normalize_qq_event(envelope["raw"], envelope=envelope)
    assert bare["event_id"] == wrapped["event_id"]
    assert wrapped["raw_archive_id"].startswith("rta_raw_")
    for key in ("chat_id_hash", "text", "message_class", "sent_at"):
        assert bare[key] == wrapped[key]


def test_build_transcript_idempotent_two_sources(tmp_path):
    # 源1:legacy 平铺(两条);源2:envelope 分片,其中一条与 legacy 重复。
    legacy = tmp_path / "messages.jsonl"
    e1 = make_qq_group_at(GROUP, USER, "第一条", at_bot=False, self_id=BOT)
    e2 = make_qq_private(USER, "第二条", self_id=BOT, message_id=2002)
    legacy.write_text(
        json.dumps(e1, ensure_ascii=False) + "\n" + json.dumps(e2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    arch = tmp_path / "arch"
    w = ShardedArchiveWriter(str(arch), "qq", mode="events")
    w.append(e1)  # 与 legacy 重复
    e3 = make_qq_group_at(GROUP, "333", "第三条", at_bot=True, self_id=BOT, message_id=3003)
    w.append(e3)

    out = tmp_path / "out"
    r1 = build_transcript([str(legacy), str(arch)], str(out))
    assert r1["events_written"] == 3
    assert r1["deduped"] == 1  # 跨源重复被内容寻址挡掉
    assert r1["malformed"] == 0
    assert r1["by_direction"] == {"inbound": 3}

    # 第二遍:全 dedup,分片内容不变(幂等)。
    shards = sorted(out.rglob("events.jsonl"))
    before = [s.read_text(encoding="utf-8") for s in shards]
    r2 = build_transcript([str(legacy), str(arch)], str(out))
    # deduped 计的是被跳过的输入行:4 行(3 唯一事件 + 1 跨源重复)全部命中已有
    assert r2["events_written"] == 0 and r2["deduped"] == 4
    assert [s.read_text(encoding="utf-8") for s in sorted(out.rglob("events.jsonl"))] == before


def test_malformed_lines_counted_not_fatal(tmp_path):
    src = tmp_path / "bad.jsonl"
    good = make_qq_private(USER, "好行", self_id=BOT)
    src.write_text(
        "not-json\n" + json.dumps(good, ensure_ascii=False) + "\n" + '{"no_post_type":1}\n',
        encoding="utf-8",
    )
    report = build_transcript([str(src)], str(tmp_path / "out"))
    assert report["events_written"] == 1
    assert report["malformed"] == 2


def test_report_contains_no_plaintext_ids(tmp_path):
    src = tmp_path / "m.jsonl"
    src.write_text(
        json.dumps(make_qq_private(USER, "隐私", self_id=BOT), ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    report = build_transcript([str(src)], str(tmp_path / "out"))
    blob = json.dumps(report, ensure_ascii=False)
    assert USER not in blob and "隐私" not in blob  # 报告无正文无明文号
