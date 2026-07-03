#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared helpers for the brain intake pipeline scripts."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any


DEFAULT_BRAIN_ROOT = Path.home() / "OrangePi-Store" / "sync" / "brain"
DEFAULT_VAULT_ROOT = Path.home() / "Desktop" / "brain-notes"
DEFAULT_RUN_DIR = Path("work/pipeline/run-01")
RUN_ID = "run-01"
GENERATED_BY = "Codex run-01 frontmatter-backfill 2026-06-11"
TODAY = "2026-06-11"
THERMAL_COURSE = "thermal-statistical-physics"
THERMAL_VAULT_REL = Path("课程") / "热力学与统计物理资料"
THERMAL_LINK_REL = Path("课程") / "热统资料"
THERMAL_ARCHIVE_REL = Path("归档") / "热统迁移-20260611"
LOCK_MARKERS = ("勿改", "LOCKED", "do-not-edit")


def utc_now() -> str:
    return dt.datetime.now(dt.timezone.utc).replace(microsecond=0).isoformat()


def run_id_from_dir(run_dir: Path) -> str:
    return Path(run_dir).name or RUN_ID


def generated_by(run_dir: Path, purpose: str) -> str:
    return f"Codex {run_id_from_dir(run_dir)} {purpose} {TODAY}"


def parser(description: str) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=description)
    p.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    p.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    p.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    return p


def add_plan_apply(sub: argparse.ArgumentParser) -> None:
    group = sub.add_mutually_exclusive_group()
    group.add_argument("--plan", action="store_true", help="write a plan and exit")
    group.add_argument("--apply", action="store_true", help="apply an approved plan")
    sub.add_argument("--approved-plan", type=Path, help="approved plan JSON path")


def repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def resolve_path(path: Path) -> Path:
    return path.expanduser().resolve()


def ensure_run_dir(run_dir: Path) -> None:
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "backups").mkdir(parents=True, exist_ok=True)


def ensure_inside(root: Path, path: Path) -> None:
    root = resolve_path(root)
    path = resolve_path(path)
    if path != root and root not in path.parents:
        raise ValueError(f"path escapes root: {path} not under {root}")


def rel_to(root: Path, path: Path) -> str:
    return resolve_path(path).relative_to(resolve_path(root)).as_posix()


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        path.write_text(text, encoding="utf-8")
    except PermissionError:
        # Windows refuses to truncate-open an existing hidden/read-only file
        # (e.g. a hidden .stignore), raising PermissionError. Clear those
        # attributes and retry so managed view files stay writable on every
        # client. POSIX keeps the original behaviour.
        if os.name != "nt" or not path.exists():
            raise
        subprocess.run(
            ["attrib", "-h", "-r", str(path)],
            check=False,
            capture_output=True,
            text=True,
        )
        path.write_text(text, encoding="utf-8")


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(read_text(path))


def backup_file(path: Path, backup_dir: Path, root: Path | None = None) -> Path | None:
    if not path.exists() and not path.is_symlink():
        return None
    if root:
        try:
            rel = Path(rel_to(root, path))
        except ValueError:
            rel = Path(path.name)
    else:
        rel = Path(path.name)
    dest = backup_dir / rel
    dest.parent.mkdir(parents=True, exist_ok=True)
    if path.is_symlink():
        target = os.readlink(path)
        write_text(dest.with_suffix(dest.suffix + ".symlink"), target + "\n")
    elif path.is_file():
        shutil.copy2(path, dest)
    return dest


def copy_tree_listing(root: Path, out: Path) -> None:
    rows: list[str] = []
    if root.exists():
        for p in sorted(root.rglob("*")):
            kind = "link" if p.is_symlink() else "dir" if p.is_dir() else "file"
            suffix = ""
            if p.is_symlink():
                suffix = f" -> {os.readlink(p)}"
            rows.append(f"{kind}\t{rel_to(root, p)}{suffix}")
    write_text(out, "\n".join(rows) + ("\n" if rows else ""))


