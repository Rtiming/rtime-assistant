# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""块5 正则直答:入站消息先过规则匹配,命中直接模板/数据直答,不调模型.

规则外置为运营可编辑的 JSON 文件(见 apps/qq-bridge/ops/direct-rules.example.json),
每条规则 = 一组正则 patterns + 一种回答方式:

  [
    {
      "name": "campus-bus",              # 规则名(日志用)
      "patterns": ["班车.*时刻", "..."],  # 任一正则 search 命中即算命中(忽略大小写)
      "type": "bus_timetable",           # "text" | "bus_timetable"
      "reply": "...",                    # type=text 用:直接返回(支持多行)
      "params": {"category": "校园班车", "startpoint": "东区", "week": "工作日"},
      "ttl_seconds": 21600                # type=bus_timetable 用:抓取结果缓存时长
    }
  ]

规则按文件顺序匹配,**首中即用**;命中后若该规则产不出答案(抓取/解析失败),
返回 None 优雅回落模型 —— 绝不抛异常穿透到桥的消息链路。
无规则文件 / 文件损坏 => engine 禁用(warning 一次),match 一律 None。

type=bus_timetable 内置 USTC 班车页抓取器:服务端直出 HTML
(https://weixine.ustc.edu.cn/ustcqy/mobile/busTimetable),GET 即得发车时间;
消息文本里识别到的关键词(东区/西区/南区/北区/节假日…)覆盖规则 params 里的默认
起点/日期;抓取结果按 (category, startpoint, week) 带 TTL 内存缓存(默认 6h)。
"高新"班车在另一个页面(/busTimetable/xyy,结构未适配),识别到即回落模型,
绝不拿校园班车数据冒充。

同步实现(urllib,超时 5s);asyncio 桥里请经 ``asyncio.to_thread(engine.match_rule, text)``
调用,避免阻塞事件循环。缓存是普通 dict:并发最坏情形只是重复抓一次,可接受。
"""

from __future__ import annotations

import json
import logging
import os
import re
import time
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime
from typing import Callable
from urllib.parse import urlencode

log = logging.getLogger("rtime_chat_runtime.direct_reply")

DEFAULT_BUS_ENDPOINT = "https://weixine.ustc.edu.cn/ustcqy/mobile/busTimetable"
DEFAULT_TTL_SECONDS = 6 * 3600.0
FETCH_TIMEOUT_SECONDS = 5.0
_MAX_FETCH_BYTES = 1_000_000

_DEFAULT_BUS_PARAMS = {"category": "校园班车", "startpoint": "东区", "week": "工作日"}

# Keyword -> canonical form-parameter value. Ordered; the match closest to the
# start of the message wins ("东区到西区的班车" => startpoint=东区, 起点语义).
_STARTPOINT_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("东校区", "东区"),
    ("西校区", "西区"),
    ("南校区", "南区"),
    ("北校区", "北区"),
    ("东区", "东区"),
    ("西区", "西区"),
    ("南区", "南区"),
    ("北区", "北区"),
)
_WEEK_KEYWORDS: tuple[tuple[str, str], ...] = (
    ("节假日", "节假日"),
    ("周末", "节假日"),
    ("假日", "节假日"),
    ("工作日", "工作日"),
)
# 高新园区班车 lives on a different page (/busTimetable/xyy) with an unadapted
# structure — bail to the model instead of answering with campus-bus data.
_UNSUPPORTED_HINTS: tuple[str, ...] = ("高新",)

# --- HTML extraction (structure pinned by tests/fixtures/bus_timetable_*.html,
# fetched from the real page on 2026-07-02; the parser targets exactly that DOM) ---
_LI_RE = re.compile(r"<li[^>]*>(.*?)</li>", re.S)
_BUTTON_RE = re.compile(r"<button[^>]*>(.*?)</button>", re.S)
_P_RE = re.compile(r"<p[^>]*>(.*?)</p>", re.S)
_TAG_RE = re.compile(r"<[^>]+>")
_TIME_RE = re.compile(r"^\d{1,2}:\d{2}#?$")
_NOTE_RE = re.compile(r'id="select_tip_msg"[^>]*>(.*?)</div>', re.S)


def _strip_tags(fragment: str) -> str:
    return re.sub(r"\s+", "", _TAG_RE.sub("", fragment))


def parse_bus_timetable(html: str) -> tuple[list[tuple[list[str], list[str]]], str]:
    """Extract ``([( [stops...], [times...] ), ...], note)`` from the timetable page.

    A route <li> holds the stop sequence as <button> texts and the departures as
    <p> texts under a "发车时间" heading; "11:45<span>#</span>" flattens to "11:45#"
    (# = 该趟有公交车辆运行, per the page's own tip text). Non-route <li> blocks
    (the query form) carry no "发车时间" and are skipped.
    """
    routes: list[tuple[list[str], list[str]]] = []
    for block in _LI_RE.findall(html or ""):
        if "发车时间" not in block:
            continue
        stops = [s for s in (_strip_tags(b) for b in _BUTTON_RE.findall(block)) if s]
        times = [
            t
            for t in (_strip_tags(p) for p in _P_RE.findall(block))
            if _TIME_RE.match(t)
        ]
        if stops and times:
            routes.append((stops, times))
    note_match = _NOTE_RE.search(html or "")
    note = (
        re.sub(r"\s+", "", _TAG_RE.sub("", note_match.group(1))) if note_match else ""
    )
    return routes, note


def _fetch_html(url: str, timeout: float = FETCH_TIMEOUT_SECONDS) -> str:
    request = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (rtime-assistant direct-reply)"}
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 — https endpoint from code, not user input
        return resp.read(_MAX_FETCH_BYTES).decode("utf-8", errors="replace")


def _find_keyword(text: str, table: tuple[tuple[str, str], ...]) -> str | None:
    """Return the canonical value whose keyword occurs earliest in ``text``."""
    best: tuple[int, str] | None = None
    for keyword, value in table:
        idx = text.find(keyword)
        if idx >= 0 and (best is None or idx < best[0]):
            best = (idx, value)
    return best[1] if best else None


@dataclass(frozen=True)
class DirectRule:
    name: str
    patterns: tuple[re.Pattern[str], ...]
    kind: str  # "text" | "bus_timetable"
    reply: str = ""
    params: dict[str, str] = field(default_factory=dict)
    ttl_seconds: float = DEFAULT_TTL_SECONDS


def _compile_rule(raw: object, index: int) -> DirectRule | None:
    if not isinstance(raw, dict):
        log.warning("direct-reply rule #%d is not an object; skipped", index)
        return None
    name = str(raw.get("name") or f"rule-{index}")
    kind = str(raw.get("type") or "text")
    if kind not in ("text", "bus_timetable"):
        log.warning("direct-reply rule %s: unknown type %r; skipped", name, kind)
        return None
    patterns: list[re.Pattern[str]] = []
    raw_patterns = raw.get("patterns")
    for pat in raw_patterns if isinstance(raw_patterns, list) else []:
        try:
            patterns.append(re.compile(str(pat), re.IGNORECASE))
        except re.error as exc:
            log.warning(
                "direct-reply rule %s: bad pattern %r (%s); skipped", name, pat, exc
            )
    if not patterns:
        log.warning("direct-reply rule %s: no usable patterns; skipped", name)
        return None
    reply = str(raw.get("reply") or "")
    if kind == "text" and not reply.strip():
        log.warning("direct-reply rule %s: type=text without reply; skipped", name)
        return None
    raw_params = raw.get("params")
    params = (
        {str(k): str(v) for k, v in raw_params.items()}
        if isinstance(raw_params, dict)
        else {}
    )
    try:
        ttl = float(raw.get("ttl_seconds", DEFAULT_TTL_SECONDS))
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL_SECONDS
    if ttl <= 0:
        ttl = DEFAULT_TTL_SECONDS
    return DirectRule(
        name=name,
        patterns=tuple(patterns),
        kind=kind,
        reply=reply,
        params=params,
        ttl_seconds=ttl,
    )


class DirectReplyEngine:
    """Ordered first-match rule engine. ``match`` never raises: any rule failure
    (fetch/parse/anything) degrades to None so the caller falls back to the model."""

    def __init__(
        self,
        rules: list[DirectRule] | None = None,
        *,
        fetch_html: Callable[[str], str] | None = None,
    ) -> None:
        self._rules: tuple[DirectRule, ...] = tuple(rules or ())
        self._fetch = fetch_html or _fetch_html
        # (rule name, category, startpoint, week) -> (monotonic fetch time, reply)
        self._bus_cache: dict[tuple[str, str, str, str], tuple[float, str]] = {}

    @property
    def enabled(self) -> bool:
        return bool(self._rules)

    @classmethod
    def disabled(cls) -> "DirectReplyEngine":
        return cls([])

    @classmethod
    def load(
        cls, path: str, *, fetch_html: Callable[[str], str] | None = None
    ) -> "DirectReplyEngine":
        """Load rules from a JSON file (a list, or ``{"rules": [...]}``).

        Empty path => quietly disabled. Missing/corrupt file or no usable rule =>
        disabled with a single warning (the bridge builds the engine once).
        """
        if not (path or "").strip():
            return cls.disabled()
        try:
            with open(path, encoding="utf-8") as fh:
                data = json.load(fh)
        except (OSError, ValueError) as exc:
            log.warning(
                "direct-reply rules %s unusable (%s); direct reply disabled", path, exc
            )
            return cls.disabled()
        raw_rules = data.get("rules") if isinstance(data, dict) else data
        if not isinstance(raw_rules, list):
            log.warning(
                "direct-reply rules %s: expected a JSON list; direct reply disabled",
                path,
            )
            return cls.disabled()
        rules = [
            rule
            for i, raw in enumerate(raw_rules)
            if (rule := _compile_rule(raw, i)) is not None
        ]
        if not rules:
            log.warning(
                "direct-reply rules %s: no usable rules; direct reply disabled", path
            )
            return cls.disabled()
        log.info("direct-reply: %d rule(s) loaded from %s", len(rules), path)
        return cls(rules, fetch_html=fetch_html)

    def match(self, text: str) -> str | None:
        hit = self.match_rule(text)
        return hit[1] if hit else None

    def match_rule(self, text: str) -> tuple[str, str] | None:
        """Return ``(rule_name, reply_text)`` for the first matching rule, else None."""
        text = (text or "").strip()
        if not text or not self._rules:
            return None
        for rule in self._rules:
            if not any(p.search(text) for p in rule.patterns):
                continue
            try:
                reply = self._dispatch(rule, text)
            except Exception as exc:  # noqa: BLE001 — never leak into the message path
                log.warning("direct-reply rule %s failed: %s", rule.name, exc)
                return None
            return (
                (rule.name, reply) if reply else None
            )  # 首中即用: no next-rule fallback
        return None

    def _dispatch(self, rule: DirectRule, text: str) -> str | None:
        if rule.kind == "text":
            return rule.reply
        if rule.kind == "bus_timetable":
            return self._bus_reply(rule, text)
        return None  # unreachable: load() drops unknown kinds

    # --- bus timetable ---
    def _bus_reply(self, rule: DirectRule, text: str) -> str | None:
        if any(hint in text for hint in _UNSUPPORTED_HINTS):
            return None  # 高新园区班车: different page, let the model handle it
        params = dict(_DEFAULT_BUS_PARAMS)
        for key in ("category", "startpoint", "week"):
            value = (rule.params.get(key) or "").strip()
            if value:
                params[key] = value
        startpoint = _find_keyword(text, _STARTPOINT_KEYWORDS)
        if startpoint:
            params["startpoint"] = startpoint
        week = _find_keyword(text, _WEEK_KEYWORDS)
        if week:
            params["week"] = week

        cache_key = (
            rule.name,
            params["category"],
            params["startpoint"],
            params["week"],
        )
        cached = self._bus_cache.get(cache_key)
        if cached and time.monotonic() - cached[0] < rule.ttl_seconds:
            return cached[1]

        url = (
            DEFAULT_BUS_ENDPOINT
            + "?"
            + urlencode(
                [
                    ("category", params["category"]),
                    ("startpoint", params["startpoint"]),
                    ("week[]", params["week"]),
                ]
            )
        )
        html = self._fetch(url)
        routes, note = parse_bus_timetable(html)
        if not routes:
            log.warning(
                "direct-reply rule %s: no routes parsed from %s (page changed?)",
                rule.name,
                url,
            )
            return None
        lines = [
            f"🚌 {params['category']}({params['startpoint']}出发,{params['week']})"
        ]
        for stops, times in routes:
            lines.append("→".join(stops) + ":" + "、".join(times))
        if note:
            lines.append("注:" + note)
        lines.append("来源:" + url)
        lines.append("抓取时间:" + datetime.now().strftime("%Y-%m-%d %H:%M"))
        reply = "\n".join(lines)
        self._bus_cache[cache_key] = (time.monotonic(), reply)
        return reply


# --- T8 热调项:direct_rules 文件热重载(按 mtime 失效重建,不每条消息读文件) ---


class DirectReplyProvider:
    """A mtime-cached :class:`DirectReplyEngine` factory (design §2.10 hot field).

    The bridge used to build the engine ONCE (``DirectReplyEngine.load(path)`` at
    handler-build time), so editing the rules file needed a container restart. T8
    makes the rules file a hot field: :meth:`current` returns the live engine,
    rebuilding it ONLY when the file's ``(mtime, size)`` moved since the last build.

    PERF (owner hard constraint — no per-message latency regression): the common
    path (rules file unchanged, or no rules file) does a SINGLE ``os.stat`` and
    returns the cached engine — no JSON parse, no rule recompile, no rebuild. A
    changed file (an operator edited the rules, or a profile reload rewrote it) is
    detected by the stat and triggers exactly one rebuild. An empty path is the
    "disabled" case and does not even stat.

    ``current`` is what the message path calls; it keeps the engine's own bus-cache
    across unchanged rebuilds (a rebuild only happens on a real file change, so the
    fresh engine starting with an empty bus-cache is correct — the rules changed).
    """

    def __init__(
        self, path: str, *, fetch_html: Callable[[str], str] | None = None
    ) -> None:
        self._path = (path or "").strip()
        self._fetch = fetch_html
        self._sig: tuple[float, int] | None = None
        # Start disabled; the first current() builds from the file (or stays
        # disabled when the path is empty / missing).
        self._engine = DirectReplyEngine.disabled()
        if self._path:
            self._engine = self._build()

    def _stat_sig(self) -> tuple[float, int] | None:
        """``(mtime, size)`` of the rules file, or None if it cannot be stat'd.

        One ``os.stat`` — the whole point of the mtime cache is that an unchanged
        file costs exactly this and nothing more.
        """
        try:
            st = os.stat(self._path)
        except OSError:
            return None
        return (st.st_mtime, st.st_size)

    def _build(self) -> DirectReplyEngine:
        self._sig = self._stat_sig()
        return DirectReplyEngine.load(self._path, fetch_html=self._fetch)

    def current(self) -> DirectReplyEngine:
        """The live engine, rebuilt only when the rules file changed (mtime/size).

        No path => the cached disabled engine, no stat. Otherwise a single stat:
        if the signature is unchanged, return the cached engine as-is (the fast
        path — no file read); if it moved (or the file appeared/vanished), rebuild.
        """
        if not self._path:
            return self._engine
        sig = self._stat_sig()
        if sig != self._sig:
            self._sig = sig
            self._engine = DirectReplyEngine.load(self._path, fetch_html=self._fetch)
        return self._engine
