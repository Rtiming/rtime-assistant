# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""通道无关的聊天原始事件归档层(A1 P1;设计 docs/design/chat-archive-storage-2026-07.zh-CN.md)。

三个渠道桥(QQ/飞书/web-chat,未来微信)共用的证据层写入器:平台事件原样进
archive envelope,按日分片落 JSONL。与 ``run_log``(脱敏元数据)职责相反——这里
保全文;所以路径永远在 runtime state(owner-local),不进 git、不进 service log。

布局(设计 §1 Raw Event Log):

    <root>/raw/<channel>/YYYY/MM/DD/events.jsonl

envelope 最小字段:schema_version/archive_id(时间有序)/channel/received_at/
source_event_key/raw_sha256/raw(平台原始 JSON 原样)。

纪律(与 qq_bridge.archive 一脉相承):
- 归档必须在准入/@bot 触发/命令解析/模型调用**之前**由调用方执行;
- best-effort:任何错误吞掉,绝不让归档破坏消息链路;
- mode: off=不落盘;events=raw 层;full=预留(A2 normalized transcript 接上后
  与 events 的差异才生效,当前与 events 同为 raw-only)。

stdlib-only:本包是刻意零依赖的运行时叶子。
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
from datetime import datetime, timezone
from typing import Any

ARCHIVE_SCHEMA_VERSION = 1
ARCHIVE_MODES = ("off", "events", "full")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def new_archive_id(now: datetime | None = None) -> str:
    """时间有序 + 抗碰撞的归档 ID(同秒内靠随机尾;不追求 ULID 依赖)。"""
    ts = (now or _utc_now()).strftime("%Y%m%dT%H%M%S")
    return f"rta_raw_{ts}_{secrets.token_hex(6)}"


def make_envelope(
    channel: str,
    raw: dict[str, Any],
    *,
    source_event_key: str | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """把平台原始事件包进 archive envelope(raw 原样保留,不动一个字段)。"""
    moment = now or _utc_now()
    raw_bytes = json.dumps(raw, ensure_ascii=False, sort_keys=True).encode("utf-8")
    return {
        "schema_version": ARCHIVE_SCHEMA_VERSION,
        "archive_id": new_archive_id(moment),
        "channel": channel,
        "received_at": moment.isoformat(timespec="seconds"),
        "source_event_key": source_event_key or "",
        "raw_sha256": "sha256:" + hashlib.sha256(raw_bytes).hexdigest(),
        "raw": raw,
    }


def make_archive_func(root: str | None, channel: str, mode: str = "events"):
    """非 QQ 渠道的一行接线工厂:root 为空或 mode=off => None(渠道零归档,现状不变);
    否则返回 ``ShardedArchiveWriter(...).append``(best-effort,永不抛)。"""
    if not (root or "").strip():
        return None
    writer = ShardedArchiveWriter(root, channel, mode=mode)
    return writer.append if writer.enabled else None


class ShardedArchiveWriter:
    """按日分片的 envelope 追加器。``append`` 永不抛异常(best-effort 证据层)。"""

    def __init__(self, root: str, channel: str, *, mode: str = "events") -> None:
        self.root = root
        self.channel = channel
        self.mode = mode if mode in ARCHIVE_MODES else "off"

    @property
    def enabled(self) -> bool:
        return bool(self.root) and self.mode != "off"

    def _shard_path(self, moment: datetime) -> str:
        return os.path.join(
            self.root,
            "raw",
            self.channel,
            moment.strftime("%Y"),
            moment.strftime("%m"),
            moment.strftime("%d"),
            "events.jsonl",
        )

    def append(
        self, raw: dict[str, Any], *, source_event_key: str | None = None
    ) -> None:
        if not self.enabled:
            return
        try:
            moment = _utc_now()
            envelope = make_envelope(
                self.channel, raw, source_event_key=source_event_key, now=moment
            )
            path = self._shard_path(moment)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(envelope, ensure_ascii=False) + "\n")
        except Exception:
            pass  # best-effort: never let archiving break the bridge


def archive_doctor(
    root: str, *, channel: str | None = None
) -> dict[str, Any]:
    """无正文计数报告:按 channel/date 数 raw 事件与坏行(设计 §Doctor)。只读。"""
    base = os.path.join(root, "raw")
    channels: dict[str, dict[str, Any]] = {}
    if not os.path.isdir(base):
        return {"ok": True, "root": root, "channels": {}, "total_events": 0}
    total = 0
    for ch in sorted(os.listdir(base)):
        if channel and ch != channel:
            continue
        ch_dir = os.path.join(base, ch)
        if not os.path.isdir(ch_dir):
            continue
        by_date: dict[str, dict[str, int]] = {}
        for dirpath, _dirs, names in os.walk(ch_dir):
            if "events.jsonl" not in names:
                continue
            rel = os.path.relpath(dirpath, ch_dir)  # YYYY/MM/DD
            date = rel.replace(os.sep, "-")
            events = 0
            malformed = 0
            try:
                with open(
                    os.path.join(dirpath, "events.jsonl"), encoding="utf-8"
                ) as fh:
                    for line in fh:
                        if not line.strip():
                            continue
                        try:
                            row = json.loads(line)
                            if not isinstance(row, dict) or "raw" not in row:
                                malformed += 1
                            else:
                                events += 1
                        except ValueError:
                            malformed += 1
            except OSError:
                malformed += 1
            by_date[date] = {"raw_events": events, "malformed": malformed}
            total += events
        channels[ch] = {
            "dates": dict(sorted(by_date.items())),
            "raw_events": sum(d["raw_events"] for d in by_date.values()),
            "malformed": sum(d["malformed"] for d in by_date.values()),
        }
    ok = all(c["malformed"] == 0 for c in channels.values())
    return {"ok": ok, "root": root, "channels": channels, "total_events": total}
