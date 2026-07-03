# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A3 决策3:管理员通知分发器(通道无关,best-effort,未配置=no-op)。"""

from __future__ import annotations

import json

from rtime_chat_runtime import admin_notify


def test_no_channel_is_noop():
    r = admin_notify.notify_admin("有事", channels=[])
    assert r["no_channel"] is True and r["ok"] is False and r["delivered"] == 0


def test_load_channels_bad_json_and_filtering():
    assert admin_notify.load_channels("not json") == []
    assert admin_notify.load_channels("") == []
    # 未知 type 被过滤;单对象容忍
    chans = admin_notify.load_channels(json.dumps({"type": "feishu_webhook", "url": "http://x"}))
    assert len(chans) == 1
    chans2 = admin_notify.load_channels(json.dumps([{"type": "bogus"}, {"type": "email", "host": "h", "to": "a@b"}]))
    assert [c["type"] for c in chans2] == ["email"]


def test_feishu_webhook_dispatch(monkeypatch):
    sent = {}

    def fake_post(url, payload, timeout=8):
        sent["url"] = url
        sent["payload"] = payload
        return True, "http 200: ok"

    monkeypatch.setattr(admin_notify, "_post_json", fake_post)
    r = admin_notify.notify_admin(
        "同学反复问社团注册,库里查不到",
        reason="答不上",
        urgency="high",
        source="studentunion-qq",
        channels=[{"type": "feishu_webhook", "url": "https://open.feishu.cn/hook/x"}],
    )
    assert r["ok"] and r["delivered"] == 1
    assert sent["url"].endswith("/hook/x")
    text = sent["payload"]["content"]["text"]
    assert "🔴" in text  # high
    assert "答不上" in text and "社团注册" in text and "studentunion-qq" in text


def test_best_effort_one_fails_other_ok(monkeypatch):
    def fake_post(url, payload, timeout=8):
        return (False, "boom") if "bad" in url else (True, "ok")

    monkeypatch.setattr(admin_notify, "_post_json", fake_post)
    r = admin_notify.notify_admin(
        "x",
        channels=[
            {"type": "feishu_webhook", "url": "http://bad"},
            {"type": "webhook", "url": "http://good"},
        ],
    )
    assert r["delivered"] == 1 and r["ok"] is True
    assert {x["type"]: x["ok"] for x in r["results"]} == {"feishu_webhook": False, "webhook": True}


def test_qq_channel_reports_not_wired():
    r = admin_notify.notify_admin("x", channels=[{"type": "qq", "admin": "123"}])
    assert r["ok"] is False
    assert "not wired" in r["results"][0]["detail"]


def test_email_missing_config():
    r = admin_notify.notify_admin("x", channels=[{"type": "email"}])
    assert r["ok"] is False and "missing" in r["results"][0]["detail"]


def test_feishu_selfheal_queues_file(tmp_path):
    qdir = tmp_path / "notify-queue"
    r = admin_notify.notify_admin(
        "有同学连续问社团注册,查不到",
        reason="答不上",
        urgency="high",
        source="studentunion-qq",
        channels=[{"type": "feishu_selfheal", "queue_dir": str(qdir)}],
    )
    assert r["ok"] and r["delivered"] == 1
    files = list(qdir.glob("*.json"))
    assert len(files) == 1
    import json as _json
    payload = _json.loads(files[0].read_text(encoding="utf-8"))
    assert "社团注册" in payload["text"] and "🔴" in payload["text"]
    # 原子写:不留 .tmp
    assert not list(qdir.glob(".*.tmp"))
