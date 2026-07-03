# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Extract text and media from a OneBot v11 message.

OneBot can deliver a message either as a CQ-code string (``messagePostFormat:
"string"``) or as a message-segment array (``"array"``). NapCat defaults to the
array form. ``extract_plain_text`` normalizes both to plain text for the model;
``extract_media`` pulls out the non-text segments (image / sticker / face / file /
voice …) so the M3 multimodal layer can download and understand them.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

# A CQ code looks like ``[CQ:type,k=v,k=v]``. Broad pattern to strip them from
# string messages; a structured one (``_CQ_SEG_RE``) is used for media parsing.
_CQ_STRIP_RE = re.compile(r"\[CQ:[^\]]*\]")
_CQ_SEG_RE = re.compile(r"\[CQ:([a-zA-Z]+),?([^\]]*)\]")

# Common QQ classic-emoji ids -> name (subset; unknown ids fall back to "[表情]").
# Source: OneBot/NapCat face table; only the frequently-sent ones are mapped.
QQ_FACE_NAMES: dict[str, str] = {
    "0": "惊讶",
    "1": "撇嘴",
    "2": "色",
    "3": "发呆",
    "4": "得意",
    "5": "流泪",
    "6": "害羞",
    "7": "闭嘴",
    "8": "睡",
    "9": "大哭",
    "10": "尴尬",
    "11": "发怒",
    "12": "调皮",
    "13": "呲牙",
    "14": "微笑",
    "15": "难过",
    "16": "酷",
    "18": "抓狂",
    "19": "吐",
    "20": "偷笑",
    "21": "可爱",
    "22": "白眼",
    "23": "傲慢",
    "24": "饥饿",
    "25": "困",
    "26": "惊恐",
    "27": "流汗",
    "28": "憨笑",
    "29": "悠闲",
    "30": "奋斗",
    "32": "疑问",
    "33": "嘘",
    "34": "晕",
    "36": "衰",
    "37": "骷髅",
    "38": "敲打",
    "39": "再见",
    "41": "发抖",
    "42": "爱情",
    "43": "跳跳",
    "46": "猪头",
    "49": "拥抱",
    "53": "蛋糕",
    "59": "便便",
    "60": "咖啡",
    "63": "玫瑰",
    "64": "凋谢",
    "66": "爱心",
    "74": "太阳",
    "75": "月亮",
    "76": "赞",
    "77": "踩",
    "78": "握手",
    "79": "胜利",
    "85": "飞吻",
    "89": "西瓜",
    "96": "冷汗",
    "97": "擦汗",
    "98": "抠鼻",
    "99": "鼓掌",
    "100": "糗大了",
    "101": "坏笑",
    "102": "左哼哼",
    "103": "右哼哼",
    "104": "哈欠",
    "106": "委屈",
    "109": "左亲亲",
    "111": "可怜",
    "116": "示爱",
    "118": "抱拳",
    "120": "拳头",
    "122": "爱你",
    "123": "NO",
    "124": "OK",
    "125": "转圈",
    "144": "喝彩",
    "147": "棒棒糖",
    "171": "茶",
    "172": "眨眼睛",
    "173": "泪奔",
    "174": "无奈",
    "175": "卖萌",
    "176": "小纠结",
    "177": "喷血",
    "178": "斜眼笑",
    "179": "doge",
    "180": "惊喜",
    "181": "骚扰",
    "182": "笑哭",
    "183": "我最美",
    "201": "点赞",
    "212": "托腮",
    "262": "脑阔疼",
    "263": "沧桑",
    "264": "捂脸",
    "265": "辣眼睛",
    "266": "哦哟",
    "267": "头秃",
    "273": "我酸了",
    "277": "汪汪",
    "278": "汗",
    "281": "无眼笑",
    "282": "敬礼",
    "284": "面无表情",
    "285": "摸鱼",
    "287": "哦",
    "289": "睁眼",
    "290": "敲开心",
    "293": "摸锦鲤",
    "294": "期待",
    "297": "拜谢",
    "298": "元宝",
    "299": "牛啊",
    "305": "右亲亲",
    "306": "牛气冲天",
    "307": "喵喵",
    "311": "打call",
    "312": "变形",
    "314": "仔细分析",
    "315": "加油",
    "318": "崇拜",
    "319": "比心",
    "320": "庆祝",
    "322": "拒绝",
    "324": "吃糖",
    "326": "生气",
}


def _unescape(text: str) -> str:
    """Reverse CQ-code text escaping (outside of a CQ segment)."""
    return text.replace("&#91;", "[").replace("&#93;", "]").replace("&amp;", "&")


def _unescape_param(text: str) -> str:
    """Reverse CQ-code escaping inside a parameter value (also unescapes commas)."""
    return _unescape(text).replace("&#44;", ",")


