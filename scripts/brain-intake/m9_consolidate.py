#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M9 memory-loop consolidation jobs for run-05.

The script is deliberately conservative. It supports an explicit plan/apply
flow, writes audit JSONL for every state-changing action, never edits
knowledge/, and never hard-deletes memory cards. Nightly auto-merge only copies
review-queue candidates that satisfy the run-05 four-part gate:
user-stated + no conflict + normal sensitivity + non-assistant-behavior.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any

import intake_common as ic
import memory_schema


DEFAULT_BRAIN_ROOT = Path.home() / "OrangePi-Store" / "sync" / "brain"
DEFAULT_RUN_DIR = Path("work/pipeline/run-05")
DEFAULT_REMINDERS_PATH = Path.home() / "OrangePi-Store" / "sync" / "brain" / "_system" / "reminders.jsonl"
BEIJING = dt.timezone(dt.timedelta(hours=8))
SENSITIVE_RE = re.compile(r"身份证|护照|银行卡|手机号|电话号码|住址|地址|密码|口令|token|api[_ -]?key|secret", re.I)
ASSISTANT_BEHAVIOR_RE = re.compile(r"assistant-behavior|助手行为|系统提示|system prompt|prompt layer", re.I)
DIRECTIVE_RE = re.compile(r"永远|必须|一律|绝不")


def _today(raw: str | None) -> str:
    return raw or dt.datetime.now(BEIJING).date().isoformat()


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sha_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def _jsonl_append(path: Path, record: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def _load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            rows.append(payload)
    return rows


def _json_file_equals(path: Path, payload: Any) -> bool:
    if not path.exists():
        return False
    try:
        existing = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    return existing == payload


def _parse_card(path: Path) -> dict[str, Any]:
    text = _read_text(path)
    fm, parse_err = memory_schema.parse_frontmatter(text)
    errors, warnings = memory_schema.validate_card(path)
    return {
        "path": path,
        "frontmatter": fm,
        "parse_error": parse_err,
        "errors": errors,
        "warnings": warnings,
        "text": text,
        "sha256": _sha_text(text),
    }


def _iter_cards(root: Path) -> list[dict[str, Any]]:
    if not root.exists():
        return []
    cards: list[dict[str, Any]] = []
    for path in sorted(root.glob("*.md")):
        if path.name == "README.md":
            continue
        cards.append(_parse_card(path))
    return cards


def _claim_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"[，。,.；;:：\"'“”‘’（）()\\[\\]【】]", "", text)
    return text


def _redacted_claim(value: Any) -> str:
    claim = str(value or "").strip()
    if SENSITIVE_RE.search(claim):
        return "<sensitive-redacted>"
    return claim[:120]


def _slug(value: str) -> str:
    asciiish = re.sub(r"[^A-Za-z0-9]+", "-", value).strip("-").lower()
    if asciiish:
        return asciiish[:40]
    return hashlib.sha1(value.encode("utf-8")).hexdigest()[:12]


def _candidate_gate(candidate: dict[str, Any], active_keys: set[str]) -> tuple[bool, dict[str, Any]]:
    fm = candidate["frontmatter"]
    claim = str(fm.get("claim") or "")
    key = _claim_key(claim)
    reasons: list[str] = []
    if candidate["parse_error"] or candidate["errors"]:
        reasons.append("schema-invalid")
    if fm.get("type") != "memory-card":
        reasons.append("not-memory-card")
    if fm.get("confidence") != "user-stated":
        reasons.append("confidence-not-user-stated")
    if fm.get("sensitivity", "normal") != "normal":
        reasons.append("sensitivity-not-normal")
    if ASSISTANT_BEHAVIOR_RE.search(str(fm.get("scope") or "")) or ASSISTANT_BEHAVIOR_RE.search(claim):
        reasons.append("assistant-behavior")
    if DIRECTIVE_RE.search(claim):
        reasons.append("directive-warning")
    if not key:
        reasons.append("empty-claim")
    if key and key in active_keys:
        reasons.append("duplicate-or-conflict")
    gate = {
        "user_stated": fm.get("confidence") == "user-stated",
        "no_conflict": bool(key) and key not in active_keys,
        "sensitivity_normal": fm.get("sensitivity", "normal") == "normal",
        "non_assistant_behavior": "assistant-behavior" not in reasons,
    }
    return not reasons, {"gate": gate, "reasons": reasons, "claim_key": key}


