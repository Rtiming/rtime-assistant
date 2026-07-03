#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M1 registry, manifest, and thermal migration for run-01."""

from __future__ import annotations

import json
import re
import shutil
from pathlib import Path
from typing import Any

import intake_common as ic


def _pdfs_under(brain_root: Path) -> list[Path]:
    return [p for p in ic.iter_files(brain_root / "knowledge", (".pdf",)) if "_archive" not in p.parts]


def _plan_manifest(brain_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    by_path, _by_sha = ic.manifest_maps(entries)
    for pdf in _pdfs_under(brain_root):
        rel = ic.rel_to(brain_root, pdf)
        current = by_path.get(rel)
        new_entry = ic.manifest_entry(brain_root, pdf, current)
        if not current:
            actions.append({"action": "manifest_add", "brain_path": rel, "entry": new_entry})
        else:
            comparable = dict(current)
            comparable.pop("_line", None)
            diff_keys = [k for k, v in new_entry.items() if comparable.get(k) != v and k in {"sha256", "md_path", "obsidian_note"}]
            if diff_keys:
                actions.append(
                    {
                        "action": "manifest_update",
                        "brain_path": rel,
                        "entry": {**comparable, **{k: new_entry[k] for k in diff_keys}, "updated_at": ic.utc_now()},
                        "diff_keys": diff_keys,
                    }
                )
    return actions


def _plan_renames(brain_root: Path, entries: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_path, _ = ic.manifest_maps(entries)
    actions: list[dict[str, Any]] = []
    for pdf in _pdfs_under(brain_root):
        if by_path.get(ic.rel_to(brain_root, pdf), {}).get("attachment_mode") == "canonical-linked":
            continue
        new_name = ic.normalize_filename(pdf.name)
        if new_name == pdf.name:
            continue
        target = pdf.with_name(new_name)
        if target.exists():
            actions.append({"action": "hold", "path": str(pdf), "reason": "rename target exists", "target": str(target)})
            continue
        old_base = pdf.stem
        new_base = Path(new_name).stem
        members = []
        candidates = [
            (pdf.parent / "images" / old_base, pdf.parent / "images" / new_base),
            (pdf.parent / "text" / old_base, pdf.parent / "text" / new_base),
            (pdf.with_suffix(".md"), pdf.with_name(new_base + ".md")),
            (pdf, target),
        ]
        conflict = None
        for old, new in candidates:
            if old.exists():
                if new.exists():
                    conflict = str(new)
                    break
                members.append({"old": ic.rel_to(brain_root, old), "new": ic.rel_to(brain_root, new), "kind": "dir" if old.is_dir() else "file"})
        if conflict:
            actions.append({"action": "hold", "path": str(pdf), "reason": "rename group target exists", "target": conflict})
            continue
        references = []
        for md in ic.iter_files(brain_root / "knowledge", (".md",)):
            references.extend(_reference_hits(brain_root, md, old_base, new_base))
        actions.append(
            {
                "action": "rename_group",
                "old_path": ic.rel_to(brain_root, pdf),
                "new_path": ic.rel_to(brain_root, target),
                "old_base": old_base,
                "new_base": new_base,
                "members": members,
                "references": references,
                "rollback": {"move": [ic.rel_to(brain_root, target), ic.rel_to(brain_root, pdf)]},
            }
        )
    return actions


def _safe_rewrite_line(line: str, old_base: str, new_base: str) -> tuple[str, str | None]:
    old_pdf = old_base + ".pdf"
    new_pdf = new_base + ".pdf"
    old_md = old_base + ".md"
    new_md = new_base + ".md"
    stripped = line.lstrip()
    frontmatter_keys = ("source:", "pdf_file:", "page_image_dir:", "raw_text_dir:", "obsidian_note:")
    if stripped.startswith(frontmatter_keys):
        return line.replace(old_pdf, new_pdf).replace(old_md, new_md).replace(old_base, new_base), "frontmatter-field"
    if "[[" in line and (old_pdf in line or old_base in line):
        return line.replace(old_pdf, new_pdf).replace(old_base, new_base), "wikilink"
    if re.search(r"!?\[[^\]]*\]\(", line) and (old_pdf in line or old_md in line or old_base in line):
        return line.replace(old_pdf, new_pdf).replace(old_md, new_md).replace(old_base, new_base), "markdown-link"
    if f"images/{old_base}" in line or f"text/{old_base}" in line:
        return line.replace(f"images/{old_base}", f"images/{new_base}").replace(f"text/{old_base}", f"text/{new_base}"), "asset-path"
    if stripped in {f"# {old_base}", f"title: \"{old_base}\"", f"title: {old_base}"}:
        return line.replace(old_base, new_base), "title"
    if old_pdf in line or old_md in line:
        return line.replace(old_pdf, new_pdf).replace(old_md, new_md), "file-list"
    return line, None


def _rewrite_reference_text(text: str, old_base: str, new_base: str) -> tuple[str, list[dict[str, Any]]]:
    changed: list[dict[str, Any]] = []
    out: list[str] = []
    for lineno, line in enumerate(text.splitlines(keepends=True), start=1):
        line_body = line[:-1] if line.endswith("\n") else line
        newline = "\n" if line.endswith("\n") else ""
        rewritten, shape = _safe_rewrite_line(line_body, old_base, new_base)
        if shape and rewritten != line_body:
            changed.append({"line": lineno, "shape": shape})
        out.append(rewritten + newline)
    return "".join(out), changed


def _reference_hits(brain_root: Path, md: Path, old_base: str, new_base: str) -> list[dict[str, Any]]:
    text = ic.read_text(md)
    _rewritten, changes = _rewrite_reference_text(text, old_base, new_base)
    rel = ic.rel_to(brain_root, md)
    return [{"path": rel, **change} for change in changes]


def _reference_path(ref: str | dict[str, Any]) -> str:
    return ref if isinstance(ref, str) else str(ref["path"])


def _apply_rename_group(brain_root: Path, action: dict[str, Any]) -> dict[str, Any]:
    moved_members: dict[str, str] = {}
    moved_stack: list[tuple[Path, Path]] = []
    original_texts: dict[Path, str] = {}
    old_base = action.get("old_base")
    new_base = action.get("new_base")
    try:
        for member in action.get("members", []):
            old = brain_root / member["old"]
            new = brain_root / member["new"]
            if old.exists() and new.exists():
                raise FileExistsError(f"rename target exists: {new}")
        for member in action.get("members", []):
            old = brain_root / member["old"]
            new = brain_root / member["new"]
            if old.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                old.rename(new)
                moved_members[member["old"]] = member["new"]
                moved_stack.append((old, new))
        changed_refs = 0
        if old_base and new_base:
            for ref in action.get("references", []):
                ref_rel = _reference_path(ref)
                ref_path = brain_root / moved_members.get(ref_rel, ref_rel)
                if ref_path.exists():
                    text = ic.read_text(ref_path)
                    rewritten, changes = _rewrite_reference_text(text, old_base, new_base)
                    if rewritten != text:
                        original_texts[ref_path] = text
                        ic.write_text(ref_path, rewritten)
                        changed_refs += len(changes)
        return {"moved": len(moved_stack), "reference_changes": changed_refs}
    except Exception:
        for path, text in original_texts.items():
            ic.write_text(path, text)
        for old, new in reversed(moved_stack):
            if new.exists() and not old.exists():
                old.parent.mkdir(parents=True, exist_ok=True)
                new.rename(old)
        raise


def _rename_map_path(brain_root: Path) -> Path:
    stamp = ic.TODAY.replace("-", "")
    return brain_root / "_indexes" / f"rename-map-{stamp}.jsonl"


def _plan_thermal(brain_root: Path, vault_root: Path) -> list[dict[str, Any]]:
    source_root = vault_root / ic.THERMAL_VAULT_REL
    archive_root = vault_root / ic.THERMAL_ARCHIVE_REL / "热力学与统计物理资料-original"
    if not source_root.exists() or source_root.is_symlink():
        return []
    actions: list[dict[str, Any]] = []
    existing_sha: dict[str, Path] = {}
    for pdf in _pdfs_under(brain_root / "knowledge" / "courses" / ic.THERMAL_COURSE if False else brain_root):
        existing_sha[ic.sha256_file(pdf)] = pdf
    move_count = 0
    duplicate_count = 0
    holds = 0
    for src in ic.iter_files(source_root, (".pdf", ".md")):
        target = ic.thermal_target(vault_root, brain_root, src)
        if not target:
            holds += 1
            actions.append({"action": "hold", "path": str(src), "reason": "thermal target mapping missing"})
            continue
        sha = ic.sha256_file(src) if src.suffix.lower() == ".pdf" else ""
        if src.suffix.lower() == ".pdf" and sha in existing_sha:
            duplicate_count += 1
            continue
        if target.exists():
            if src.suffix.lower() == ".pdf" and sha == ic.sha256_file(target):
                duplicate_count += 1
                continue
            if src.suffix.lower() == ".md" and ic.read_text(src) == ic.read_text(target):
                duplicate_count += 1
                continue
            holds += 1
            actions.append(
                {
                    "action": "hold",
                    "path": str(src),
                    "target": str(target),
                    "reason": "target exists with different content",
                }
            )
            continue
        move_count += 1
        actions.append(
            {
                "action": "move_to_brain",
                "source": str(src),
                "target": str(target),
                "sha256": sha,
                "rollback": {"move": [str(target), str(src)]},
            }
        )
    if holds == 0:
        actions.append(
            {
                "action": "move_vault_duplicate_to_archive",
                "source": str(source_root),
                "target": str(archive_root),
                "duplicate_files": duplicate_count,
                "moved_to_brain": move_count,
                "rollback": {"move": [str(archive_root), str(source_root)]},
            }
        )
    else:
        actions.append(
            {
                "action": "hold",
                "path": str(source_root),
                "reason": f"thermal directory archive blocked by {holds} held items",
            }
        )
    return actions


def _plan_misc(brain_root: Path) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    tmp = brain_root / "_tmp_patch_bridge.py"
    if tmp.exists():
        actions.append(
            {
                "action": "move_vault_duplicate_to_archive",
                "source": str(tmp),
                "target": str(ic.repo_root() / "work" / "recovered" / "_tmp_patch_bridge.py"),
                "reason": "recover stray sync root helper into ignored work",
                "rollback": {"move": [str(ic.repo_root() / "work" / "recovered" / "_tmp_patch_bridge.py"), str(tmp)]},
            }
        )
    interests_readme = brain_root / "knowledge" / "interests" / "README.md"
    if not interests_readme.exists():
        actions.append(
            {
                "action": "readme_update",
                "target": str(interests_readme),
                "content": "# interests\n\n这里保留兴趣主题资料的占位目录。当前 run-01 仅补充 README，未迁入新资料。\n",
                "rollback": {"remove": str(interests_readme)},
            }
        )
    return actions


def build_plan(brain_root: Path, vault_root: Path, run_dir: Path) -> dict[str, Any]:
    entries, invalid = ic.read_manifest(brain_root)
    actions = []
    actions.extend(_plan_manifest(brain_root, entries))
    actions.extend(_plan_renames(brain_root, entries))
    actions.extend(_plan_thermal(brain_root, vault_root))
    actions.extend(_plan_misc(brain_root))
    return {
        "run_id": ic.RUN_ID,
        "generated_at": ic.utc_now(),
        "approved_by": "docs/tasks/pipeline/RUN.md run-01 preauthorized rules",
        "manifest_invalid_lines": invalid,
        "actions": actions,
        "summary": {kind: sum(1 for a in actions if a["action"] == kind) for kind in sorted({a["action"] for a in actions})},
    }


def apply_plan(brain_root: Path, vault_root: Path, run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    entries, invalid = ic.read_manifest(brain_root)
    if invalid:
        raise SystemExit("manifest has invalid JSON lines; refusing apply")
    by_path, _ = ic.manifest_maps(entries)
    log: list[dict[str, Any]] = []
    rename_maps: list[dict[str, Any]] = []
    for action in plan["actions"]:
        kind = action["action"]
        try:
            if kind == "manifest_add":
                if action["brain_path"] not in by_path:
                    entries.append(action["entry"])
                    by_path[action["brain_path"]] = action["entry"]
                    log.append({"action": kind, "status": "done", "brain_path": action["brain_path"]})
                else:
                    log.append({"action": kind, "status": "skipped", "reason": "already present", "brain_path": action["brain_path"]})
            elif kind == "manifest_update":
                idx = next((i for i, e in enumerate(entries) if e.get("brain_path") == action["brain_path"]), None)
                if idx is not None:
                    entries[idx] = action["entry"]
                    log.append({"action": kind, "status": "done", "brain_path": action["brain_path"]})
            elif kind == "rename_group":
                rename_result = _apply_rename_group(brain_root, action)
                old_rel = action["old_path"]
                new_rel = action["new_path"]
                for e in entries:
                    if e.get("brain_path") == old_rel:
                        e["brain_path"] = new_rel
                        e["title"] = str(e.get("title", "")).replace(action.get("old_base", ""), action.get("new_base", ""))
                        e["md_path"] = str(e.get("md_path", "")).replace(action.get("old_base", ""), action.get("new_base", ""))
                        e["updated_at"] = ic.utc_now()
                rename_maps.append(
                    {
                        "schema_version": "rename-map-v1",
                        "run_id": ic.RUN_ID,
                        "old_path": old_rel,
                        "new_path": new_rel,
                        "old_base": action.get("old_base"),
                        "new_base": action.get("new_base"),
                        "updated_at": ic.utc_now(),
                    }
                )
                log.append({"action": kind, "status": "done", "old": old_rel, "new": new_rel, **rename_result})
            elif kind == "move_to_brain":
                source = Path(action["source"])
                target = Path(action["target"])
                if source.exists() and not target.exists():
                    ic.ensure_inside(vault_root, source)
                    ic.ensure_inside(brain_root, target)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    if target.suffix.lower() == ".pdf":
                        rel = ic.rel_to(brain_root, target)
                        entry = ic.manifest_entry(brain_root, target, by_path.get(rel))
                        entries.append(entry)
                        by_path[rel] = entry
                    log.append({"action": kind, "status": "done", "source": str(source), "target": str(target)})
            elif kind == "move_vault_duplicate_to_archive":
                source = Path(action["source"])
                target = Path(action["target"])
                if source.exists() and not target.exists():
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(source), str(target))
                    log.append({"action": kind, "status": "done", "source": str(source), "target": str(target)})
                elif not source.exists():
                    log.append({"action": kind, "status": "skipped", "reason": "source missing", "source": str(source)})
            elif kind == "readme_update":
                target = Path(action["target"])
                if not target.exists():
                    ic.write_text(target, action["content"])
                    log.append({"action": kind, "status": "done", "target": str(target)})
            elif kind == "hold":
                log.append({"action": kind, "status": "held", "reason": action.get("reason"), "path": action.get("path")})
            else:
                log.append({"action": kind, "status": "unknown"})
        except Exception as exc:
            log.append({"action": kind, "status": "failed", "error": str(exc)})
            if kind == "rename_group":
                continue
            raise
    ic.write_manifest(brain_root, entries)
    if rename_maps:
        rename_path = _rename_map_path(brain_root)
        rename_path.parent.mkdir(parents=True, exist_ok=True)
        existing_lines = ic.read_text(rename_path).splitlines() if rename_path.exists() else []
        existing_keys = set()
        for line in existing_lines:
            if not line.strip():
                continue
            try:
                item = json.loads(line)
                existing_keys.add((item.get("old_path"), item.get("new_path")))
            except json.JSONDecodeError:
                continue
        with rename_path.open("a", encoding="utf-8") as f:
            for item in rename_maps:
                key = (item["old_path"], item["new_path"])
                if key not in existing_keys:
                    f.write(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n")
    return {"ok": True, "actions": log, "summary": {s: sum(1 for a in log if a["status"] == s) for s in sorted({a["status"] for a in log})}}


def main() -> int:
    p = ic.parser("M1 registry")
    ic.add_plan_apply(p)
    args = p.parse_args()
    brain_root = ic.resolve_path(args.brain_root)
    vault_root = ic.resolve_path(args.vault_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    if args.apply:
        plan_path = ic.require_approved_plan(args, "registry-plan.json")
        result = apply_plan(brain_root, vault_root, run_dir, ic.read_json(plan_path))
        ic.write_json(run_dir / "M1-log.json", result)
        summary = result["summary"]
    else:
        plan = build_plan(brain_root, vault_root, run_dir)
        ic.write_json(run_dir / "registry-plan.json", plan)
        summary = plan["summary"]
        ic.write_json(run_dir / "M1-log.json", {"ok": True, "mode": "plan", "summary": summary})
    ic.markdown_report(
        run_dir / "M1-报告.md",
        "M1 登记归位报告",
        [
            ("做了什么", [f"{k}: {v}" for k, v in summary.items()]),
            ("跳过什么", []),
            ("异常", []),
        ],
    )
    print(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
