# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""记忆 / 记忆候选 / 记忆事件子系统。从 gateway 抽出,共享原语来自 _common,不依赖 gateway。"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import time
from collections import Counter
from datetime import timedelta
from pathlib import Path

from _common import (
    SENSITIVE_TEXT_RE,
    MEMORY_INTENT_RE,
    _memory_terms,
    _parse_memory_frontmatter,
    _today_beijing,
)
from context_sources import context_source_injection

# 记忆卡片缓存(模块级状态,随本子系统一起搬出 gateway)。
_MEMORY_CARDS_CACHE: dict = {"entry": None}


def build_memory_section(memory) -> str | None:
    if not isinstance(memory, dict):
        return None
    commands = memory.get("commands") if isinstance(memory.get("commands"), list) else []
    disabled = bool(memory.get("disabled")) or "do_not_remember" in commands
    if disabled:
        return "本轮记忆策略：用户要求不要把本轮对话或附件写入长期记忆；只可用于当前回答。"
    if "remember" in commands:
        return (
            "本轮记忆策略：用户显式点击/表达了记住意图。若内容适合作为偏好，"
            "只能产生记忆候选或review摘要，不得直接改长期画像。"
        )
    if "open_review" in commands:
        return "本轮记忆策略：用户希望查看记忆review入口；回答中给出安全的review路径或下一步。"
    return None


def _memory_disabled(context: dict) -> bool:
    memory = context.get("memory")
    if not isinstance(memory, dict):
        return False
    commands = memory.get("commands") if isinstance(memory.get("commands"), list) else []
    return bool(memory.get("disabled")) or "do_not_remember" in commands


def _memory_query_text(body: dict) -> str:
    context = body.get("context") or {}
    options = body.get("options") or {}
    parts = [
        str(body.get("message") or ""),
        str((context.get("active_file") or {}).get("path") or ""),
        str(options.get("task_mode") or ""),
        str(((context.get("selection") or {}).get("text") or ""))[:600],
    ]
    history = context.get("history")
    if isinstance(history, list):
        for item in history[-4:]:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                parts.append(item["content"][:500])
    return "\n".join(part for part in parts if part)


def _memory_cards_fingerprint(cards_dir: Path) -> tuple:
    """Cheap stat-only signature of the cards dir, so the (much more expensive)
    read+frontmatter-parse of every card is skipped when nothing changed. Any
    add/remove/edit changes a name, mtime, or size and thus the fingerprint."""
    entries: list[tuple] = []
    try:
        with os.scandir(cards_dir) as it:
            for item in it:
                if not item.name.endswith(".md") or item.name == "README.md":
                    continue
                try:
                    st = item.stat()
                except OSError:
                    continue
                entries.append((item.name, st.st_mtime_ns, st.st_size))
    except OSError:
        return ()
    entries.sort()
    return tuple(entries)


def _approved_memory_cards(cfg: dict) -> list[dict]:
    if not cfg.get("memory_injection_enabled", False):
        return []
    memory_root = cfg.get("memory_root")
    if memory_root is None:
        brain_root = cfg.get("brain_root")
        if brain_root is None:
            return []
        memory_root = Path(brain_root) / "memory"
    cards_dir = Path(memory_root) / "cards"
    if not cards_dir.is_dir():
        return []
    today = time.strftime("%Y-%m-%d")
    # today is part of the key so day-boundary expiry of situational cards is honored.
    cache_key = (str(cards_dir), today, _memory_cards_fingerprint(cards_dir))
    entry = _MEMORY_CARDS_CACHE["entry"]
    if entry is not None and entry[0] == cache_key:
        return list(entry[1])
    cards: list[dict] = []
    for path in sorted(cards_dir.glob("*.md")):
        if path.name == "README.md":
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        fm, body = _parse_memory_frontmatter(text)
        if fm.get("type") != "memory-card":
            continue
        if fm.get("sensitivity", "normal") != "normal":
            continue
        if fm.get("confidence") == "inferred":
            continue
        expires = str(fm.get("expires") or "")
        if fm.get("layer") == "situational" and expires and expires < today:
            continue
        claim = str(fm.get("claim") or "").strip()
        if not claim:
            continue
        cards.append(
            {
                "id": path.name,
                "claim": claim,
                "body": re.sub(r"\s+", " ", body).strip(),
                "scope": str(fm.get("scope") or ""),
                "confidence": str(fm.get("confidence") or ""),
                "layer": str(fm.get("layer") or ""),
                "unlock_hints": fm.get("unlock_hints") if isinstance(fm.get("unlock_hints"), list) else [],
            }
        )
    _MEMORY_CARDS_CACHE["entry"] = (cache_key, cards)
    return list(cards)


