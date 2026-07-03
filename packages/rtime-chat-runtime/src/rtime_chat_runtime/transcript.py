# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A2 normalized transcript:把 raw 归档(legacy 平铺 + envelope 分片)归一成跨平台事件层。

设计: docs/design/chat-archive-storage-2026-07.zh-CN.md §2(normalized 层)/§分类字典;
规格: docs/specs/spec-a2-normalized-transcript.zh-CN.md。

离线批处理工具(不在消息热路径上):可重复跑、幂等——event_id 是**内容寻址**的
(sha256(canonical raw)),同一条平台事件不管来自 legacy 平铺、envelope 分片还是重
复重放,归一后永远是同一条(此处有意修正规格初稿"envelope 用 archive_id 派生"的
写法:archive_id 每次 append 都新,按它去重会漏;raw 内容才是事件身份)。

隐私口径:normalized 层**不落明文 QQ 号**——chat/sender/mentions 全部 sha256 hash,
display 只留群名片/昵称;正文 text/segments 保留(这是 owner-local 证据层,与 raw
同级,永不进 git/日志)。

输出布局: <out_root>/transcript/<channel>/YYYY/MM/DD/events.jsonl(日期取 sent_at)。

用法:
    python -m rtime_chat_runtime.transcript <src...> --out <root> [--channel qq]
