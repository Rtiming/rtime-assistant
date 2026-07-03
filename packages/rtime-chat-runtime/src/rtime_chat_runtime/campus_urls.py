# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""USTC campus-service URL table for the web-intent router (块2 校园网页意图路由).

口语化问"东区班车几点发车"这类问题不带 URL、不带"网页"字样，老的 web 意图正则
不命中，模型拿不到 web 工具也不知道去哪查。tool_policy 检测到校园意图后，把这份
URL 表作为 runtime hint 附给模型，让它直接 WebFetch/rtime-web-fetch 已知地址，
而不是让用户自己找链接。

只收录已核实"公开、服务端直出 HTML、无登录墙、WebFetch 可直接读"的页面，宁缺毋滥
（核实时间见条目注释）。

覆盖/扩展：env ``RTIME_CAMPUS_URLS_FILE`` 指向一个 JSON 文件——
- 顶层是列表 ``[{"name": .., "url": .., "note": ..}, ...]`` → 整表替换内置条目；
- 顶层是对象 ``{"mode": "extend", "entries": [...]}`` → 追加到内置条目后（按 url 去重）；
  ``"mode": "replace"``（或省略 mode）→ 整表替换。
文件缺失/解析失败/无有效条目时回退内置表——这是提示性功能，配置错误不应炸桥。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

CAMPUS_URLS_ENV = "RTIME_CAMPUS_URLS_FILE"

# 已核实 2026-07-02：三条均 HTTP 200、服务端直出 HTML、无登录墙。
BUILTIN_CAMPUS_URLS: tuple[dict[str, str], ...] = (
    {
        "name": "校园班车时刻表（示例：东区发车、工作日）",
        "url": (
            "https://weixine.ustc.edu.cn/ustcqy/mobile/busTimetable"
            "?category=%E6%A0%A1%E5%9B%AD%E7%8F%AD%E8%BD%A6"
            "&startpoint=%E4%B8%9C%E5%8C%BA"
            "&week%5B%5D=%E5%B7%A5%E4%BD%9C%E6%97%A5&"
        ),
        "note": (
            "查询参数可改：startpoint=东区/西区/南区/北区（值需 URL 编码），"
            "week[]=工作日/周末"
        ),
    },
    {
        "name": "教务处教学日历（校历）",
        "url": "https://www.teach.ustc.edu.cn/calendar",
        "note": "列出各学期校历/教学日历",
    },
    {
        "name": "教务处通知公告",
        "url": "https://www.teach.ustc.edu.cn/notice",
        "note": "",
    },
)


def _normalize(raw: object) -> list[dict[str, str]]:
    """Keep only entries with a name and an http(s) url; coerce fields to str."""
    out: list[dict[str, str]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "").strip()
        url = str(item.get("url") or "").strip()
        if not name or not url.startswith(("http://", "https://")):
            continue
        out.append(
            {"name": name, "url": url, "note": str(item.get("note") or "").strip()}
        )
    return out


# T8 热调项:校园 URL 表按 mtime 失效缓存(设计 §2.10)。老实现每次调用都读文件——
# 命中校园意图的每条消息都做一次磁盘读。缓存后:文件未变=一次 os.stat 返回已解析结
# 果(无 JSON parse),文件改了(运营编辑/profile reload 改写)才重新解析一次。
# key=(path, mtime, size);path 变(env 改指别的文件)也失效。builtin 无文件路径,
# 直接返回(无 stat)。owner 硬约束:不给每条消息加解析开销,只留一个 stat。
_CACHE_KEY: tuple[str, float, int] | None = None
_CACHE_VALUE: list[dict[str, str]] | None = None


def _load_campus_urls_from_file(path: str) -> list[dict[str, str]]:
    """Parse + merge the override file onto the builtin table (no caching here)."""
    builtin = [dict(entry) for entry in BUILTIN_CAMPUS_URLS]
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return builtin
    if isinstance(data, list):
        entries, mode = _normalize(data), "replace"
    elif isinstance(data, dict):
        entries = _normalize(data.get("entries"))
        mode = (
            "extend"
            if str(data.get("mode") or "replace").lower() == "extend"
            else "replace"
        )
    else:
        return builtin
    if not entries:
        return builtin
    if mode == "extend":
        known = {entry["url"] for entry in builtin}
        return builtin + [entry for entry in entries if entry["url"] not in known]
    return entries


def load_campus_urls() -> list[dict[str, str]]:
    """Return the effective URL table: builtin, or the env-file override.

    mtime-cached (T8): an unchanged override file costs a single ``os.stat`` and
    returns the cached parse; the file only re-parses when it (or the env pointing
    at it) changes. No env file => the builtin table with no stat.
    """
    global _CACHE_KEY, _CACHE_VALUE
    path = os.getenv(CAMPUS_URLS_ENV, "").strip()
    if not path:
        return [dict(entry) for entry in BUILTIN_CAMPUS_URLS]
    try:
        st = os.stat(path)
        key: tuple[str, float, int] | None = (path, st.st_mtime, st.st_size)
    except OSError:
        key = None
    if key is not None and key == _CACHE_KEY and _CACHE_VALUE is not None:
        # unchanged file: return a copy so a caller mutating the list cannot
        # corrupt the cached table.
        return [dict(entry) for entry in _CACHE_VALUE]
    value = _load_campus_urls_from_file(path)
    if key is not None:
        _CACHE_KEY, _CACHE_VALUE = key, [dict(entry) for entry in value]
    return value


def campus_urls_hint() -> str:
    """Runtime hint listing known campus service URLs ('' when table is empty)."""
    entries = load_campus_urls()
    if not entries:
        return ""
    lines = "\n".join(
        f"- {entry['name']}：{entry['url']}"
        + (f"（{entry['note']}）" if entry["note"] else "")
        for entry in entries
    )
    return (
        "\n\n[运行环境提示：本次请求疑似涉及校园服务信息。已知校园服务地址：\n"
        f"{lines}\n"
        "以上是公开页面（服务端直出 HTML、无需登录），可用 WebFetch 或 "
        "`rtime-web-fetch url <URL>` 直接读取后回答；与问题相关时优先直接抓取并给出"
        "要点，不要让用户自己找链接。若抓到的内容与问题无关或疑似过期，如实说明。]"
    )
