#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M0 triage for run-01."""

from __future__ import annotations

from pathlib import Path

import intake_common as ic


def build_plan(brain_root: Path, vault_root: Path, run_dir: Path) -> dict:
    ic.ensure_run_dir(run_dir)
    entries, invalid = ic.read_manifest(brain_root)
    by_path, by_sha = ic.manifest_maps(entries)
    items = []

    scopes = [
        ("inbox", brain_root / "_inbox", (".pdf", ".md", ".ppt", ".pptx", ".doc", ".docx", ".png", ".jpg", ".jpeg")),
        ("knowledge", brain_root / "knowledge", (".pdf", ".md")),
        ("thermal-vault", vault_root / ic.THERMAL_VAULT_REL, (".pdf", ".md")),
    ]
    for scope, root, suffixes in scopes:
        for path in ic.iter_files(root, suffixes):
            if ic.has_lock_marker(path):
                action = "hold"
                klass = "skip"
                reason = "lock marker"
                confidence = "high"
            elif path.suffix.lower() == ".pdf":
                sha = ic.sha256_file(path)
                if scope == "knowledge":
                    klass, _meta = ic.classify_pdf(brain_root, path)
                    rel = ic.rel_to(brain_root, path)
                    action = "already-filed"
                    reason = "manifest-present" if rel in by_path or sha in by_sha else "manifest-missing"
                    confidence = "high"
                elif scope == "thermal-vault":
                    klass = "lecture-text"
                    target = ic.thermal_target(vault_root, brain_root, path)
                    action = "migrate" if target else "hold"
                    reason = f"thermal-vault target={target}" if target else "no thermal mapping"
                    confidence = "high" if target else "low"
                else:
                    klass = "skip"
                    action = "hold"
                    reason = "inbox triage needs human/product context"
                    confidence = "low"
            elif path.suffix.lower() == ".md":
                sha = ""
                klass = "note"
                if scope == "thermal-vault":
                    target = ic.thermal_target(vault_root, brain_root, path)
                    action = "migrate" if target else "hold"
                    reason = f"thermal-vault-md target={target}" if target else "no thermal md mapping"
                    confidence = "medium"
                elif scope == "knowledge":
                    action = "already-filed"
                    reason = "frontmatter candidate"
                    confidence = "high"
                else:
                    action = "hold"
                    reason = "inbox note needs target"
                    confidence = "low"
            else:
                sha = ic.sha256_file(path) if path.is_file() else ""
                klass = "skip"
                action = "hold"
                reason = "unsupported in run-01"
                confidence = "low"
            items.append(
                {
                    "path": str(path),
                    "rel_path": ic.rel_to(root, path),
                    "scope": scope,
                    "sha256": sha,
                    "class": klass,
                    "target_dir": "",
                    "action": action,
                    "reason": reason,
                    "confidence": confidence,
                }
            )
    return {
        "run_id": ic.RUN_ID,
        "generated_at": ic.utc_now(),
        "brain_root": str(ic.resolve_path(brain_root)),
        "vault_root": str(ic.resolve_path(vault_root)),
        "manifest_invalid_lines": invalid,
        "items": items,
        "summary": {
            "total": len(items),
            "hold": sum(1 for i in items if i["action"] == "hold"),
            "migrate": sum(1 for i in items if i["action"] == "migrate"),
            "already_filed": sum(1 for i in items if i["action"] == "already-filed"),
        },
    }


def main() -> int:
    p = ic.parser("M0 triage")
    args = p.parse_args()
    brain_root = ic.resolve_path(args.brain_root)
    vault_root = ic.resolve_path(args.vault_root)
    run_dir = args.run_dir
    plan = build_plan(brain_root, vault_root, run_dir)
    ic.write_json(run_dir / "triage-plan.json", plan)
    ic.write_json(run_dir / "M0-log.json", {"ok": True, "summary": plan["summary"], "generated_at": ic.utc_now()})
    ic.markdown_report(
        run_dir / "M0-报告.md",
        "M0 分诊报告",
        [
            ("做了什么", [f"扫描条目 {plan['summary']['total']} 个", f"待迁移 {plan['summary']['migrate']} 个"]),
            ("跳过什么", [f"hold {plan['summary']['hold']} 个"]),
            ("异常", [f"manifest invalid lines: {len(plan['manifest_invalid_lines'])}"] if plan["manifest_invalid_lines"] else []),
        ],
    )
    print(plan["summary"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