def _importance(claim: str) -> int:
    score = 4
    if any(word in claim for word in ("偏好", "希望", "更喜欢", "以后", "下次")):
        score += 2
    if any(word in claim for word in ("错", "纠正", "别", "不要")):
        score += 2
    if len(claim) > 40:
        score += 1
    return max(1, min(10, score))


def _planned_once(audit_rows: list[dict[str, Any]], action: str, source: str | None = None, dest: str | None = None) -> bool:
    for row in audit_rows:
        if row.get("action") != action or row.get("status") != "applied":
            continue
        if source and row.get("source") != source:
            continue
        if dest and row.get("dest") != dest:
            continue
        return True
    return False


def _weekly_due(observed_at: str) -> str:
    date = dt.date.fromisoformat(observed_at)
    due = dt.datetime.combine(date, dt.time(hour=21, minute=30), tzinfo=BEIJING)
    return due.isoformat()


def _weekly_id(observed_at: str) -> str:
    date = dt.date.fromisoformat(observed_at)
    iso = date.isocalendar()
    return f"m9-weekly-report-{iso.year}-W{iso.week:02d}"


def _target_from_env() -> tuple[str | None, str]:
    explicit = os.environ.get("RTIME_REMINDER_DEFAULT_TARGET", "").strip()
    if explicit:
        return explicit, "RTIME_REMINDER_DEFAULT_TARGET"
    allowed = [item.strip() for item in os.environ.get("ALLOWED_USERS", "").split(",") if item.strip()]
    if len(allowed) == 1 and allowed[0].startswith("ou_"):
        return allowed[0], "ALLOWED_USERS"
    return None, "missing"


def _target_from_existing_reminders(reminders_path: Path) -> tuple[str | None, str]:
    rows = _load_jsonl(reminders_path)
    for row in reversed(rows):
        target = str(row.get("target") or "").strip()
        if target.startswith("ou_"):
            return target, "existing-reminder-target"
    return None, "missing"


def _weekly_report(cards: list[dict[str, Any]], hypotheses: list[dict[str, Any]], failed_queries: list[dict[str, Any]]) -> str:
    normal_cards = [
        card
        for card in cards
        if card["frontmatter"].get("type") == "memory-card"
        and card["frontmatter"].get("sensitivity", "normal") == "normal"
    ]
    learned = [_redacted_claim(card["frontmatter"].get("claim")) for card in normal_cards[:8]]
    pending = [
        _redacted_claim(card["frontmatter"].get("claim"))
        for card in hypotheses
        if card["frontmatter"].get("type") == "hypothesis"
        and card["frontmatter"].get("sensitivity", "normal") == "normal"
        and card["frontmatter"].get("status") == "testing"
    ][:8]
    top_failed = []
    for item in failed_queries[-20:]:
        excerpt = str(item.get("query_excerpt") or item.get("query") or "").strip()
        if excerpt and not SENSITIVE_RE.search(excerpt):
            top_failed.append(excerpt[:80])
    lines = [f"本周学到{len(learned)}件事+{len(pending)}条待确认"]
    lines.append("")
    lines.append("本周学到的事：")
    if learned:
        lines.extend(f"- {claim}" for claim in learned)
    else:
        lines.append("- 暂无可自动汇总的 normal 记忆卡。")
    lines.append("")
    lines.append("待你确认：")
    if pending:
        lines.extend(f"- 是否确认：{claim}？" for claim in pending)
    else:
        lines.append("- 暂无待确认假设。")
    lines.append("")
    lines.append("记忆库体检：")
    lines.append(f"- cards: {len(cards)}")
    lines.append(f"- hypotheses: {len(hypotheses)}")
    lines.append(f"- failed_queries_top3: {len(top_failed[:3])}")
    lines.extend(f"  - {query}" for query in top_failed[:3])
    return "\n".join(lines).rstrip() + "\n"


