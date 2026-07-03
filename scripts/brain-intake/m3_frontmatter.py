#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M3 frontmatter backfill for run-01."""

from __future__ import annotations

from pathlib import Path

import intake_common as ic


def candidates(brain_root: Path) -> list[Path]:
    roots = [brain_root / "knowledge" / "courses", brain_root / "knowledge" / "research"]
    out: list[Path] = []
    for root in roots:
        out.extend(ic.iter_files(root, (".md",)))
    return out


def build_plan(brain_root: Path, run_dir: Path) -> dict:
    actions = []
    for md in candidates(brain_root):
        if ic.has_lock_marker(md):
            actions.append({"action": "hold", "path": ic.rel_to(brain_root, md), "reason": "lock marker"})
            continue
        text = ic.read_text(md)
        fm, body, _had = ic.parse_frontmatter(text)
        defaults = ic.frontmatter_defaults(brain_root, md, body)
        missing = [k for k, v in defaults.items() if k not in fm or fm.get(k, "") == "" and v not in ("", [], None)]
        if missing:
            actions.append({"action": "frontmatter_backfill", "path": ic.rel_to(brain_root, md), "defaults": defaults, "missing": missing})
    return {"run_id": ic.RUN_ID, "generated_at": ic.utc_now(), "actions": actions, "summary": {"frontmatter_backfill": len(actions)}}


def _append_missing_frontmatter(text: str, missing_values: dict) -> str:
    if text.startswith("---\n"):
        end = text.find("\n---\n", 4)
        if end != -1:
            additions = ic.format_frontmatter(missing_values).splitlines()[1:-1]
            before = text[:end]
            after = text[end:]
            return before + "\n" + "\n".join(additions) + after
    return ic.format_frontmatter(missing_values) + text


def apply_plan(brain_root: Path, run_dir: Path, plan: dict) -> dict:
    log = []
    for action in plan["actions"]:
        if action["action"] == "hold":
            log.append({"status": "held", **action})
            continue
        md = brain_root / action["path"]
        text = ic.read_text(md)
        fm, body, had = ic.parse_frontmatter(text)
        missing_values = {}
        for key, value in action["defaults"].items():
            if key not in fm or (fm.get(key, "") == "" and value not in ("", [], None)):
                missing_values[key] = value
        if missing_values:
            backup = run_dir / "backups" / "frontmatter" / Path(action["path"] + ".bak")
            if not backup.exists():
                ic.write_text(backup, text)
            ic.write_text(md, _append_missing_frontmatter(text, missing_values))
            log.append({"status": "done", "path": action["path"], "missing": action["missing"]})
        else:
            log.append({"status": "skipped", "path": action["path"]})
    return {"ok": True, "actions": log, "summary": {s: sum(1 for a in log if a["status"] == s) for s in sorted({a["status"] for a in log})}}


def main() -> int:
    p = ic.parser("M3 frontmatter backfill")
    ic.add_plan_apply(p)
    args = p.parse_args()
    brain_root = ic.resolve_path(args.brain_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    if args.apply:
        plan = ic.read_json(ic.require_approved_plan(args, "frontmatter-plan.json"))
        result = apply_plan(brain_root, run_dir, plan)
        ic.write_json(run_dir / "M3-log.json", result)
        summary = result["summary"]
    else:
        plan = build_plan(brain_root, run_dir)
        ic.write_json(run_dir / "frontmatter-plan.json", plan)
        ic.write_json(run_dir / "M3-log.json", {"ok": True, "mode": "plan", "summary": plan["summary"]})
        summary = plan["summary"]
    ic.markdown_report(
        run_dir / "M3-报告.md",
        "M3 frontmatter回填报告",
        [("做了什么", [f"{k}: {v}" for k, v in summary.items()]), ("跳过什么", []), ("异常", [])],
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