@dataclass(frozen=True)
class MediaSegment:
    """One non-text piece of an incoming message, normalized across wire forms."""

    kind: str  # image | sticker | face | file | voice | video | other
    url: str = ""
    name: str = ""  # file name (image/file)
    summary: str = ""  # sticker/animated-emoji label, e.g. "[动画表情]"
    face_id: str = ""  # classic QQ emoji id
    file_id: str = ""
    file_size: int = 0
    seg_type: str = ""  # the raw OneBot segment type


def _parse_cq_params(blob: str) -> dict[str, str]:
    params: dict[str, str] = {}
    for kv in blob.split(","):
        kv = kv.strip()
        if not kv or "=" not in kv:
            continue
        k, v = kv.split("=", 1)
        params[k.strip()] = _unescape_param(v.strip())
    return params


def _segment_to_media(seg_type: str, data: dict[str, Any]) -> MediaSegment | None:
    """Map one OneBot segment (type + data dict) to a MediaSegment, or None for text-like."""
    if seg_type in ("text", "at", "reply"):
        return None

    def _int(v: Any) -> int:
        try:
            return int(str(v))
        except (TypeError, ValueError):
            return 0

    url = str(data.get("url", "")).strip()
    summary = str(data.get("summary", "")).strip()

    if seg_type == "image":
        # NapCat sends stickers / animated emoji as image segments with sub_type=1
        # (and a summary like "[动画表情]"); normal photos are sub_type=0.
        is_sticker = str(data.get("sub_type", "0")) not in ("", "0") or bool(summary)
        return MediaSegment(
            kind="sticker" if is_sticker else "image",
            url=url,
            name=str(data.get("file", "")).strip(),
            summary=summary,
            file_size=_int(data.get("file_size")),
            seg_type=seg_type,
        )
    if seg_type == "mface":  # market sticker (商城表情 / 斗图)
        return MediaSegment(
            kind="sticker",
            url=url,
            summary=summary or str(data.get("key", "")).strip(),
            seg_type=seg_type,
        )
    if seg_type == "face":  # classic QQ emoji
        fid = str(data.get("id", "")).strip()
        return MediaSegment(
            kind="face",
            face_id=fid,
            summary=QQ_FACE_NAMES.get(fid, ""),
            seg_type=seg_type,
        )
    if seg_type == "file":
        return MediaSegment(
            kind="file",
            url=url,
            name=str(data.get("file", data.get("name", ""))).strip(),
            file_id=str(data.get("file_id", "")).strip(),
            file_size=_int(data.get("file_size")),
            seg_type=seg_type,
        )
    if seg_type == "record":  # voice
        return MediaSegment(
            kind="voice",
            url=url,
            name=str(data.get("file", "")).strip(),
            seg_type=seg_type,
        )
    if seg_type == "video":
        return MediaSegment(
            kind="video",
            url=url,
            name=str(data.get("file", "")).strip(),
            seg_type=seg_type,
        )
    return MediaSegment(kind="other", seg_type=seg_type)


def extract_plain_text(message: Any) -> str:
    """Return the plain-text content of a OneBot message (string or segment array)."""
    if isinstance(message, str):
        return _unescape(_CQ_STRIP_RE.sub("", message)).strip()

    if isinstance(message, list):
        parts: list[str] = []
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "text":
                data = seg.get("data") or {}
                parts.append(str(data.get("text", "")))
        return "".join(parts).strip()

    return ""


def extract_media(message: Any) -> list[MediaSegment]:
    """Return the non-text media segments of a OneBot message (string or array)."""
    out: list[MediaSegment] = []
    if isinstance(message, list):
        for seg in message:
            if not isinstance(seg, dict):
                continue
            seg_type = str(seg.get("type", ""))
            data = seg.get("data") if isinstance(seg.get("data"), dict) else {}
            media = _segment_to_media(seg_type, data or {})
            if media is not None and media.kind != "other":
                out.append(media)
    elif isinstance(message, str):
        for m in _CQ_SEG_RE.finditer(message):
            seg_type = m.group(1)
            params = _parse_cq_params(m.group(2))
            media = _segment_to_media(seg_type, params)
            if media is not None and media.kind != "other":
                out.append(media)
    return out


def mentioned_user_ids(message: Any) -> list[str]:
    """Return QQ ids mentioned via @ in the message (string or segment array)."""
    ids: list[str] = []
    if isinstance(message, str):
        for m in re.finditer(r"\[CQ:at,qq=(\d+)\]", message):
            ids.append(m.group(1))
    elif isinstance(message, list):
        for seg in message:
            if isinstance(seg, dict) and seg.get("type") == "at":
                qq = str((seg.get("data") or {}).get("qq", "")).strip()
                if qq:
                    ids.append(qq)
    return ids
