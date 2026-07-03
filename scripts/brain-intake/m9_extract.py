#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M9 memory-loop candidate extractor for run-03.

The extractor is intentionally conservative: it only turns explicit user
memory/correction signals into review-queue candidates. It never merges cards
into durable memory.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
import tempfile
from pathlib import Path
from typing import Any

import intake_common as ic
import memory_schema


DEFAULT_BRAIN_ROOT = Path.home() / "OrangePi-Store" / "sync" / "brain"
DEFAULT_RUN_DIR = Path("work/pipeline/run-03")
SENSITIVE = re.compile(r"身份证|护照|银行卡|手机号|电话号码|住址|地址|密码|口令|token|api[_ -]?key|secret", re.I)
REMEMBER_PATTERNS = (
    re.compile(r"(?:请)?记住[：:]\s*(?P<claim>.+)"),
    re.compile(r"(?:请)?帮我记[一]?下[：:]\s*(?P<claim>.+)"),
)
PREFERENCE_PATTERNS = (
    re.compile(r"我(?:更)?(?:希望|喜欢|偏好)(?P<claim>.+)"),
    re.compile(r"以后(?:请|最好|都)?(?P<claim>.+)"),
)
FEEDBACK_PATTERNS = (
    re.compile(r"(?:下次|以后)(?P<claim>.+?)(?:。|$)"),
    re.compile(r"(?:刚才|上次).{0,16}(?:错了|不对|别这样)(?P<claim>.*)"),
)


def _date(raw: str | None) -> str:
    if raw:
        return raw
    return dt.datetime.now().strftime("%Y-%m-%d")


def _expires(observed_at: str) -> str:
    base = dt.date.fromisoformat(observed_at)
    return (base + dt.timedelta(days=90)).isoformat()


def _slug(text: str) -> str:
    asciiish = re.sub(r"[^A-Za-z0-9]+", "-", text).strip("-").lower()
    if asciiish:
        return asciiish[:40]
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:12]