def _rank_memory_cards(cards: list[dict], query: str) -> list[tuple[float, dict]]:
    query_terms = Counter(_memory_terms(query))
    if not query_terms or not cards:
        return []
    docs: list[Counter] = []
    lengths: list[int] = []
    df: Counter = Counter()
    for card in cards:
        text = " ".join(
            [
                card.get("claim", ""),
                card.get("body", ""),
                card.get("scope", ""),
                " ".join(card.get("unlock_hints") or []),
            ]
        )
        terms = Counter(_memory_terms(text))
        docs.append(terms)
        lengths.append(sum(terms.values()) or 1)
        df.update(terms.keys())
    avgdl = sum(lengths) / len(lengths)
    scored: list[tuple[float, dict]] = []
    k1 = 1.4
    b = 0.75
    total = len(cards)
    for card, terms, doc_len in zip(cards, docs, lengths):
        score = 0.0
        for term, qf in query_terms.items():
            tf = terms.get(term, 0)
            if tf <= 0:
                continue
            idf = math.log(1 + (total - df[term] + 0.5) / (df[term] + 0.5))
            denom = tf + k1 * (1 - b + b * doc_len / avgdl)
            score += idf * (tf * (k1 + 1) / denom) * min(qf, 3)
        if score > 0:
            scored.append((score, card))
    scored.sort(key=lambda item: (-item[0], item[1]["id"]))
    return scored


def approved_memory_injection(body: dict, cfg: dict) -> tuple[str | None, dict | None]:
    context = body.get("context") or {}
    if _memory_disabled(context):
        return None, None
    ranked = _rank_memory_cards(_approved_memory_cards(cfg), _memory_query_text(body))
    if not ranked:
        return None, None
    max_cards = max(0, int(cfg.get("memory_injection_max_cards", 3)))
    budget = max(0, int(cfg.get("memory_injection_max_chars", 1200)))
    if max_cards <= 0 or budget <= 0:
        return None, None
    selected = [card for _, card in ranked[:max_cards]]
    lines = ["关于用户的已批准记忆（只在适用时使用；本轮明确指令优先；不得写入或改动记忆）："]
    remaining = budget
    referenced: list[str] = []
    for card in selected:
        detail = card["claim"]
        if card.get("body") and card["body"] not in detail:
            detail = f"{detail}；{card['body']}"
        suffix = ", ".join(part for part in (card.get("scope"), card.get("confidence")) if part)
        line = f"- [{card['id']}] {detail}"
        if suffix:
            line += f"（{suffix}）"
        if len(line) > remaining:
            line = line[: max(0, remaining - 3)].rstrip() + "..."
        if not line.strip("."):
            break
        lines.append(line)
        referenced.append(card["id"])
        remaining -= len(line)
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
        "referenced_cards": referenced,
        "summary": "本轮引用已批准记忆：" + "、".join(referenced),
    }
    return "\n".join(lines), events


def _memory_candidate_requested(body: dict) -> bool:
    context = body.get("context") or {}
    events = memory_events_from_context(context)
    if isinstance(events, dict) and int(events.get("candidate_count") or 0) > 0:
        return True
    return bool(MEMORY_INTENT_RE.search(str(body.get("message") or "")))


def _memory_candidate_claim(body: dict) -> str:
    message = str(body.get("message") or "").strip()
    if message:
        return message
    context = body.get("context") or {}
    selection = context.get("selection") or {}
    selected = str(selection.get("text") or "").strip()
    return selected[:1200]


def _candidate_slug(text: str) -> str:
    slug = "-".join(re.findall(r"[a-zA-Z0-9]+", text.lower()))[:48].strip("-")
    return slug or "memory-candidate"


