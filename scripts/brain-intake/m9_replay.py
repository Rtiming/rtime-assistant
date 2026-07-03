#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Replay/rollback helper for M9 auto-merge audit events.

Rollback is archive-only: auto-created cards are moved into
memory/_archive/replay-rollback/<date>/ if their sha256 still matches the audit
event. Review-queue candidates are preserved by design.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import shutil
import sys
from pathlib import Path
from typing import Any

import intake_common as ic


DEFAULT_BRAIN_ROOT = Path.home() / "OrangePi-Store" / "sync" / "brain"
DEFAULT_RUN_DIR = Path("work/pipeline/run-05")


def _utc() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


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


def _already_replayed(rows: list[dict[str, Any]], dest: str) -> bool:
    return any(row.get("action") == "replay_auto_merge" and row.get("original_dest") == dest for row in rows)


def build_plan(brain_root: Path, run_dir: Path, audit_log: Path, observed_at: str | None, limit: int) -> dict[str, Any]:
    brain_root = ic.resolve_path(brain_root)
    run_dir = ic.resolve_path(run_dir)
    ic.ensure_run_dir(run_dir)
    rows = _load_jsonl(audit_log)
    actions: list[dict[str, Any]] = []
    replayed_rows = rows
    for row in rows:
        if row.get("action") != "auto_merge" or row.get("status") != "applied":
            continue
        dest_raw = str(row.get("dest") or "")
        if not dest_raw or _already_replayed(replayed_rows, dest_raw):
            continue
        dest = Path(dest_raw).resolve()
        try:
            ic.ensure_inside(brain_root / "memory" / "cards", dest)
        except ValueError:
            actions.append({"action": "hold", "reason": "dest outside memory/cards", "dest": str(dest)})
            continue
        if not dest.exists():
            actions.append({"action": "noop_missing", "reason": "dest already absent", "dest": str(dest)})
            continue
        current_sha = ic.sha256_file(dest)
        if current_sha != row.get("dest_sha256"):
            actions.append(
                {
                    "action": "hold",
                    "reason": "dest changed since auto_merge",
                    "dest": str(dest),
                    "expected_sha256": row.get("dest_sha256"),
                    "current_sha256": current_sha,
                }
            )
            continue
        replay_date = observed_at or dt.datetime.now().strftime("%Y-%m-%d")
        archive = brain_root / "memory" / "_archive" / "replay-rollback" / replay_date / dest.name
        actions.append(
            {
                "action": "archive_auto_card",
                "source": str(dest),
                "dest": str(archive),
                "source_sha256": current_sha,
                "original_audit_ts": row.get("ts"),
                "original_source": row.get("source"),
            }
        )
        if len([a for a in actions if a["action"] == "archive_auto_card"]) >= limit:
            break
    return {
        "run_id": ic.run_id_from_dir(run_dir),
        "generated_at": _utc(),
        "brain_root": str(brain_root),
        "run_dir": str(run_dir),
        "audit_log": str(audit_log),
        "summary": {
            "actions": len(actions),
            "archive_auto_card": sum(1 for item in actions if item["action"] == "archive_auto_card"),
            "hold": sum(1 for item in actions if item["action"] == "hold"),
        },
        "actions": actions,
    }


def apply_plan(plan: dict[str, Any], approved_plan: Path) -> dict[str, Any]:
    if not approved_plan.exists():
        raise FileNotFoundError(f"approved plan not found: {approved_plan}")
    brain_root = Path(plan["brain_root"]).resolve()
    audit_log = Path(plan["audit_log"]).resolve()
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for action in plan.get("actions", []):
        kind = action.get("action")
        if kind == "archive_auto_card":
            source = Path(action["source"]).resolve()
            dest = Path(action["dest"]).resolve()
            ic.ensure_inside(brain_root / "memory" / "cards", source)
            ic.ensure_inside(brain_root / "memory" / "_archive", dest)
            if not source.exists():
                skipped.append({"action": kind, "source": str(source), "reason": "missing"})
                continue
            current_sha = ic.sha256_file(source)
            if current_sha != action.get("source_sha256"):
                raise ValueError(f"source changed before replay: {source}")
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.exists():
                raise ValueError(f"archive destination already exists: {dest}")
            shutil.move(str(source), str(dest))
            event = {
                "ts": _utc(),
                "run_id": plan["run_id"],
                "action": "replay_auto_merge",
                "status": "applied",
                "original_dest": str(source),
                "archive_dest": str(dest),
                "source_sha256": current_sha,
                "queue_preserved": True,
            }
            with audit_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            applied.append(event)
        elif kind in {"hold", "noop_missing"}:
            skipped.append({"action": kind, "reason": action.get("reason"), "dest": action.get("dest")})
        else:
            raise ValueError(f"unknown action: {kind}")
    return {"ok": True, "approved_plan": str(approved_plan), "applied": applied, "skipped": skipped}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay M9 auto-merge actions")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true", help="write replay plan")
    group.add_argument("--apply", action="store_true", help="apply approved replay plan")
    parser.add_argument("--approved-plan", type=Path)
    parser.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--audit-log", type=Path, required=True)
    parser.add_argument("--date")
    parser.add_argument("--limit", type=int, default=1)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    run_dir = args.run_dir.expanduser().resolve()
    plan_path = run_dir / "m9-replay-plan.json"
    if args.plan:
        plan = build_plan(args.brain_root, run_dir, args.audit_log.expanduser().resolve(), args.date, args.limit)
        ic.write_json(plan_path, plan)
        print(json.dumps({"ok": True, "plan": str(plan_path), "summary": plan["summary"]}, ensure_ascii=False))
        return 0
    if not args.approved_plan:
        print(json.dumps({"ok": False, "errors": ["--apply requires --approved-plan"]}, ensure_ascii=False))
        return 2
    approved_plan = args.approved_plan.expanduser().resolve()
    plan = ic.read_json(approved_plan)
    result = apply_plan(plan, approved_plan)
    ic.write_json(run_dir / "M9-replay-apply-log.json", result)
    print(json.dumps({"ok": True, "applied": len(result["applied"]), "skipped": len(result["skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