src 可以是 legacy 平铺 jsonl 文件,也可以是 envelope 分片根目录(自动 rglob)。
"""

from __future__ import annotations

import argparse
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

TRANSCRIPT_SCHEMA_VERSION = 1


def _sha(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _canonical(raw: dict[str, Any]) -> str:
    return json.dumps(raw, ensure_ascii=False, sort_keys=True)


def _iso(ts: Any) -> str | None:
    try:
        return datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat(
            timespec="seconds"
        )
    except (TypeError, ValueError, OSError, OverflowError):
        return None


def _segments(raw: dict[str, Any]) -> list[dict[str, Any]]:
    seg = raw.get("message")
    return [s for s in seg if isinstance(s, dict)] if isinstance(seg, list) else []


def _seg_types(segments: list[dict[str, Any]]) -> set[str]:
    return {str(s.get("type", "")) for s in segments}


def _text_of(raw: dict[str, Any], segments: list[dict[str, Any]]) -> str:
    parts = [
        str((s.get("data") or {}).get("text", ""))
        for s in segments
        if s.get("type") == "text"
    ]
    if parts:
        return "".join(parts).strip()
    rm = raw.get("raw_message")
    return str(rm).strip() if isinstance(rm, str) else ""


def _message_class(text: str, types: set[str]) -> str:
    media = types & {"image", "record", "video", "file", "face"}
    if not media:
        if text.startswith("/"):
            return "command"
        return "user_text" if text else "unknown"
    if "text" in types and text:
        return "mixed_media"
    if types & {"record"}:
        return "voice"
    if types & {"file"}:
        return "file"
    if types == {"face"} or types == {"image", "face"} or types == {"face", "at"}:
        return "sticker"
    return "media_only"


def _outbound_text(params: dict[str, Any]) -> str:
    msg = params.get("message")
    if isinstance(msg, str):
        return msg.strip()
    if isinstance(msg, list):
        return "".join(
            str((s.get("data") or {}).get("text", ""))
            for s in msg
            if isinstance(s, dict) and s.get("type") == "text"
        ).strip()
    return ""


def normalize_qq_event(
    raw: dict[str, Any], *, envelope: dict[str, Any] | None = None
) -> dict[str, Any] | None:
    """一条 OneBot 事件(或 rtime_outbound 出站记录)→ normalized event。

    认不出形状(无 post_type)返回 None(调用方计 malformed)。纯函数,不做 IO。
    """
    post_type = raw.get("post_type")
    if not isinstance(post_type, str) or not post_type:
        return None
    event_id = "rta_evt_" + _sha(_canonical(raw))[:24]
    outbound = post_type == "rtime_outbound"

    if outbound:
        params = raw.get("params") if isinstance(raw.get("params"), dict) else {}
        group_id = params.get("group_id")
        user_id = params.get("user_id")
        text = _outbound_text(params)
        segments: list[dict[str, Any]] = []
        event_type = "message"
        message_class = "bot_reply"
        sent_at = _iso(raw.get("sent_at"))
        sender_hash = None
        sender_display = ""
        source_message_id = None
        sub_type = ""
    else:
        group_id = raw.get("group_id")
        user_id = raw.get("user_id")
        segments = _segments(raw)
        text = _text_of(raw, segments)
        event_type = post_type if post_type in ("message", "notice", "request") else "system"
        types = _seg_types(segments)
        message_class = (
            _message_class(text, types) if event_type == "message" else "system_notice"
        )
        sent_at = _iso(raw.get("time"))
        sender = raw.get("sender") if isinstance(raw.get("sender"), dict) else {}
        sender_hash = _sha(f"qq:{user_id}") if user_id is not None else None
        sender_display = str(sender.get("card") or sender.get("nickname") or "")
        mid = raw.get("message_id")
        source_message_id = str(mid) if mid is not None else None
        sub_type = str(raw.get("sub_type", ""))

    if group_id is not None:
        chat_type = "group"
        chat_key = f"qq:g:{group_id}"
    elif user_id is not None:
        # OneBot 私聊 sub_type: friend=好友, group=群临时会话, other=其他
        chat_type = "temporary" if (not outbound and sub_type == "group") else "private"
        chat_key = f"qq:p:{user_id}"
    else:
        chat_type = "system"
        chat_key = "qq:system"

    mentions = [
        _sha("qq:" + str((s.get("data") or {}).get("qq", "")))
        for s in segments
        if s.get("type") == "at" and (s.get("data") or {}).get("qq")
    ]

    return {
        "schema_version": TRANSCRIPT_SCHEMA_VERSION,
        "event_id": event_id,
        "raw_archive_id": (envelope or {}).get("archive_id"),
        "channel": "qq",
        "direction": "outbound" if outbound else "inbound",
        "event_type": event_type,
        "message_class": message_class,
        "chat_id_hash": _sha(chat_key),
        "chat_type": chat_type,
        "is_group": chat_type == "group",
        "sender_id_hash": sender_hash,
        "sender_display": sender_display,
        "sent_at": sent_at,
        "received_at": (envelope or {}).get("received_at"),
        "source_message_id": source_message_id,
        "text": text,
        "segments": segments,
        "mentions": mentions,
        "media_refs": [],
        "reply_to": None,
        "thread_root": None,
        "model_triggered": False,
        "trigger_reason": "none",
        "access_outcome": "archived",
        "run_id": None,
        "session_id": None,
        "model": None,
        "status": "archived",
    }


# --------------------------------------------------------------------------- batch
def _iter_source_lines(source: Path) -> Iterable[tuple[str, str]]:
    """(来源文件, 行) 序列;目录=envelope 分片根(rglob events.jsonl),文件=按行。"""
    if source.is_dir():
        for shard in sorted(source.rglob("events.jsonl")):
            for line in shard.read_text(encoding="utf-8").splitlines():
                yield str(shard), line
    elif source.is_file():
        for line in source.read_text(encoding="utf-8").splitlines():
            yield str(source), line


def _existing_event_ids(out_root: Path, channel: str) -> set[str]:
    seen: set[str] = set()
    base = out_root / "transcript" / channel
    if not base.is_dir():
        return seen
    for shard in base.rglob("events.jsonl"):
        for line in shard.read_text(encoding="utf-8").splitlines():
            try:
                seen.add(json.loads(line)["event_id"])
            except (ValueError, KeyError, TypeError):
                continue
    return seen


def build_transcript(
    sources: list[str], out_root: str, *, channel: str = "qq"
) -> dict[str, Any]:
    """把 raw 源(legacy 平铺文件 / envelope 分片根)归一进 transcript 层。幂等。

    返回无正文计数报告(隐私口径同 doctor)。
    """
    root = Path(out_root)
    seen = _existing_event_ids(root, channel)
    written = 0
    deduped = 0
    malformed = 0
    by_chat_type: dict[str, int] = {}
    by_direction: dict[str, int] = {}
    handles: dict[Path, Any] = {}
    try:
        for _src, line in (
            pair for s in sources for pair in _iter_source_lines(Path(s))
        ):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                malformed += 1
                continue
            if not isinstance(row, dict):
                malformed += 1
                continue
            envelope = None
            raw = row
            if "raw" in row and "archive_id" in row:  # envelope 形状
                envelope = row
                raw = row.get("raw")
                if not isinstance(raw, dict):
                    malformed += 1
                    continue
            event = normalize_qq_event(raw, envelope=envelope)
            if event is None:
                malformed += 1
                continue
            if event["event_id"] in seen:
                deduped += 1
                continue
            seen.add(event["event_id"])
            moment = event["sent_at"] or event["received_at"]
            if not moment:
                malformed += 1
                continue
            date = moment[:10]  # YYYY-MM-DD
            shard = (
                root / "transcript" / channel
                / date[:4] / date[5:7] / date[8:10] / "events.jsonl"
            )
            fh = handles.get(shard)
            if fh is None:
                shard.parent.mkdir(parents=True, exist_ok=True)
                fh = shard.open("a", encoding="utf-8")
                handles[shard] = fh
            fh.write(json.dumps(event, ensure_ascii=False) + "\n")
            written += 1
            by_chat_type[event["chat_type"]] = by_chat_type.get(event["chat_type"], 0) + 1
            by_direction[event["direction"]] = by_direction.get(event["direction"], 0) + 1
    finally:
        for fh in handles.values():
            fh.close()
    return {
        "ok": True,
        "channel": channel,
        "out_root": str(root),
        "events_written": written,
        "deduped": deduped,
        "malformed": malformed,
        "by_chat_type": dict(sorted(by_chat_type.items())),
        "by_direction": dict(sorted(by_direction.items())),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rtime_chat_runtime.transcript",
        description="raw 聊天归档 → normalized transcript(离线、幂等、无正文报告)。",
    )
    parser.add_argument("sources", nargs="+", help="legacy jsonl 文件或 envelope 分片根目录")
    parser.add_argument("--out", required=True, help="transcript 输出根目录")
    parser.add_argument("--channel", default="qq")
    args = parser.parse_args(argv)
    report = build_transcript(args.sources, args.out, channel=args.channel)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