def create_preflight(
    brain_root: Path,
    vault_root: Path,
    run_dir: Path,
    repo: Path | None = None,
) -> dict[str, Any]:
    repo = repo or repo_root()
    ensure_run_dir(run_dir)
    lock = run_dir / ".run-lock"
    if lock.exists():
        lock_payload = read_json(lock)
    else:
        lock_payload = {"run_id": run_id_from_dir(run_dir), "created_at": utc_now(), "pid": os.getpid()}
        write_json(lock, lock_payload)

    backup_dir = run_dir / "backups" / "preflight"
    backup_dir.mkdir(parents=True, exist_ok=True)
    backup_file(brain_root / "_indexes" / "pdf-manifest.jsonl", backup_dir, brain_root)
    backup_file(vault_root / ".stignore", backup_dir, vault_root)
    backup_file(brain_root / "knowledge" / "courses" / THERMAL_COURSE / "README.md", backup_dir, brain_root)
    backup_file(brain_root / "knowledge" / "interests" / "README.md", backup_dir, brain_root)
    copy_tree_listing(vault_root / THERMAL_VAULT_REL, backup_dir / "thermal-vault-inventory.tsv")

    status = subprocess.run(
        ["git", "status", "--short"],
        cwd=repo,
        check=False,
        capture_output=True,
        text=True,
    )
    write_text(run_dir / "preflight-git-status.txt", status.stdout)
    forbidden = brain_root / "_meta" / "禁改清单.md"
    forbidden_text = read_text(forbidden) if forbidden.exists() else ""
    write_text(run_dir / "preflight-forbidden-list.txt", forbidden_text)
    payload = {
        "ok": brain_root.exists() and vault_root.exists(),
        "run_id": run_id_from_dir(run_dir),
        "created_at": utc_now(),
        "brain_root": str(resolve_path(brain_root)),
        "vault_root": str(resolve_path(vault_root)),
        "run_dir": str(resolve_path(run_dir)),
        "lock": lock_payload,
        "git_status_lines": len([ln for ln in status.stdout.splitlines() if ln.strip()]),
        "forbidden_list_exists": forbidden.exists(),
    }
    write_json(run_dir / "preflight-log.json", payload)
    return payload


def should_skip_path(path: Path) -> bool:
    parts = set(path.parts)
    return bool(parts & {"personal-data", "_archive", ".git", ".obsidian", ".stfolder"})


def has_lock_marker(path: Path) -> bool:
    if not path.is_file() or path.stat().st_size > 1024 * 1024:
        return False
    text = read_text(path)[:2000]
    return any(marker in text for marker in LOCK_MARKERS)


def iter_files(root: Path, suffixes: tuple[str, ...]) -> list[Path]:
    if not root.exists():
        return []
    suffixes = tuple(s.lower() for s in suffixes)
    return [
        p
        for p in sorted(root.rglob("*"))
        if p.is_file() and p.suffix.lower() in suffixes and not should_skip_path(p)
    ]


