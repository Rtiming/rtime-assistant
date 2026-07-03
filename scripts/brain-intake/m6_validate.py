#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M6 validation and summary report for run-01."""

from __future__ import annotations

import concurrent.futures
import json
import os
import subprocess
from pathlib import Path

import intake_common as ic


def iter_suffix_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    suffixes = tuple(s.lower() for s in suffixes)
    skip_names = {"personal-data", "_archive", ".git", ".obsidian", ".stfolder"}
    find_cmd = ["find", str(root)]
    if skip_names:
        find_cmd += ["("]
        for idx, name in enumerate(sorted(skip_names)):
            if idx:
                find_cmd.append("-o")
            find_cmd += ["-name", name]
        find_cmd += [")", "-prune", "-o"]
    find_cmd += ["-type", "f", "("]
    for idx, suffix in enumerate(suffixes):
        if idx:
            find_cmd.append("-o")
        find_cmd += ["-iname", f"*{suffix}"]
    find_cmd += [")", "-print"]
    try:
        proc = subprocess.run(find_cmd, check=False, capture_output=True, text=True)
    except OSError:
        proc = None
    if proc is not None and proc.returncode == 0:
        return sorted(
            path
            for path in (Path(line) for line in proc.stdout.splitlines() if line.strip())
            if not ic.should_skip_path(path)
        )

    paths: list[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in skip_names]
        base = Path(dirpath)
        for name in filenames:
            if name.lower().endswith(suffixes):
                path = base / name
                if not ic.should_skip_path(path):
                    paths.append(path)
    return sorted(paths)


def read_text_prefix(path: Path, max_bytes: int = 64 * 1024) -> str:
    with path.open("rb") as f:
        return f.read(max_bytes).decode("utf-8", errors="replace")


def frontmatter_has_required_fields(path: Path) -> bool:
    try:
        text = read_text_prefix(path)
    except OSError:
        return False
    fm, _body, had = ic.parse_frontmatter(text)
    return bool(had and fm.get("type") and fm.get("status"))


# Controlled status enum (docs/brain-intake-workflow.zh-CN.md). Advisory only:
# surfaced as drift, never folded into `ok`, so it never blocks frictionless intake.
STATUS_ENUM = {"inbox", "filed", "converted", "carded", "reviewed"}


def frontmatter_status_drift(path: Path) -> str | None:
    """Return a non-enum ``status`` value if one is present, else None (advisory)."""
    try:
        text = read_text_prefix(path)
    except OSError:
        return None
    fm, _body, had = ic.parse_frontmatter(text)
    if not had:
        return None
    status = fm.get("status")
    if status and str(status).strip() not in STATUS_ENUM:
        return str(status)
    return None


def count_markdown_refs(root: Path, needle: str, md_files: list[Path]) -> int:
    if not needle:
        return 0
    try:
        rg = subprocess.run(
            [
                "rg",
                "--fixed-strings",
                "--files-with-matches",
                "--glob",
                "*.md",
                "--glob",
                "!personal-data/**",
                "--glob",
                "!_archive/**",
                needle,
                str(root),
            ],
            check=False,
            capture_output=True,
            text=True,
        )
    except OSError:
        rg = None
    if rg is not None and rg.returncode in {0, 1}:
        return len([line for line in rg.stdout.splitlines() if line.strip()])
    return sum(1 for md in md_files if needle in ic.read_text(md))


def count_markdown_refs_many(root: Path, needles: list[str], md_files: list[Path]) -> dict[str, int]:
    counts = {needle: 0 for needle in needles if needle}
    if not counts:
        return counts
    cmd = [
        "rg",
        "--fixed-strings",
        "--files-with-matches",
        "--glob",
        "*.md",
        "--glob",
        "!personal-data/**",
        "--glob",
        "!_archive/**",
    ]
    for needle in counts:
        cmd += ["-e", needle]
    cmd.append(str(root))
    try:
        rg = subprocess.run(cmd, check=False, capture_output=True, text=True)
    except OSError:
        rg = None
    if rg is not None and rg.returncode in {0, 1}:
        matched = [Path(line) for line in rg.stdout.splitlines() if line.strip()]
        for md in matched:
            text = ic.read_text(md)
            for needle in counts:
                if needle in text:
                    counts[needle] += 1
        return counts
    for md in md_files:
        text = ic.read_text(md)
        for needle in counts:
            if needle in text:
                counts[needle] += 1
    return counts