def build_plan(
    *,
    brain_root: Path,
    run_dir: Path,
    state_dir: Path,
    mode: str,
    observed_at: str,
    reminders_path: Path,
    failed_queries: Path | None,
    allow_existing_reminder_target: bool,
) -> dict[str, Any]:
    brain_root = ic.resolve_path(brain_root)
    run_dir = ic.resolve_path(run_dir)
    state_dir = ic.resolve_path(state_dir)
    reminders_path = reminders_path.expanduser()
    ic.ensure_run_dir(run_dir)

    memory_root = brain_root / "memory"
    review_dir = memory_root / "review-queue"
    cards_dir = memory_root / "cards"
    hypotheses_dir = memory_root / "hypotheses"
    audit_log = state_dir / "audit.jsonl"
    audit_rows = _load_jsonl(audit_log)

    cards = _iter_cards(cards_dir)
    candidates = _iter_cards(review_dir)
    hypotheses = _iter_cards(hypotheses_dir)
    active_keys = {_claim_key(card["frontmatter"].get("claim")) for card in cards if card["frontmatter"].get("claim")}
    actions: list[dict[str, Any]] = []

    if mode == "nightly":
        derived_rows: list[dict[str, Any]] = []
        for candidate in candidates:
            fm = candidate["frontmatter"]
            source = candidate["path"]
            dest = cards_dir / source.name
            if dest.exists():
                if _sha_file(dest) == candidate["sha256"]:
                    actions.append(
                        {
                            "action": "noop_existing",
                            "reason": "card already copied",
                            "source": str(source),
                            "dest": str(dest),
                            "source_sha256": candidate["sha256"],
                        }
                    )
                else:
                    actions.append(
                        {
                            "action": "hold",
                            "reason": "destination exists with different content",
                            "source": str(source),
                            "dest": str(dest),
                            "source_sha256": candidate["sha256"],
                        }
                    )
                continue
            ok, gate_info = _candidate_gate(candidate, active_keys)
            if ok:
                if _planned_once(audit_rows, "auto_merge", source=str(source), dest=str(dest)):
                    actions.append(
                        {
                            "action": "noop_existing",
                            "reason": "audit already has applied auto_merge",
                            "source": str(source),
                            "dest": str(dest),
                        }
                    )
                    continue
                actions.append(
                    {
                        "action": "auto_merge",
                        "source": str(source),
                        "dest": str(dest),
                        "source_sha256": candidate["sha256"],
                        "gate": gate_info["gate"],
                        "rollback": {"script": "m9_replay.py", "mode": "archive-auto-card", "queue_preserved": True},
                    }
                )
                active_keys.add(gate_info["claim_key"])
                claim = str(fm.get("claim") or "")
                derived_rows.append(
                    {
                        "source": str(source.relative_to(memory_root)),
                        "claim": _redacted_claim(claim),
                        "importance": _importance(claim),
                        "sensitivity": "normal",
                    }
                )
            else:
                actions.append(
                    {
                        "action": "keep_in_review_queue",
                        "source": str(source),
                        "reason": ",".join(gate_info["reasons"]),
                        "gate": gate_info["gate"],
                    }
                )
        derived_path = state_dir / "derived" / f"{observed_at}-nightly-importance.json"
        derived_content = {
            "mode": mode,
            "date": observed_at,
            "items": derived_rows,
            "privacy": {"sensitive_claims_redacted": True},
        }
        if _json_file_equals(derived_path, derived_content):
            actions.append({"action": "noop_existing", "reason": "derived index already current", "dest": str(derived_path)})
        else:
            actions.append(
                {
                    "action": "write_derived_index",
                    "dest": str(derived_path),
                    "content": derived_content,
                }
            )

    elif mode == "weekly":
        failed_rows = _load_jsonl(failed_queries) if failed_queries else []
        report = _weekly_report(cards, hypotheses, failed_rows)
        report_path = run_dir / "weekly-report-draft.md"
        if report_path.exists() and _read_text(report_path) == report:
            actions.append({"action": "noop_existing", "reason": "weekly report already current", "dest": str(report_path)})
        else:
            actions.append({"action": "write_weekly_report", "dest": str(report_path), "content": report})
        target, target_source = _target_from_env()
        if not target and allow_existing_reminder_target:
            target, target_source = _target_from_existing_reminders(reminders_path)
        reminder_id = _weekly_id(observed_at)
        existing = _load_jsonl(reminders_path)
        if not target:
            actions.append(
                {
                    "action": "hold",
                    "reason": "missing Feishu reminder target",
                    "dest": str(reminders_path),
                    "privacy": {"target_value_returned": False},
                }
            )
        elif any(str(item.get("id")) == reminder_id for item in existing):
            actions.append(
                {
                    "action": "noop_existing",
                    "reason": "weekly reminder already exists",
                    "dest": str(reminders_path),
                    "id": reminder_id,
                    "privacy": {"target_value_returned": False},
                }
            )
        else:
            actions.append(
                {
                    "action": "append_weekly_reminder",
                    "dest": str(reminders_path),
                    "record": {
                        "id": reminder_id,
                        "created": dt.datetime.now(BEIJING).isoformat(timespec="seconds"),
                        "due": _weekly_due(observed_at),
                        "repeat": "none",
                        "message": report,
                        "status": "pending",
                    },
                    "message_sha256": _sha_text(report),
                    "target_source": target_source,
                    "privacy": {"target_set": True, "target_value_returned": False, "message_text_in_plan": True},
                }
            )
    else:
        raise ValueError(f"unsupported mode: {mode}")

    return {
        "run_id": ic.run_id_from_dir(run_dir),
        "mode": mode,
        "date": observed_at,
        "generated_at": _utc(),
        "brain_root": str(brain_root),
        "run_dir": str(run_dir),
        "state_dir": str(state_dir),
        "audit_log": str(audit_log),
        "summary": {
            "actions": len(actions),
            "auto_merge": sum(1 for item in actions if item["action"] == "auto_merge"),
            "hold": sum(1 for item in actions if item["action"] == "hold"),
            "noop_existing": sum(1 for item in actions if item["action"] == "noop_existing"),
        },
        "actions": actions,
    }