def read_manifest(brain_root: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    path = brain_root / "_indexes" / "pdf-manifest.jsonl"
    entries: list[dict[str, Any]] = []
    invalid: list[dict[str, Any]] = []
    if not path.exists():
        return entries, invalid
    for lineno, line in enumerate(read_text(path).splitlines(), start=1):
        if not line.strip():
            continue
        try:
            item = json.loads(line)
            item["_line"] = lineno
            entries.append(item)
        except json.JSONDecodeError as exc:
            invalid.append({"line": lineno, "error": str(exc), "text": line[:200]})
    return entries, invalid


def write_manifest(brain_root: Path, entries: list[dict[str, Any]]) -> None:
    path = brain_root / "_indexes" / "pdf-manifest.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    cleaned = []
    for entry in entries:
        item = dict(entry)
        item.pop("_line", None)
        cleaned.append(item)
    cleaned.sort(key=lambda item: item.get("brain_path", ""))
    path.write_text(
        "".join(json.dumps(item, ensure_ascii=False, sort_keys=True) + "\n" for item in cleaned),
        encoding="utf-8",
    )


def manifest_maps(entries: list[dict[str, Any]]) -> tuple[dict[str, dict[str, Any]], dict[str, list[dict[str, Any]]]]:
    by_path = {str(e.get("brain_path")): e for e in entries if e.get("brain_path")}
    by_sha: dict[str, list[dict[str, Any]]] = {}
    for e in entries:
        sha = e.get("sha256")
        if sha:
            by_sha.setdefault(str(sha), []).append(e)
    return by_path, by_sha


def classify_pdf(brain_root: Path, path: Path) -> tuple[str, dict[str, str]]:
    rel = Path(rel_to(brain_root, path))
    parts = rel.parts
    meta: dict[str, str] = {}
    if len(parts) >= 3 and parts[0] == "knowledge" and parts[1] == "courses":
        meta["kind"] = "course-pdf"
        meta["course"] = parts[2]
        section = parts[3] if len(parts) >= 4 else ""
        if section in {"exams", "exam"}:
            meta["material_type"] = "exam"
            return "exam", meta
        if section in {"lectures", "slides"}:
            meta["material_type"] = "lecture"
            return "lecture-text", meta
        if section in {"solutions", "homework", "exercises"}:
            meta["material_type"] = "exercise"
            return "exam", meta
        meta["material_type"] = section or "course-material"
        return "lecture-text", meta
    if len(parts) >= 3 and parts[0] == "knowledge" and parts[1] == "research":
        meta["kind"] = "paper"
        meta["research_topic"] = parts[2]
        return "paper", meta
    meta["kind"] = "source"
    return "skip", meta


def obsidian_note_for(meta: dict[str, str]) -> str | None:
    course = meta.get("course")
    if course == "thermal-statistical-physics":
        return "课程/热力学与统计物理.md"
    if course == "solid-state-physics":
        return "课程/固体物理.md"
    if course == "advanced-photonics":
        return "课程/先进光子物理.md"
    return None


def companion_md_for_pdf(brain_root: Path, pdf: Path) -> str | None:
    rel = Path(rel_to(brain_root, pdf))
    if "knowledge" not in rel.parts:
        return None
    if len(rel.parts) >= 5 and rel.parts[0] == "knowledge" and rel.parts[1] == "courses":
        course_root = brain_root / "knowledge" / "courses" / rel.parts[2]
        section = rel.parts[3]
        md = course_root / "md" / section / (pdf.stem + ".md")
        if md.exists():
            return rel_to(brain_root, md)
        same = pdf.with_suffix(".md")
        if same.exists():
            return rel_to(brain_root, same)
    return None


def manifest_entry(brain_root: Path, pdf: Path, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    existing = dict(existing or {})
    rel = rel_to(brain_root, pdf)
    cls, meta = classify_pdf(brain_root, pdf)
    item = {
        "schema_version": existing.get("schema_version", "pdf-manifest-v1"),
        "brain_path": rel,
        "sha256": sha256_file(pdf),
        "title": existing.get("title") or pdf.stem,
        "kind": existing.get("kind") or meta.get("kind"),
        "canonical": existing.get("canonical", True),
        "attachment_mode": existing.get("attachment_mode") or "brain-only",
        "mobile_cache": existing.get("mobile_cache", False),
        "zotero_item_key": existing.get("zotero_item_key"),
        "zotero_linked_attachment_key": existing.get("zotero_linked_attachment_key"),
        "citekey": existing.get("citekey"),
        "obsidian_note": existing.get("obsidian_note") or obsidian_note_for(meta),
        "updated_at": utc_now(),
    }
    if "created_at" in existing:
        item["created_at"] = existing["created_at"]
    else:
        item["created_at"] = utc_now()
    if meta.get("course"):
        item["course"] = meta["course"]
    if meta.get("material_type"):
        item["material_type"] = meta["material_type"]
    md = existing.get("md_path") or companion_md_for_pdf(brain_root, pdf)
    if md:
        item["md_path"] = md
    return item


def normalize_filename(name: str) -> str:
    stem, suffix = os.path.splitext(name)
    stem = stem.replace("（", "(").replace("）", ")")
    stem = re.sub(r"\s+", " ", stem).strip()
    stem = re.sub(r"\s+\(1\)$", "", stem)
    return stem + suffix


def parse_frontmatter(text: str) -> tuple[dict[str, str], str, bool]:
    if not text.startswith("---\n"):
        return {}, text, False
    end = text.find("\n---\n", 4)
    if end == -1:
        return {}, text, False
    raw = text[4:end]
    body = text[end + 5 :]
    data: dict[str, str] = {}
    for line in raw.splitlines():
        if not line.strip() or line.lstrip().startswith("#") or ":" not in line:
            continue
        key, value = line.split(":", 1)
        data[key.strip()] = value.strip().strip('"')
    return data, body, True


def format_frontmatter(data: dict[str, Any]) -> str:
    preferred = [
        "type",
        "title",
        "source",
        "sha256",
        "course",
        "term",
        "citekey",
        "status",
        "created",
        "generated_by",
        "tags",
        "needs_review",
    ]
    lines = ["---"]
    keys = preferred + sorted(k for k in data if k not in preferred)
    for key in keys:
        if key not in data:
            continue
        value = data[key]
        if isinstance(value, list):
            rendered = "[" + ", ".join(str(v) for v in value) + "]"
        elif value is None:
            rendered = ""
        else:
            rendered = str(value)
            if any(ch in rendered for ch in [":", "#", "[", "]", "{", "}", '"']):
                rendered = json.dumps(rendered, ensure_ascii=False)
        lines.append(f"{key}: {rendered}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def title_from_markdown(path: Path, body: str) -> str:
    for line in body.splitlines()[:40]:
        if line.startswith("# "):
            return line[2:].strip()
    return path.stem


def source_for_markdown(brain_root: Path, md: Path) -> tuple[str, str, str | None]:
    rel = Path(rel_to(brain_root, md))
    source = ""
    sha = ""
    course = rel.parts[2] if len(rel.parts) >= 3 and rel.parts[:2] == ("knowledge", "courses") else None
    if len(rel.parts) >= 6 and rel.parts[0] == "knowledge" and rel.parts[1] == "courses" and rel.parts[3] == "md":
        section = rel.parts[4]
        pdf = brain_root / "knowledge" / "courses" / rel.parts[2] / section / (md.stem + ".pdf")
        if pdf.exists():
            source = rel_to(brain_root, pdf)
            sha = sha256_file(pdf)
    else:
        pdf = md.with_suffix(".pdf")
        if pdf.exists():
            source = rel_to(brain_root, pdf)
            sha = sha256_file(pdf)
    return source, sha, course


def frontmatter_defaults(brain_root: Path, md: Path, body: str) -> dict[str, Any]:
    rel = Path(rel_to(brain_root, md))
    source, sha, course = source_for_markdown(brain_root, md)
    if md.name.lower() == "readme.md":
        typ = "note"
        status = "filed"
    elif source:
        typ = "course-pdf" if course else "paper"
        status = "converted"
    elif "research" in rel.parts:
        typ = "paper"
        status = "filed"
    else:
        typ = "note"
        status = "filed"
    created = dt.datetime.fromtimestamp(md.stat().st_mtime).date().isoformat()
    defaults: dict[str, Any] = {
        "type": typ,
        "title": title_from_markdown(md, body),
        "source": source,
        "sha256": sha,
        "course": course or "",
        "term": "",
        "citekey": "",
        "status": status,
        "created": created,
        "generated_by": GENERATED_BY,
        "tags": [],
    }
    if not source and typ != "note":
        defaults["needs_review"] = "source-not-found"
    return defaults


def thermal_target(vault_root: Path, brain_root: Path, path: Path) -> Path | None:
    try:
        rel = Path(rel_to(vault_root / THERMAL_VAULT_REL, path))
    except ValueError:
        return None
    course_root = brain_root / "knowledge" / "courses" / THERMAL_COURSE
    parts = rel.parts
    if not parts:
        return None
    if parts[0] == "PDF" and len(parts) >= 3:
        folder = parts[1]
        filename = parts[-1]
        section = {
            "试卷": "exams",
            "讲义": "lectures",
            "习题解答": "solutions",
        }.get(folder, "misc")
        return course_root / section / filename
    if parts[0] == "试卷" and path.suffix.lower() == ".md":
        return course_root / "md" / "exams" / path.name
    if parts[0] == "讲义" and path.suffix.lower() == ".md":
        return course_root / "md" / "lectures" / path.name
    if parts[0] == "习题解答" and path.suffix.lower() == ".md":
        return course_root / "md" / "solutions" / path.name
    return None


def markdown_report(path: Path, title: str, sections: list[tuple[str, list[str]]]) -> None:
    lines = [f"# {title}", "", f"- run_id: {RUN_ID}", f"- generated_at: {utc_now()}", ""]
    for heading, items in sections:
        lines.append(f"## {heading}")
        if items:
            lines.extend(f"- {item}" for item in items)
        else:
            lines.append("- 无")
        lines.append("")
    write_text(path, "\n".join(lines))


def require_approved_plan(args: argparse.Namespace, expected_name: str | None = None) -> Path:
    if not args.apply:
        raise SystemExit("--apply is required")
    if not args.approved_plan:
        raise SystemExit("--approved-plan is required for apply")
    plan = args.approved_plan
    if expected_name and plan.name != expected_name:
        raise SystemExit(f"approved plan must be {expected_name}: {plan}")
    if not plan.exists():
        raise SystemExit(f"approved plan not found: {plan}")
    return plan