def sha_check_for(job: tuple[str, Path, str | None]) -> tuple[str, str, str | None]:
    rel, path, expected = job
    try:
        actual = ic.sha256_file(path)
    except OSError as exc:
        return ("error", rel, str(exc))
    if expected != actual:
        return ("mismatch", rel, None)
    return ("ok", rel, None)


def validate(brain_root: Path, vault_root: Path, run_dir: Path) -> dict:
    run_id = ic.run_id_from_dir(run_dir)
    entries, invalid = ic.read_manifest(brain_root)
    by_path, by_sha = ic.manifest_maps(entries)
    knowledge_files = iter_suffix_files(brain_root / "knowledge", (".pdf", ".md"))
    knowledge_pdfs = [p for p in knowledge_files if p.suffix.lower() == ".pdf"]
    md_files = [p for p in knowledge_files if p.suffix.lower() == ".md"]
    missing_manifest = []
    sha_mismatch = []
    sha_errors = []
    sha_check_prefixes = ["knowledge/courses/advanced-photonics/"] if run_id == "run-02" else []
    sha_checked = []
    sha_skipped = []
    sha_jobs = []
    for pdf in knowledge_pdfs:
        rel = ic.rel_to(brain_root, pdf)
        entry = by_path.get(rel)
        if not entry:
            missing_manifest.append(rel)
        elif sha_check_prefixes and not any(rel.startswith(prefix) for prefix in sha_check_prefixes):
            sha_skipped.append(rel)
        else:
            sha_checked.append(rel)
            sha_jobs.append((rel, pdf, entry.get("sha256")))
    sha_workers = int(os.environ.get("M6_SHA_WORKERS", "1"))
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, sha_workers)) as executor:
        for status, rel, detail in executor.map(sha_check_for, sha_jobs):
            if status == "mismatch":
                sha_mismatch.append(rel)
            elif status == "error":
                sha_errors.append({"path": rel, "error": detail})
    duplicate_sha = {sha: [e.get("brain_path") for e in items] for sha, items in by_sha.items() if len([i for i in items if i.get("canonical", True)]) > 1}
    frontmatter_workers = int(os.environ.get("M6_FRONTMATTER_WORKERS", "8"))
    md_without_frontmatter = []
    frontmatter_enum_drift = []
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, frontmatter_workers)) as executor:
        frontmatter_results = list(executor.map(frontmatter_has_required_fields, md_files))
        status_drift_results = list(executor.map(frontmatter_status_drift, md_files))
    for md, has_frontmatter in zip(md_files, frontmatter_results):
        if not has_frontmatter:
            md_without_frontmatter.append(ic.rel_to(brain_root, md))
    for md, drift in zip(md_files, status_drift_results):
        if drift:
            frontmatter_enum_drift.append({"path": ic.rel_to(brain_root, md), "status": drift})
    vault_pdfs = [
        ic.rel_to(vault_root, p)
        for p in iter_suffix_files(vault_root / "课程", (".pdf",))
        if "热力学与统计物理资料" in p.parts
    ]
    stignore = vault_root / ".stignore"
    stignore_lines = ic.read_text(stignore).splitlines() if stignore.exists() else []
    rename_old_refs = []
    rename_olds = []
    for rename_map in sorted((brain_root / "_indexes").glob("rename-map-*.jsonl")):
        for line in ic.read_text(rename_map).splitlines():
            if not line.strip():
                continue
            item = json.loads(line)
            old = item.get("old_path") or item.get("old")
            if old:
                rename_olds.append(old)
    rename_counts = count_markdown_refs_many(brain_root / "knowledge", rename_olds, md_files)
    for old in rename_olds:
        count = rename_counts.get(old, 0)
        if count:
            rename_old_refs.append({"old": old, "count": count})
    checks = {
        "manifest_invalid_json": invalid,
        "missing_manifest": missing_manifest,
        "sha_mismatch": sha_mismatch,
        "sha_errors": sha_errors,
        "sha_checked": sha_checked,
        "sha_skipped": sha_skipped,
        "duplicate_canonical_sha256": duplicate_sha,
        "md_without_frontmatter": md_without_frontmatter,
        "frontmatter_enum_drift": frontmatter_enum_drift,
        "vault_thermal_pdf_copies": vault_pdfs,
        "stignore_has_thermal_link": ic.THERMAL_LINK_REL.as_posix() in stignore_lines,
        "stignore_has_solid_link": (Path("课程") / "固体物理资料").as_posix() in stignore_lines,
        "rename_old_refs": rename_old_refs,
    }
    ok = (
        not invalid
        and not missing_manifest
        and not sha_mismatch
        and not sha_errors
        and not duplicate_sha
        and not vault_pdfs
        and checks["stignore_has_thermal_link"]
        and checks["stignore_has_solid_link"]
        and not rename_old_refs
    )
    return {
        "ok": ok,
        "run_id": run_id,
        "generated_at": ic.utc_now(),
        "counts": {
            "knowledge_pdfs": len(knowledge_pdfs),
            "manifest_entries": len(entries),
            "md_without_frontmatter": len(md_without_frontmatter),
            "sha_checked": len(sha_checked),
            "sha_skipped": len(sha_skipped),
        },
        "checks": checks,
    }