def _quote(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _record_text(record: dict[str, Any]) -> str:
    for key in ("message_excerpt", "message", "query_excerpt", "user_message", "prompt"):
        value = record.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    request = record.get("request")
    if isinstance(request, dict):
        value = request.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    body = record.get("body")
    if isinstance(body, dict):
        value = body.get("message")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _entry(record: dict[str, Any]) -> str:
    value = record.get("entry")
    if isinstance(value, str) and value.strip():
        return value.strip()
    return "gateway"


def _source(record: dict[str, Any], source_id: str, line_no: int) -> str:
    sid = str(record.get("source_id") or source_id)
    ts = str(record.get("ts") or record.get("timestamp") or "")
    suffix = f" line {line_no}"
    return f"{sid}{suffix}" + (f" ts {ts}" if ts else "")


def _clean_claim(raw: str) -> str:
    claim = re.sub(r"\s+", " ", raw).strip(" ，,。.;；")
    claim = re.sub(r"(?:请)?只回复.*$", "", claim).strip(" ，,。.;；")
    claim = re.sub(r"(?:请)?简单回复.*$", "", claim).strip(" ，,。.;；")
    claim = claim.replace("我", "用户", 1) if claim.startswith("我") else claim
    if not claim.startswith("用户"):
        claim = "用户" + claim
    return claim[:220]


def _extract_signal(text: str) -> tuple[str, str, str]:
    if not text.strip():
        return "noop", "", "empty message"
    if SENSITIVE.search(text):
        return "hold", "", "sensitive signal"
    for pattern in REMEMBER_PATTERNS:
        match = pattern.search(text)
        if match:
            return "memory-card", _clean_claim(match.group("claim")), "explicit remember signal"
    for pattern in PREFERENCE_PATTERNS:
        match = pattern.search(text)
        if match:
            return "memory-card", _clean_claim(match.group("claim")), "explicit preference signal"
    for pattern in FEEDBACK_PATTERNS:
        match = pattern.search(text)
        if match:
            claim = match.group("claim") or text
            return "feedback", _clean_claim(claim), "explicit correction/feedback signal"
    return "noop", "", "no explicit memory signal"


def _card_body(kind: str, claim: str, source: str, observed_at: str, entry: str, source_text: str) -> str:
    if kind == "memory-card":
        return "\n".join(
            [
                "---",
                "type: memory-card",
                f"claim: {_quote(claim)}",
                "scope: assistant-personalization",
                f"source: {_quote(source)}",
                f"observed_at: {observed_at}",
                "confidence: user-stated",
                "layer: situational",
                f"expires: {_expires(observed_at)}",
                "supersedes: []",
                "sensitivity: normal",
                "unlock_hints: [memory-loop, gateway]",
                "access: local-only",
                "---",
                f"来源入口：{entry}",
                "",
                f"原始摘录：{source_text[:500]}",
                "",
            ]
        )
    return "\n".join(
        [
            "---",
            "type: feedback",
            f"claim: {_quote(claim)}",
            f"source: {_quote(source)}",
            f"observed_at: {observed_at}",
            "sensitivity: normal",
            "---",
            f"下次处理同类请求时，先参考这条用户反馈；来源入口：{entry}。",
            "",
            f"原始摘录：{source_text[:500]}",
            "",
        ]
    )


def _validate_content(content: str) -> tuple[list[str], list[str]]:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "candidate.md"
        path.write_text(content, encoding="utf-8")
        return memory_schema.validate_card(path)


def _load_jsonl(path: Path) -> list[tuple[int, dict[str, Any]]]:
    rows: list[tuple[int, dict[str, Any]]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            rows.append((line_no, {"_malformed": line}))
            continue
        if isinstance(payload, dict):
            rows.append((line_no, payload))
    return rows


def build_plan(brain_root: Path, run_dir: Path, input_log: Path, observed_at: str, source_id: str) -> dict[str, Any]:
    ic.ensure_run_dir(run_dir)
    rows = _load_jsonl(input_log)
    actions: list[dict[str, Any]] = []
    review_dir = brain_root / "memory" / "review-queue"
    journal_path = brain_root / "memory" / "journal" / f"{observed_at}.md"
    for line_no, record in rows:
        if "_malformed" in record:
            actions.append({"action": "hold", "line": line_no, "reason": "malformed json"})
            continue
        text = _record_text(record)
        kind, claim, reason = _extract_signal(text)
        if kind in {"noop", "hold"}:
            actions.append({"action": kind, "line": line_no, "reason": reason})
            continue
        entry = _entry(record)
        source = _source(record, source_id, line_no)
        content = _card_body(kind, claim, source, observed_at, entry, text)
        errors, warnings = _validate_content(content)
        if errors:
            actions.append({"action": "hold", "line": line_no, "reason": "schema errors", "errors": errors})
            continue
        digest = hashlib.sha1(f"{kind}\n{claim}\n{source}".encode("utf-8")).hexdigest()[:10]
        filename = f"{observed_at}-{_slug(claim)}-{digest}.md"
        actions.append(
            {
                "action": "write_candidate",
                "line": line_no,
                "card_type": kind,
                "claim": claim,
                "reason": reason,
                "entry": entry,
                "source": source,
                "dest": str(review_dir / filename),
                "content": content,
                "warnings": warnings,
            }
        )
    write_count = sum(1 for action in actions if action["action"] == "write_candidate")
    if write_count:
        actions.append(
            {
                "action": "append_journal",
                "dest": str(journal_path),
                "entry": "gateway",
                "source": source_id,
                "content": f"- [entry: gateway] [source: {source_id}] 生成 {write_count} 条候选记忆，等待 review-queue 人工确认。",
            }
        )
    return {
        "run_id": ic.run_id_from_dir(run_dir),
        "generated_at": ic.utc_now(),
        "brain_root": str(brain_root.expanduser().resolve()),
        "input_log": str(input_log.expanduser().resolve()),
        "observed_at": observed_at,
        "source_id": source_id,
        "summary": {
            "records": len(rows),
            "write_candidate": write_count,
            "noop": sum(1 for action in actions if action["action"] == "noop"),
            "hold": sum(1 for action in actions if action["action"] == "hold"),
        },
        "actions": actions,
    }


def apply_plan(plan: dict[str, Any], approved_plan: Path) -> dict[str, Any]:
    if not approved_plan.exists():
        raise FileNotFoundError(f"approved plan not found: {approved_plan}")
    written: list[str] = []
    appended: list[str] = []
    for action in plan.get("actions", []):
        if action.get("action") == "write_candidate":
            dest = Path(action["dest"]).expanduser().resolve()
            if "memory/review-queue" not in dest.as_posix():
                raise ValueError(f"candidate destination outside review-queue: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists():
                dest.write_text(action["content"], encoding="utf-8")
            errors, _warnings = memory_schema.validate_card(dest)
            if errors:
                raise ValueError(f"schema errors after write for {dest}: {errors}")
            written.append(str(dest))
        elif action.get("action") == "append_journal":
            dest = Path(action["dest"]).expanduser().resolve()
            if "memory/journal" not in dest.as_posix():
                raise ValueError(f"journal destination outside memory/journal: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            line = action["content"].rstrip() + "\n"
            existing = dest.read_text(encoding="utf-8") if dest.exists() else f"# {dest.stem}\n\n"
            if line not in existing:
                dest.write_text(existing.rstrip() + "\n\n" + line, encoding="utf-8")
            appended.append(str(dest))
    return {"ok": True, "approved_plan": str(approved_plan), "written": written, "appended": appended}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M9 memory-loop candidate extractor")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="write plan JSON under run-dir")
    mode.add_argument("--apply", action="store_true", help="apply an approved plan")
    parser.add_argument("--approved-plan", type=Path, help="approved plan JSON path")
    parser.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--input-log", type=Path, required=True)
    parser.add_argument("--date", default=None)
    parser.add_argument("--source-id", default=None)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_dir = args.run_dir.expanduser().resolve()
    input_log = args.input_log.expanduser().resolve()
    source_id = args.source_id or input_log.name
    observed_at = _date(args.date)
    plan_path = run_dir / "m9-extract-plan.json"

    if args.plan:
        plan = build_plan(args.brain_root, run_dir, input_log, observed_at, source_id)
        ic.write_json(plan_path, plan)
        print(json.dumps({"ok": True, "plan": str(plan_path), "summary": plan["summary"]}, ensure_ascii=False))
        return 0

    if not args.approved_plan:
        print(json.dumps({"ok": False, "errors": ["--apply requires --approved-plan"]}, ensure_ascii=False))
        return 2
    approved_plan = args.approved_plan.expanduser().resolve()
    plan = ic.read_json(approved_plan)
    result = apply_plan(plan, approved_plan)
    ic.write_json(run_dir / "M9-apply-log.json", result)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