def apply_plan(plan: dict[str, Any], approved_plan: Path) -> dict[str, Any]:
    if not approved_plan.exists():
        raise FileNotFoundError(f"approved plan not found: {approved_plan}")
    brain_root = Path(plan["brain_root"]).resolve()
    state_dir = Path(plan["state_dir"]).resolve()
    audit_log = Path(plan["audit_log"]).resolve()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []

    for action in plan.get("actions", []):
        kind = action.get("action")
        if kind == "auto_merge":
            source = Path(action["source"]).resolve()
            dest = Path(action["dest"]).resolve()
            ic.ensure_inside(brain_root / "memory" / "review-queue", source)
            ic.ensure_inside(brain_root / "memory" / "cards", dest)
            if not all(action.get("gate", {}).values()):
                raise ValueError(f"unsafe auto_merge gate for {source}")
            text = _read_text(source)
            if _sha_text(text) != action.get("source_sha256"):
                raise ValueError(f"source changed before apply: {source}")
            if dest.exists():
                if _sha_file(dest) == action.get("source_sha256"):
                    skipped.append({"action": kind, "source": str(source), "dest": str(dest), "reason": "already exists"})
                    continue
                raise ValueError(f"destination exists with different content: {dest}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_text(text, encoding="utf-8")
            event = {
                "ts": _utc(),
                "run_id": plan["run_id"],
                "mode": plan["mode"],
                "date": plan["date"],
                "action": "auto_merge",
                "status": "applied",
                "source": str(source),
                "dest": str(dest),
                "source_sha256": action["source_sha256"],
                "dest_sha256": _sha_file(dest),
                "gate": action["gate"],
                "rollback": action["rollback"],
            }
            _jsonl_append(audit_log, event)
            applied.append(event)
        elif kind == "write_derived_index":
            dest = Path(action["dest"]).resolve()
            ic.ensure_inside(state_dir, dest)
            ic.write_json(dest, action["content"])
            event = {
                "ts": _utc(),
                "run_id": plan["run_id"],
                "mode": plan["mode"],
                "date": plan["date"],
                "action": "write_derived_index",
                "status": "applied",
                "dest": str(dest),
                "dest_sha256": _sha_file(dest),
            }
            _jsonl_append(audit_log, event)
            applied.append(event)
        elif kind == "write_weekly_report":
            dest = Path(action["dest"]).resolve()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if not dest.exists() or _read_text(dest) != action["content"]:
                dest.write_text(action["content"], encoding="utf-8")
            event = {
                "ts": _utc(),
                "run_id": plan["run_id"],
                "mode": plan["mode"],
                "date": plan["date"],
                "action": "write_weekly_report",
                "status": "applied",
                "dest": str(dest),
                "dest_sha256": _sha_file(dest),
            }
            _jsonl_append(audit_log, event)
            applied.append(event)
        elif kind == "append_weekly_reminder":
            dest = Path(action["dest"]).expanduser()
            record = dict(action["record"])
            existing = _load_jsonl(dest)
            if any(str(item.get("id")) == str(record.get("id")) for item in existing):
                skipped.append({"action": kind, "dest": str(dest), "reason": "already exists", "id": record.get("id")})
                continue
            target = str(record.get("target") or "").strip()
            if not target:
                target, target_source = _target_from_env()
                if not target and action.get("target_source") == "existing-reminder-target":
                    target, target_source = _target_from_existing_reminders(dest)
                if not target:
                    raise ValueError("weekly reminder target missing at apply time")
                record["target"] = target
            dest.parent.mkdir(parents=True, exist_ok=True)
            with dest.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")) + "\n")
            event = {
                "ts": _utc(),
                "run_id": plan["run_id"],
                "mode": plan["mode"],
                "date": plan["date"],
                "action": "append_weekly_reminder",
                "status": "applied",
                "dest": str(dest),
                "id": record.get("id"),
                "repeat": record.get("repeat"),
                "message_sha256": action.get("message_sha256"),
                "target_set": bool(record.get("target")),
                "target_source": action.get("target_source"),
                "privacy": {"target_value_returned": False},
            }
            _jsonl_append(audit_log, event)
            applied.append(event)
        elif kind in {"keep_in_review_queue", "noop_existing", "hold"}:
            skipped.append({"action": kind, "reason": action.get("reason"), "source": action.get("source")})
        else:
            raise ValueError(f"unknown action: {kind}")

    result = {"ok": True, "approved_plan": str(approved_plan), "applied": applied, "skipped": skipped}
    return result


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="M9 memory-loop consolidation job")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true", help="write a plan and exit")
    group.add_argument("--apply", action="store_true", help="apply an approved plan")
    parser.add_argument("--approved-plan", type=Path, help="approved plan JSON path")
    parser.add_argument("--mode", choices=["nightly", "weekly"], required=True)
    parser.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--state-dir", type=Path)
    parser.add_argument("--date")
    parser.add_argument("--reminders-path", type=Path, default=DEFAULT_REMINDERS_PATH)
    parser.add_argument("--failed-queries", type=Path)
    parser.add_argument("--allow-existing-reminder-target", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_dir = args.run_dir.expanduser().resolve()
    state_dir = (args.state_dir or (run_dir / "runtime-state" / "memory-loop")).expanduser().resolve()
    observed_at = _today(args.date)
    plan_path = run_dir / f"m9-{args.mode}-plan-{observed_at}.json"

    if args.plan:
        plan = build_plan(
            brain_root=args.brain_root,
            run_dir=run_dir,
            state_dir=state_dir,
            mode=args.mode,
            observed_at=observed_at,
            reminders_path=args.reminders_path,
            failed_queries=args.failed_queries,
            allow_existing_reminder_target=args.allow_existing_reminder_target,
        )
        ic.write_json(plan_path, plan)
        print(json.dumps({"ok": True, "plan": str(plan_path), "summary": plan["summary"]}, ensure_ascii=False))
        return 0

    if not args.approved_plan:
        print(json.dumps({"ok": False, "errors": ["--apply requires --approved-plan"]}, ensure_ascii=False))
        return 2
    approved_plan = args.approved_plan.expanduser().resolve()
    plan = ic.read_json(approved_plan)
    result = apply_plan(plan, approved_plan)
    ic.write_json(run_dir / f"M9-{args.mode}-apply-log-{plan['date']}.json", result)
    print(json.dumps({"ok": True, "applied": len(result["applied"]), "skipped": len(result["skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