def _write_review_queue_candidate(body: dict, cfg: dict) -> dict | None:
    if not cfg.get("memory_candidate_write_enabled", False):
        return None
    if not _memory_candidate_requested(body):
        return None
    claim = _memory_candidate_claim(body)
    if not claim:
        return {"ok": False, "action": "skip", "reason": "empty_claim", "written": False}
    entry = str(body.get("entry") or "gateway").strip() or "gateway"
    if SENSITIVE_TEXT_RE.search(claim):
        return {
            "ok": True,
            "action": "hold",
            "reason": "sensitive_signal",
            "written": False,
            "entry": entry,
            "claim_chars": len(claim),
        }
    review_dir = Path(cfg.get("memory_candidate_review_dir") or "")
    today = _today_beijing().date()
    expires = today + timedelta(days=90)
    digest = hashlib.sha256(f"{entry}\n{claim}".encode("utf-8")).hexdigest()[:12]
    path = review_dir / f"{today.isoformat()}-{_candidate_slug(claim)}-{digest}.md"
    if path.exists():
        return {
            "ok": True,
            "action": "dedupe",
            "written": False,
            "path": str(path),
            "entry": entry,
            "claim_chars": len(claim),
        }
    review_dir.mkdir(parents=True, exist_ok=True)
    source = f"gateway:{entry}"
    text = "\n".join(
        [
            "---",
            "type: memory-card",
            f"claim: {json.dumps(claim, ensure_ascii=False)}",
            "scope: assistant-personalization",
            f"source: {json.dumps(source, ensure_ascii=False)}",
            f"observed_at: {today.isoformat()}",
            "confidence: user-stated",
            "layer: situational",
            f"expires: {expires.isoformat()}",
            "supersedes: []",
            "sensitivity: normal",
            f"unlock_hints: [gateway-memory-candidate, {entry}]",
            "---",
            "候选由 gateway 根据用户显式记忆意图生成，等待审核合并；不要自动写入长期 memory/cards。",
            "",
        ]
    )
    path.write_text(text, encoding="utf-8")
    return {
        "ok": True,
        "action": "write",
        "written": True,
        "path": str(path),
        "entry": entry,
        "claim_chars": len(claim),
    }


def merge_memory_events(*items: dict | None) -> dict | None:
    events = [item for item in items if isinstance(item, dict)]
    if not events:
        return None
    merged = {
        "referenced_count": 0,
        "candidate_count": 0,
        "auto_merged_count": 0,
        "review_count": 0,
        "disabled": any(bool(item.get("disabled")) for item in events),
        "summary": "",
    }
    summaries: list[str] = []
    referenced_cards: list[str] = []
    referenced_context_sources: list[dict] = []
    for item in events:
        for key in ("referenced_count", "candidate_count", "auto_merged_count", "review_count"):
            value = item.get(key)
            if isinstance(value, int):
                merged[key] += value
        if isinstance(item.get("summary"), str) and item["summary"]:
            summaries.append(item["summary"])
        if isinstance(item.get("referenced_cards"), list):
            for card_id in item["referenced_cards"]:
                if isinstance(card_id, str) and card_id not in referenced_cards:
                    referenced_cards.append(card_id)
        if isinstance(item.get("referenced_context_sources"), list):
            for source in item["referenced_context_sources"]:
                if not isinstance(source, dict):
                    continue
                safe = {
                    "id": str(source.get("id") or ""),
                    "path": str(source.get("path") or ""),
                    "kind": str(source.get("kind") or ""),
                }
                if safe["id"] and safe not in referenced_context_sources:
                    referenced_context_sources.append(safe)
    if summaries:
        merged["summary"] = "；".join(summaries)
    if referenced_cards:
        merged["referenced_cards"] = referenced_cards
    if referenced_context_sources:
        merged["referenced_context_sources"] = referenced_context_sources
    return merged


def memory_events_for_request(body: dict, cfg: dict) -> dict | None:
    context = body.get("context") or {}
    _, approved_events = approved_memory_injection(body, cfg)
    _, context_source_events = context_source_injection(body, cfg)
    return merge_memory_events(memory_events_from_context(context), approved_events, context_source_events)


def memory_events_from_context(context: dict) -> dict | None:
    memory = context.get("memory")
    if not isinstance(memory, dict):
        return None
    commands = memory.get("commands") if isinstance(memory.get("commands"), list) else []
    disabled = bool(memory.get("disabled")) or "do_not_remember" in commands
    remember = "remember" in commands and not disabled
    return {
        "referenced_count": 0,
        "candidate_count": 1 if remember else 0,
        "auto_merged_count": 0,
        "review_count": 1 if remember else 0,
        "disabled": disabled,
        "summary": "本轮只生成记忆候选，不直接写长期画像。" if remember else "",
    }
