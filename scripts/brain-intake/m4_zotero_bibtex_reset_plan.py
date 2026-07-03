#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Generate a reset-only BibTeX linked-file import package for run-04.

This is intentionally plan-only. Zotero's connector import can create linked
attachments from ``file = {Title:attachments:...:application/pdf}``, but it also
creates new top-level parent items. In the current run-04 state, those parents
already exist, so applying this package directly would duplicate Zotero items.
"""

from __future__ import annotations

import csv
import json
import re
import sys
from pathlib import Path
from typing import Any

import intake_common as ic


DEFAULT_PREFIX = "zotero-bibtex-linked-import-reset-only"


def _clean(value: Any) -> str:
    return str(value or "").replace("\r", " ").replace("\n", " ").strip()


def _bib_value(value: Any) -> str:
    return _clean(value).replace("\\", "\\textbackslash{}").replace("{", "\\{").replace("}", "\\}")


def _bib_key(value: Any, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_:\-.]+", "", _clean(value)) or "paper"
    candidate = base
    index = 2
    while candidate in used:
        candidate = f"{base}{index}"
        index += 1
    used.add(candidate)
    return candidate


def _source_actions(source_plan: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {
        str(action.get("brain_path")): action
        for action in source_plan.get("actions", [])
        if action.get("action") == "zotero_import_linked_pdf" and action.get("brain_path")
    }


def _entry_from_action(action: dict[str, Any], source_meta: dict[str, Any], citekey: str) -> str:
    brain_path = str(action["brain_path"])
    payload = action["payload"]
    title = _clean(source_meta.get("title") or Path(brain_path).stem)
    author = _clean(source_meta.get("author"))
    year = _clean(source_meta.get("year"))
    doi = _clean(source_meta.get("doi"))
    arxiv = _clean(source_meta.get("arxiv"))
    file_field = f"PDF:{payload['path']}:application/pdf"

    lines = [
        f"@article{{{citekey},",
        f"  title = {{{_bib_value(title)}}},",
    ]
    if author:
        lines.append(f"  author = {{{_bib_value(author)}}},")
    if year:
        lines.append(f"  year = {{{_bib_value(year)}}},")
    if doi:
        lines.append(f"  doi = {{{_bib_value(doi)}}},")
    if arxiv:
        lines.append(f"  eprint = {{{_bib_value(arxiv)}}},")
        lines.append("  archivePrefix = {arXiv},")
    lines.extend(
        [
            "  keywords = {run-04导入},",
            f"  note = {{{_bib_value('brain_path: ' + brain_path + '; sha256: ' + str(action['sha256']))}}},",
            f"  file = {{{_bib_value(file_field)}}}",
            "}",
        ]
    )
    return "\n".join(lines)


def build_package(
    brain_root: Path,
    run_dir: Path,
    source_plan_path: Path | None = None,
    webapi_plan_path: Path | None = None,
    prefix: str = DEFAULT_PREFIX,
) -> dict[str, Any]:
    source_plan_path = source_plan_path or run_dir / "zotero-run04-plan.json"
    webapi_plan_path = webapi_plan_path or run_dir / "zotero-linked-file-webapi-plan.json"
    source_plan = ic.read_json(source_plan_path)
    webapi_plan = ic.read_json(webapi_plan_path)
    source_by_path = _source_actions(source_plan)

    bib_entries: list[str] = []
    rows: list[dict[str, Any]] = []
    holds: list[dict[str, Any]] = []
    used_keys: set[str] = set()

    for action in webapi_plan.get("actions", []):
        if action.get("action") != "webapi_create_linked_file_attachment":
            continue
        brain_path = str(action.get("brain_path") or "")
        payload = action.get("payload") or {}
        attachment_path = str(payload.get("path") or "")
        source_meta = source_by_path.get(brain_path, {})
        citekey = _bib_key(action.get("citekey") or source_meta.get("planned_citekey") or Path(brain_path).stem, used_keys)
        pdf_path = brain_root / brain_path

        if not brain_path.startswith("knowledge/research/") or "/papers/" not in brain_path:
            holds.append({"brain_path": brain_path, "reason": "not_research_paper_path"})
        if not attachment_path.startswith("attachments:knowledge/research/"):
            holds.append({"brain_path": brain_path, "reason": "attachment_path_not_relative_brain_placeholder", "attachment_path": attachment_path})
        if not pdf_path.exists():
            holds.append({"brain_path": brain_path, "reason": "pdf_missing", "absolute_path": str(pdf_path)})

        bib_entries.append(_entry_from_action(action, source_meta, citekey))
        rows.append(
            {
                "citekey": citekey,
                "title": _clean(source_meta.get("title") or Path(brain_path).stem),
                "brain_path": brain_path,
                "sha256": str(action.get("sha256") or ""),
                "attachments_path": attachment_path,
                "pdf_exists": pdf_path.exists(),
                "current_zotero_item_key_to_avoid_duplication": str(action.get("zotero_item_key") or ""),
                "planned_mode": "reset_only_bibtex_linked_import",
            }
        )

    bib_path = run_dir / f"{prefix}.bib"
    csv_path = run_dir / f"{prefix}.csv"
    plan_path = run_dir / f"{prefix}-plan.json"
    readme_path = run_dir / f"{prefix}-README.md"

    ic.write_text(bib_path, "\n\n".join(bib_entries) + ("\n" if bib_entries else ""))
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    with csv_path.open("w", newline="", encoding="utf-8") as handle:
        fieldnames = [
            "citekey",
            "title",
            "brain_path",
            "sha256",
            "attachments_path",
            "pdf_exists",
            "current_zotero_item_key_to_avoid_duplication",
            "planned_mode",
        ]
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)

    plan = {
        "run_id": ic.run_id_from_dir(run_dir),
        "created_at": ic.utc_now(),
        "method": "connector_import_bibtex_with_attachments_placeholder",
        "apply_allowed": False,
        "apply_precondition": (
            "Only after run-04导入 collection and its current imported items have "
            "been removed or after a clean Zotero rollback. Otherwise this will "
            "duplicate top-level Zotero items."
        ),
        "source_run04_plan": str(source_plan_path),
        "source_webapi_plan": str(webapi_plan_path),
        "bibtex_file": str(bib_path),
        "csv_file": str(csv_path),
        "summary": {
            "entries": len(rows),
            "pdf_missing": sum(1 for row in rows if not row["pdf_exists"]),
            "unique_citekeys": len({row["citekey"] for row in rows}),
            "duplicate_citekeys": len(rows) - len({row["citekey"] for row in rows}),
            "file_fields": len(rows),
            "all_paths_use_attachments_placeholder": all(
                str(row["attachments_path"]).startswith("attachments:knowledge/research/") for row in rows
            ),
            "holds": len(holds),
        },
        "holds": holds,
        "rollback_note": (
            "Connector import has no command-line delete endpoint in current setup; "
            "apply requires a separate Zotero rollback mechanism before/after testing."
        ),
    }
    ic.write_json(plan_path, plan)
    ic.write_text(
        readme_path,
        "\n".join(
            [
                "# run-04 BibTeX linked import reset-only package",
                "",
                f"Generated: {plan['created_at']}",
                "",
                "This package is not for the current partially applied Zotero state.",
                "It is a reset-only fallback for a clean rerun after the existing",
                "`run-04导入` collection/items are removed by an external rollback path.",
                "",
                "Files:",
                "",
                f"- `{bib_path.name}`: {len(rows)} BibTeX entries with `file = {{PDF:attachments:knowledge/research/...pdf:application/pdf}}`.",
                f"- `{csv_path.name}`: row-level path/citekey/sha evidence.",
                f"- `{plan_path.name}`: machine-readable preconditions and summary.",
                "",
                "Do not apply this while the current Zotero parent items still exist.",
                "`/connector/import` creates new top-level items and would duplicate them.",
                "",
            ]
        ),
    )
    return plan


def main() -> int:
    parser = ic.parser("M4 Zotero reset-only BibTeX linked import package")
    parser.add_argument("--plan", action="store_true", help="generate reset-only package")
    parser.add_argument("--apply", action="store_true", help="refuse; this script is plan-only")
    parser.add_argument("--approved-plan", type=Path, help="accepted for interface symmetry; never applied")
    parser.add_argument("--source-plan", type=Path)
    parser.add_argument("--webapi-plan", type=Path)
    parser.add_argument("--prefix", default=DEFAULT_PREFIX)
    args = parser.parse_args()

    if args.apply:
        print("Refusing --apply: this reset-only package would duplicate existing Zotero parent items.", file=sys.stderr)
        return 2

    brain_root = ic.resolve_path(args.brain_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    plan = build_package(brain_root, run_dir, args.source_plan, args.webapi_plan, args.prefix)
    log = {
        "ok": plan["summary"]["entries"] > 0 and plan["summary"]["holds"] == 0,
        "summary": plan["summary"],
        "plan": str(run_dir / f"{args.prefix}-plan.json"),
    }
    ic.write_json(run_dir / "M4-zotero-bibtex-reset-plan-log.json", log)
    print(json.dumps(log, ensure_ascii=False, indent=2))
    return 0 if log["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
