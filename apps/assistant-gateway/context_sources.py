# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""动态上下文源(brain/_system/rtime-context-sources.jsonl)的选择与注入。

从 gateway.py 抽出的内聚子系统(行为不变)。gateway.py 在文件底部把这里的公开函数
re-import 回自身命名空间,所以 `gateway.context_source_injection` 等仍可用,测试与调用方无需改动。

共享原语全部来自 _common(叶子层),不依赖 gateway —— 既无循环导入,也避免线上 __main__
运行时把 gateway 二次加载。
"""
from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path

from _common import (
    SENSITIVE_TEXT_RE,
    safe_brain_path,
    _beijing_date,
    _memory_terms,
    request_is_runtime_error_question,
)


def _redact_context_source_text(text: str) -> str:
    redacted_lines: list[str] = []
    for line in text.splitlines():
        redacted_lines.append("[redacted sensitive line]" if SENSITIVE_TEXT_RE.search(line) else line)
    return "\n".join(redacted_lines).strip()


def _context_source_tags(value) -> list[str]:
    if isinstance(value, list):
        raw = value
    else:
        raw = re.split(r"[,，\s]+", str(value or ""))
    tags: list[str] = []
    for item in raw:
        tag = str(item).strip()
        if tag and tag not in tags:
            tags.append(tag)
    return tags


def _context_source_date(value) -> str:
    text = str(value or "").strip()
    return text[:10] if re.match(r"^\d{4}-\d{2}-\d{2}", text) else ""


def _context_source_priority(value) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _load_context_source_records(cfg: dict) -> list[dict]:
    manifest = Path(cfg.get("context_sources_path") or "")
    if not manifest.is_file():
        return []
    records: list[dict] = []
    try:
        lines = manifest.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for line in lines:
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(item, dict):
            records.append(item)
    return records


def _context_source_query_text(body: dict) -> str:
    context = body.get("context") or {}
    options = body.get("options") or {}
    active = context.get("active_file") or {}
    selection = context.get("selection") or {}
    route = options.get("route_hint") or {}
    parts = [
        str(body.get("message") or ""),
        str(options.get("task_mode") or ""),
        str(options.get("target_folder") or ""),
        str(route.get("target_folder") or "") if isinstance(route, dict) else "",
        str(active.get("path") or ""),
        str(selection.get("text") or "")[:600],
    ]
    history = context.get("history")
    if isinstance(history, list):
        for item in history[-4:]:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                parts.append(item["content"][:500])
    return "\n".join(part for part in parts if part)


def _rank_context_sources(records: list[dict], body: dict, cfg: dict) -> list[tuple[float, dict, Path]]:
    brain_root = cfg.get("brain_root")
    if brain_root is None:
        return []
    root = Path(brain_root)
    today = _beijing_date()
    query_terms = Counter(_memory_terms(_context_source_query_text(body)))
    ranked: list[tuple[float, dict, Path]] = []
    for item in records:
        if str(item.get("status") or "").strip().lower() != "active":
            continue
        expires = _context_source_date(item.get("expires"))
        if expires and expires < today:
            continue
        active_from = _context_source_date(item.get("active_from"))
        if active_from and active_from > today:
            continue
        rel = str(item.get("path") or "").strip()
        path = safe_brain_path(rel, root)
        if path is None or not path.is_file():
            continue
        tags = _context_source_tags(item.get("tags"))
        source_text = " ".join(
            [
                str(item.get("id") or ""),
                str(item.get("kind") or ""),
                str(item.get("title") or ""),
                rel,
                " ".join(tags),
            ]
        )
        source_terms = Counter(_memory_terms(source_text))
        score = 0.0
        if query_terms:
            for term, qf in query_terms.items():
                if term in source_terms:
                    score += min(qf, 3) * (1 + min(source_terms[term], 3))
        if bool(item.get("always_include")):
            score += 2.0
        priority = _context_source_priority(item.get("priority"))
        if score <= 0 and priority <= 0:
            continue
        score += priority / 100.0
        ranked.append((score, {**item, "tags": tags, "path": rel}, path))
    ranked.sort(key=lambda item: (-item[0], -_context_source_priority(item[1].get("priority")), str(item[1].get("id") or "")))
    return ranked


def context_source_injection(body: dict, cfg: dict) -> tuple[str | None, dict | None]:
    if not cfg.get("context_sources_enabled", False):
        return None, None
    if request_is_runtime_error_question(body):
        return None, None
    max_chars = max(0, int(cfg.get("context_sources_max_chars", 5000)))
    max_items = max(0, int(cfg.get("context_sources_max_items", 3)))
    if max_chars <= 0 or max_items <= 0:
        return None, None
    ranked = _rank_context_sources(_load_context_source_records(cfg), body, cfg)
    if not ranked:
        return None, None
    lines = [
        "动态上下文源（来自brain/_system/rtime-context-sources.jsonl；只读；按本轮相关度选择；本轮明确指令优先；不要改写这些源文件）："
    ]
    remaining = max_chars
    referenced: list[dict] = []
    for _score, item, path in ranked[:max_items]:
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        text = _redact_context_source_text(re.sub(r"\n{3,}", "\n\n", text).strip())
        if not text:
            continue
        source_id = str(item.get("id") or item.get("path") or path.name)
        title = str(item.get("title") or path.name)
        rel = str(item.get("path") or "")
        tags = ", ".join(_context_source_tags(item.get("tags")))
        meta = f"id={source_id}; path={rel}; kind={item.get('kind') or 'context'}"
        if tags:
            meta += f"; tags={tags}"
        header = f"### {title}\n{meta}\n"
        available = remaining - len(header)
        per_source_limit = int(item.get("max_chars") or 0) if str(item.get("max_chars") or "").isdigit() else 0
        if per_source_limit > 0:
            available = min(available, per_source_limit)
        if available <= 0:
            break
        if len(text) > available:
            text = text[: max(0, available - 3)].rstrip() + "..."
        lines.append(header + text)
        referenced.append({"id": source_id, "path": rel, "kind": str(item.get("kind") or "")})
        remaining -= len(header) + len(text)
        if remaining <= 0:
            break
    if not referenced:
        return None, None
    events = {
        "referenced_count": len(referenced),
        "candidate_count": 0,
        "auto_merged_count": 0,
        "review_count": 0,
        "disabled": False,
        "referenced_context_sources": referenced,
        "summary": "本轮引用动态上下文源：" + "、".join(item["id"] for item in referenced),
    }
    return "\n\n".join(lines), events