def write_total_report(run_dir: Path, result: dict) -> None:
    module_reports = []
    for name in ["M0", "M1", "M2", "M3", "M4", "M5", "M6"]:
        path = run_dir / f"{name}-报告.md"
        module_reports.append(f"- {name}: {'存在' if path.exists() else '缺失'}")
    checks = result["checks"]
    holds = []
    for plan_name in ["triage-plan.json", "registry-plan.json", "frontmatter-plan.json", "link-plan.json"]:
        path = run_dir / plan_name
        if path.exists():
            payload = ic.read_json(path)
            for action in payload.get("actions", payload.get("items", [])):
                if action.get("action") == "hold":
                    holds.append(f"{plan_name}: {action.get('path') or action.get('rel_path')} - {action.get('reason')}")
    matrix = [
        f"manifest invalid json: {len(checks['manifest_invalid_json'])}",
        f"missing manifest: {len(checks['missing_manifest'])}",
        f"sha mismatch: {len(checks['sha_mismatch'])}",
        f"sha errors: {len(checks['sha_errors'])}",
        f"sha checked: {len(checks['sha_checked'])}",
        f"sha skipped: {len(checks['sha_skipped'])}",
        f"duplicate canonical sha256: {len(checks['duplicate_canonical_sha256'])}",
        f"vault thermal pdf copies: {len(checks['vault_thermal_pdf_copies'])}",
        f"thermal stignore: {checks['stignore_has_thermal_link']}",
        f"solid stignore: {checks['stignore_has_solid_link']}",
    ]
    ic.markdown_report(
        run_dir / "总报告.md",
        f"{result.get('run_id', ic.RUN_ID)} 总报告",
        [
            ("模块报告", module_reports),
            ("验收矩阵", matrix),
            ("遗留问题清单", holds),
            ("结论", ["通过" if result["ok"] else "未完全通过，见M6-validate.json"]),
        ],
    )


def main() -> int:
    p = ic.parser("M6 validate")
    args = p.parse_args()
    brain_root = ic.resolve_path(args.brain_root)
    vault_root = ic.resolve_path(args.vault_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    result = validate(brain_root, vault_root, run_dir)
    ic.write_json(run_dir / "M6-validate.json", result)
    ic.write_json(run_dir / "M6-log.json", result)
    ic.markdown_report(
        run_dir / "M6-报告.md",
        "M6 治理验证报告",
        [
            (
                "做了什么",
                [
                    f"knowledge PDFs: {result['counts']['knowledge_pdfs']}",
                    f"manifest entries: {result['counts']['manifest_entries']}",
                    f"sha checked: {result['counts']['sha_checked']}",
                    f"sha skipped by run scope: {result['counts']['sha_skipped']}",
                ],
            ),
            ("跳过什么", []),
            (
                "异常",
                [
                    k
                    for k, v in result["checks"].items()
                    if v
                    and k
                    not in {
                        "stignore_has_thermal_link",
                        "stignore_has_solid_link",
                        "sha_checked",
                        "sha_skipped",
                    }
                ],
            ),
        ],
    )
    write_total_report(run_dir, result)
    print({"ok": result["ok"], "counts": result["counts"]})
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
