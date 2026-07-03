# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Repository-backed brain-docpack CLI.

The first package version keeps existing `scripts/` entrypoints as the source
of truth. This gives Mac, orangepi, and future skill/plugin/MCP wrappers a
stable command contract while the implementation is migrated in smaller units.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_ASSISTANT_ROOT")
    if env_root:
        roots.append(Path(env_root))

    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    roots.extend([PACKAGE_ROOT, *PACKAGE_ROOT.parents])
    return roots


def find_repo_root() -> Path:
    for root in _candidate_roots():
        if (
            (root / "scripts" / "validate-docpack.py").is_file()
            and (root / "scripts" / "build-docpack.py").is_file()
            and (root / "schemas" / "docpack").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def _run(command: Sequence[str], *, cwd: Path) -> int:
    try:
        completed = subprocess.run(list(command), cwd=cwd, check=False)
    except FileNotFoundError as exc:
        print(f"error: missing command: {exc.filename}", file=sys.stderr)
        return 127
    return completed.returncode


def _python_script(script: str, args: Sequence[str], *, repo: Path) -> int:
    return _run([sys.executable, str(repo / "scripts" / script), *args], cwd=repo)


def _shell_script(script: str, args: Sequence[str], *, repo: Path) -> int:
    return _run([str(repo / "scripts" / script), *args], cwd=repo)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain-docpack",
        description="Audit, build, select, and validate rtime brain DocPacks.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="rtime-assistant repository root; defaults to auto-detect or RTIME_ASSISTANT_ROOT",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    audit = subparsers.add_parser("audit", help="Run the read-only knowledge-material audit.")
    audit.add_argument("root", type=Path, help="brain/knowledge root")
    audit.add_argument("--deep", action="store_true", help="Run deep Office conversion checks")

    samples = subparsers.add_parser("select-samples", help="Select representative DocPack samples.")
    samples.add_argument("root", nargs="?", type=Path, help="brain/knowledge root")
    samples.add_argument("--limit-per-category", type=int, default=1)
    samples.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    build = subparsers.add_parser("build", help="Build a DocPack from a source file.")
    build.add_argument("source", type=Path)
    build.add_argument("--out", type=Path, required=True)
    build.add_argument("--docpack-id", default="")
    build.add_argument("--force", action="store_true")
    build.add_argument("--no-validate", action="store_true")

    validate = subparsers.add_parser("validate", help="Validate a DocPack directory.")
    validate.add_argument("docpack", type=Path)
    validate.add_argument("--json", action="store_true")

    course_intake = subparsers.add_parser(
        "course-intake",
        help="Plan and optionally apply a course-material intake into brain.",
    )
    course_intake.add_argument("source_root", type=Path)
    course_intake.add_argument("--brain-root", type=Path, required=True)
    course_intake.add_argument("--course-id", required=True)
    course_intake.add_argument("--course-title", required=True)
    course_intake.add_argument("--out", type=Path)
    course_intake.add_argument("--include-all", action="store_true")
    course_intake.add_argument("--apply", action="store_true")
    course_intake.add_argument("--approved", action="store_true")
    course_intake.add_argument("--auto-approve", action="store_true")
    course_intake.add_argument("--policy", type=Path)
    course_intake.add_argument("--write-md", action="store_true")
    course_intake.add_argument("--md-max-pages", type=int, default=120)
    course_intake.add_argument("--update-pdf-manifest", action="store_true")
    course_intake.add_argument("--obsidian-note")
    course_intake.add_argument("--obsidian-course-dir", type=Path)
    course_intake.add_argument("--keyword", action="append", default=[])
    course_intake.add_argument("--json", action="store_true")

    course_mirror = subparsers.add_parser(
        "course-mirror-obsidian",
        help="Rebuild a vault-visible course folder from an existing brain course root.",
    )
    course_mirror.add_argument("--brain-root", type=Path, required=True)
    course_mirror.add_argument("--course-id", required=True)
    course_mirror.add_argument("--obsidian-course-dir", type=Path, required=True)
    course_mirror.add_argument("--out", type=Path)
    course_mirror.add_argument("--json", action="store_true")

    course_index = subparsers.add_parser(
        "course-index",
        help="Write materials_index.csv/md for an existing brain course root.",
    )
    course_index.add_argument("--brain-root", type=Path, required=True)
    course_index.add_argument("--course-id", required=True)
    course_index.add_argument("--course-title", required=True)
    course_index.add_argument("--json", action="store_true")

    dialogue_audit = subparsers.add_parser(
        "dialogue-audit-template",
        help="Write a reusable Obsidian course-intake dialogue audit report template.",
    )
    dialogue_audit.add_argument("--course-id", required=True)
    dialogue_audit.add_argument("--course-title", required=True)
    dialogue_audit.add_argument("--source-root", type=Path, required=True)
    dialogue_audit.add_argument("--brain-root", type=Path, required=True)
    dialogue_audit.add_argument("--entry", default="obsidian")
    dialogue_audit.add_argument("--executor", default="kimi")
    dialogue_audit.add_argument("--out", type=Path, required=True)

    subparsers.add_parser("mcp", help="Run the read-only MCP stdio server.")
    subparsers.add_parser("doctor", help="Show resolved repo and required script paths.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        repo = args.repo_root.resolve() if args.repo_root else find_repo_root()
    except RuntimeError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if args.command == "audit":
        audit_args = [str(args.root)]
        if args.deep:
            audit_args.append("--deep")
        return _shell_script("audit-knowledge-materials.sh", audit_args, repo=repo)

    if args.command == "select-samples":
        sample_args: list[str] = []
        if args.root is not None:
            sample_args.append(str(args.root))
        sample_args.extend(["--limit-per-category", str(args.limit_per_category)])
        if args.json:
            sample_args.append("--json")
        return _python_script("select-docpack-samples.py", sample_args, repo=repo)

    if args.command == "build":
        build_args = [str(args.source), "--out", str(args.out)]
        if args.docpack_id:
            build_args.extend(["--docpack-id", args.docpack_id])
        if args.force:
            build_args.append("--force")
        if args.no_validate:
            build_args.append("--no-validate")
        return _python_script("build-docpack.py", build_args, repo=repo)

    if args.command == "validate":
        validate_args = [str(args.docpack)]
        if args.json:
            validate_args.append("--json")
        return _python_script("validate-docpack.py", validate_args, repo=repo)

    if args.command == "course-intake":
        from .course_intake import main as course_intake_main

        intake_args = [
            str(args.source_root),
            "--brain-root",
            str(args.brain_root),
            "--course-id",
            args.course_id,
            "--course-title",
            args.course_title,
        ]
        if args.out:
            intake_args.extend(["--out", str(args.out)])
        if args.include_all:
            intake_args.append("--include-all")
        if args.apply:
            intake_args.append("--apply")
        if args.approved:
            intake_args.append("--approved")
        if args.auto_approve:
            intake_args.append("--auto-approve")
        if args.policy:
            intake_args.extend(["--policy", str(args.policy)])
        if args.write_md:
            intake_args.append("--write-md")
        intake_args.extend(["--md-max-pages", str(args.md_max_pages)])
        if args.update_pdf_manifest:
            intake_args.append("--update-pdf-manifest")
        if args.obsidian_note:
            intake_args.extend(["--obsidian-note", args.obsidian_note])
        if args.obsidian_course_dir:
            intake_args.extend(["--obsidian-course-dir", str(args.obsidian_course_dir)])
        for keyword in args.keyword:
            intake_args.extend(["--keyword", keyword])
        if args.json:
            intake_args.append("--json")
        return course_intake_main(intake_args)

    if args.command == "course-mirror-obsidian":
        from .course_intake import mirror_existing_course_to_obsidian, write_json

        course_root = (
            args.brain_root.expanduser().resolve()
            / "knowledge"
            / "courses"
            / args.course_id
        )
        if not course_root.is_dir():
            print(f"error: course root not found: {course_root}", file=sys.stderr)
            return 2
        summary = mirror_existing_course_to_obsidian(
            course_root,
            args.obsidian_course_dir.expanduser().resolve(),
        )
        result = {
            "course_id": args.course_id,
            "course_root": str(course_root),
            "obsidian_course_dir": str(args.obsidian_course_dir.expanduser().resolve()),
            **summary,
        }
        if args.out:
            out_dir = args.out.expanduser().resolve()
            write_json(out_dir / "obsidian-course-mirror.json", result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                "obsidian_mirror "
                f"course_id={args.course_id} "
                f"files={summary['obsidian_mirror_files']} "
                f"changed={summary['obsidian_mirror_changed']}"
            )
        return 0

    if args.command == "course-index":
        from .course_intake import index_existing_course, write_json, write_materials_indexes

        brain_root = args.brain_root.expanduser().resolve()
        course_root = brain_root / "knowledge" / "courses" / args.course_id
        if not course_root.is_dir():
            print(f"error: course root not found: {course_root}", file=sys.stderr)
            return 2
        plan = index_existing_course(
            course_root,
            brain_root=brain_root,
            course_id=args.course_id,
            course_title=args.course_title,
        )
        write_materials_indexes(plan)
        result = {
            "course_id": args.course_id,
            "course_root": str(course_root),
            "summary": plan.summary,
            "materials_index_csv": str(course_root / "materials_index.csv"),
            "materials_index_md": str(course_root / "materials_index.md"),
        }
        write_json(course_root / "_intake" / "course-index-report.json", result)
        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            print(
                "course_index "
                f"course_id={args.course_id} "
                f"files={plan.summary['files']} "
                f"csv={result['materials_index_csv']}"
            )
        return 0

    if args.command == "dialogue-audit-template":
        from .dialogue_audit import main as dialogue_audit_main

        return dialogue_audit_main(
            [
                "--course-id",
                args.course_id,
                "--course-title",
                args.course_title,
                "--source-root",
                str(args.source_root),
                "--brain-root",
                str(args.brain_root),
                "--entry",
                args.entry,
                "--executor",
                args.executor,
                "--out",
                str(args.out),
            ]
        )

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main([])

    if args.command == "doctor":
        scripts = {
            relative: (repo / relative).exists()
            for relative in (
                "scripts/audit-knowledge-materials.sh",
                "scripts/select-docpack-samples.py",
                "scripts/build-docpack.py",
                "scripts/validate-docpack.py",
                "schemas/docpack",
            )
        }
        tools = {tool: shutil.which(tool) for tool in ("pdfinfo", "pdftotext", "pdftoppm")}
        # LibreOffice is required only for Office (.doc/.docx/.ppt/.pptx) builds;
        # report it (optional) so a green poppler doctor isn't read as "Office OK".
        tools["soffice_or_libreoffice"] = shutil.which("soffice") or shutil.which("libreoffice")
        result = {
            "ok": all(scripts.values()),
            "method": "doctor",
            "repo_root": str(repo),
            "scripts": scripts,
            "tools": tools,
        }
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0 if result["ok"] else 1

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
