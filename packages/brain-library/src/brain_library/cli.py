# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only brain library index diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence

from .indexer import (
    build_index,
    index_status,
    query_courses,
    query_index,
    recent_documents,
)

EXCLUDED_TOP_DIRS = {"personal-data"}
SKIP_DIRS_LITE = {".git", ".obsidian", ".trash", "node_modules", "__pycache__"}
INDEX_TEXT_SUFFIXES = {"md", "markdown", "txt", "bib", "csl"}


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MAX_FILES = 50000
DEFAULT_SAMPLE_LIMIT = 20
MAX_TEXT_BYTES = 2_000_000

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".obsidian",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
SOURCE_SUFFIXES = {
    "bib",
    "csv",
    "doc",
    "docx",
    "epub",
    "gif",
    "html",
    "jpeg",
    "jpg",
    "json",
    "md",
    "pdf",
    "png",
    "ppt",
    "pptx",
    "tif",
    "tiff",
    "tsv",
    "webp",
    "xls",
    "xlsx",
}
SQLITE_SUFFIXES = {"db", "sqlite", "sqlite3"}

WIKILINK_RE = re.compile(r"(?<!!)\[\[[^\]\n]+\]\]")
EMBED_RE = re.compile(r"!\[\[[^\]\n]+\]\]|!\[[^\]\n]*\]\([^)]+\)")
TAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_/-]+")
CITEKEY_RE = re.compile(r"(?<![\w@])@[A-Za-z0-9][A-Za-z0-9_:.:-]*")
ZOTERO_RE = re.compile(r"zotero://", re.IGNORECASE)
PDF_MANIFEST_RELATIVE = Path("_indexes") / "pdf-manifest.jsonl"
PDF_MANIFEST_SAMPLE_FIELDS = (
    "schema_version",
    "sha256",
    "canonical_sha256",
    "canonical",
    "brain_path",
    "canonical_brain_path",
    "attachment_mode",
    "mobile_cache",
    "zotero_item_key",
    "zotero_linked_attachment_key",
    "zotero_stored_attachment_key",
    "legacy_zotero_stored_attachment_key",
    "citekey",
    "obsidian_note",
    "kind",
    "title",
)


JsonObject = dict[str, Any]


def _force_utf8_streams() -> None:
    """Force UTF-8 text output so non-ASCII (Chinese) JSON is readable on Windows.

    On Windows the default stdout/stderr encoding is the locale code page (e.g.
    cp936/GBK); with ``ensure_ascii=False`` that mangles Chinese into mojibake.
    Reconfiguring to UTF-8 is a no-op on Mac/Linux (already UTF-8). Not applied to
    the MCP stdio transport, which manages its own framing.
    """
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


def _json_print(data: JsonObject) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _candidate_repo_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_ASSISTANT_ROOT")
    if env_root:
        roots.append(Path(env_root))
    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    roots.extend([PACKAGE_ROOT, *PACKAGE_ROOT.parents])
    return roots


def find_repo_root() -> Path:
    for root in _candidate_repo_roots():
        if (
            (root / "docs" / "tooling-packaging.md").is_file()
            and (root / "packages" / "brain-library").is_dir()
            and (root / "skills" / "brain-library").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def candidate_brain_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("BRAIN_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend(
        [
            Path("/mnt/brain"),
            Path.home() / "brain",
            Path.home() / "OrangePi-Store" / "sync" / "brain",
        ]
    )
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.resolve() if root.exists() else root.expanduser()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def default_brain_root() -> Path | None:
    for root in candidate_brain_roots():
        if root.is_dir():
            return root.resolve()
    return None


def resolve_brain_root(raw: Path | None) -> Path | None:
    if raw is not None:
        return raw.expanduser().resolve()
    return default_brain_root()


def _relative(path: Path, root: Path) -> str:
    try:
        # POSIX separators so scan output is identical across Mac/orangepi/Windows.
        return path.relative_to(root).as_posix()
    except ValueError:
        return str(path)


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _sample_manifest_entry(entry: JsonObject) -> JsonObject:
    return {
        field: entry.get(field)
        for field in PDF_MANIFEST_SAMPLE_FIELDS
        if field in entry
    }


def summarize_pdf_manifest(
    root: Path,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    path = root / PDF_MANIFEST_RELATIVE
    summary: JsonObject = {
        "exists": path.is_file(),
        "path": str(PDF_MANIFEST_RELATIVE),
        "line_count": 0,
        "valid_entries": 0,
        "invalid_json_lines": 0,
        "canonical_count": 0,
        "cache_count": 0,
        "mobile_cache_count": 0,
        "by_attachment_mode": {},
        "zotero_item_key_count": 0,
        "obsidian_note_count": 0,
        "missing_brain_path_count": 0,
        "missing_sha256_count": 0,
        "missing_brain_files": [],
        "duplicate_canonical_sha256": [],
        "samples": [],
        "read_error": "",
    }
    if not path.is_file():
        return summary

    attachment_modes: Counter[str] = Counter()
    canonical_sha256: Counter[str] = Counter()
    missing_brain_files: list[str] = []
    samples: list[JsonObject] = []

    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        summary["read_error"] = str(exc)
        return summary

    summary["line_count"] = len(lines)
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            summary["invalid_json_lines"] += 1
            continue
        if not isinstance(loaded, dict):
            summary["invalid_json_lines"] += 1
            continue

        entry: JsonObject = loaded
        summary["valid_entries"] += 1
        mode = str(entry.get("attachment_mode") or "unknown")
        attachment_modes[mode] += 1
        if entry.get("mobile_cache") is True:
            summary["mobile_cache_count"] += 1
        if entry.get("zotero_item_key"):
            summary["zotero_item_key_count"] += 1
        if entry.get("obsidian_note"):
            summary["obsidian_note_count"] += 1

        is_canonical = entry.get("canonical") is not False
        if mode == "stored-mobile-cache" or is_canonical is False:
            summary["cache_count"] += 1
        else:
            summary["canonical_count"] += 1
            sha256 = entry.get("sha256")
            if isinstance(sha256, str) and sha256:
                canonical_sha256[sha256] += 1
            else:
                summary["missing_sha256_count"] += 1
            brain_path = entry.get("brain_path")
            if isinstance(brain_path, str) and brain_path:
                if not (root / brain_path).is_file() and len(missing_brain_files) < sample_limit:
                    missing_brain_files.append(brain_path)
            else:
                summary["missing_brain_path_count"] += 1

        if len(samples) < sample_limit:
            sample = _sample_manifest_entry(entry)
            sample["line"] = line_no
            samples.append(sample)

    duplicates = sorted(key for key, count in canonical_sha256.items() if count > 1)
    summary["by_attachment_mode"] = dict(sorted(attachment_modes.items()))
    summary["duplicate_canonical_sha256"] = duplicates[:sample_limit]
    summary["missing_brain_files"] = missing_brain_files
    summary["samples"] = samples
    return summary


def _walk_files(root: Path, *, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    truncated = False
    for current, dirs, names in os.walk(root):
        current_path = Path(current)
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIP_DIRS and not name.endswith(".docpack")
        ]
        for name in names:
            path = current_path / name
            files.append(path)
            if len(files) >= max_files:
                return files, True
    return files, truncated


def _walk_docpack_dirs(root: Path) -> Iterable[Path]:
    for current, dirs, _names in os.walk(root):
        current_path = Path(current)
        docpack_dirs = [name for name in dirs if name.endswith(".docpack")]
        for name in sorted(docpack_dirs):
            yield current_path / name
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIP_DIRS and not name.endswith(".docpack")
        ]


def _read_text_sample(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _count_text_signals(markdown_files: Iterable[Path]) -> JsonObject:
    wikilinks = 0
    embeds = 0
    tags = 0
    citekeys = 0
    zotero_links = 0
    skipped_large = 0
    for path in markdown_files:
        try:
            if path.stat().st_size > MAX_TEXT_BYTES:
                skipped_large += 1
                continue
        except OSError:
            continue
        text = _read_text_sample(path)
        wikilinks += len(WIKILINK_RE.findall(text))
        embeds += len(EMBED_RE.findall(text))
        tags += len(TAG_RE.findall(text))
        citekeys += len(CITEKEY_RE.findall(text))
        zotero_links += len(ZOTERO_RE.findall(text))
    return {
        "wikilinks": wikilinks,
        "embeds": embeds,
        "tags": tags,
        "citekey_occurrences": citekeys,
        "zotero_links": zotero_links,
        "large_markdown_skipped": skipped_large,
    }


def _citation_anchor_count(path: Path) -> int:
    try:
        loaded = _load_json(path)
    except (OSError, json.JSONDecodeError):
        return 0
    if not isinstance(loaded, dict):
        return 0
    anchors = loaded.get("anchors")
    return len(anchors) if isinstance(anchors, list) else 0


def inspect_docpack(docpack: Path, *, root: Path) -> JsonObject:
    manifest_path = docpack / "manifest.json"
    verify_path = docpack / "verify.json"
    citations_path = docpack / "citations.json"
    item: JsonObject = {
        "path": _relative(docpack, root),
        "manifest_exists": manifest_path.is_file(),
        "verify_exists": verify_path.is_file(),
        "citations_exists": citations_path.is_file(),
        "status": "missing",
        "docpack_id": None,
        "page_count": None,
        "citation_anchor_count": 0,
        "risks": [],
    }

    if item["manifest_exists"]:
        try:
            manifest = _load_json(manifest_path)
            if isinstance(manifest, dict):
                item["docpack_id"] = manifest.get("docpack_id")
                display = manifest.get("display", {})
                if isinstance(display, dict):
                    item["page_count"] = display.get("page_count")
                if isinstance(manifest.get("risks"), list):
                    item["risks"].extend(str(risk) for risk in manifest["risks"])
        except (OSError, json.JSONDecodeError) as exc:
            item["risks"].append(f"manifest_read_failed: {exc}")

    if item["verify_exists"]:
        try:
            verify = _load_json(verify_path)
            if isinstance(verify, dict):
                item["status"] = str(verify.get("status", "unknown"))
                if item["page_count"] is None and isinstance(verify.get("pages"), list):
                    item["page_count"] = len(verify["pages"])
                if isinstance(verify.get("risks"), list):
                    item["risks"].extend(str(risk) for risk in verify["risks"])
        except (OSError, json.JSONDecodeError) as exc:
            item["risks"].append(f"verify_read_failed: {exc}")

    if item["citations_exists"]:
        item["citation_anchor_count"] = _citation_anchor_count(citations_path)

    missing = [
        name
        for name, exists in (
            ("manifest", item["manifest_exists"]),
            ("verify", item["verify_exists"]),
            ("citations", item["citations_exists"]),
        )
        if not exists
    ]
    if missing:
        item["risks"].extend(f"missing_{name}" for name in missing)
    return item


def summarize_docpacks(root: Path, *, sample_limit: int = DEFAULT_SAMPLE_LIMIT) -> JsonObject:
    samples: list[JsonObject] = []
    status_counts: Counter[str] = Counter()
    missing_manifest = 0
    missing_verify = 0
    missing_citations = 0
    citation_anchor_count = 0
    count = 0

    for docpack in _walk_docpack_dirs(root):
        count += 1
        item = inspect_docpack(docpack, root=root)
        status_counts[str(item["status"])] += 1
        missing_manifest += 0 if item["manifest_exists"] else 1
        missing_verify += 0 if item["verify_exists"] else 1
        missing_citations += 0 if item["citations_exists"] else 1
        citation_anchor_count += int(item["citation_anchor_count"] or 0)
        if len(samples) < sample_limit:
            samples.append(item)

    return {
        "count": count,
        "status_counts": dict(sorted(status_counts.items())),
        "missing_manifest": missing_manifest,
        "missing_verify": missing_verify,
        "missing_citations": missing_citations,
        "citation_anchor_count": citation_anchor_count,
        "samples": samples,
    }


def doctor(root: Path | None = None, *, repo: Path | None = None) -> JsonObject:
    resolved = resolve_brain_root(root)
    repo_root: Path | None = repo
    repo_error = ""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)

    root_exists = bool(resolved and resolved.is_dir())
    checks: JsonObject = {
        "brain_root": "ok" if root_exists else "missing",
        "claude_md": "missing",
        "agents_md": "missing",
        "knowledge_dir": "missing",
        "obsidian_config": "missing",
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
    }
    if resolved:
        checks["claude_md"] = "ok" if (resolved / "CLAUDE.md").is_file() else "missing"
        checks["agents_md"] = "ok" if (resolved / "AGENTS.md").is_file() else "missing"
        checks["knowledge_dir"] = "ok" if (resolved / "knowledge").is_dir() else "missing"
        checks["obsidian_config"] = "ok" if (resolved / ".obsidian").is_dir() else "missing"
    if repo_root:
        checks["repo_package"] = (
            "ok"
            if (repo_root / "packages" / "brain-library" / "src" / "brain_library" / "cli.py").is_file()
            else "missing"
        )
        checks["repo_skill"] = "ok" if (repo_root / "skills" / "brain-library").is_dir() else "missing"
        checks["repo_plugin"] = "ok" if (repo_root / "plugins" / "brain-library").is_dir() else "missing"

    risks = [name for name, status in checks.items() if status != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": root_exists and checks["repo_package"] == "ok" and checks["repo_skill"] == "ok",
        "root": str(resolved) if resolved else None,
        "repo_root": str(repo_root) if repo_root else None,
        "candidate_roots": [str(path) for path in candidate_brain_roots()],
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
    }


def scan_library(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    if max_files < 1:
        return {"ok": False, "root": str(root), "errors": ["max_files must be >= 1"]}
    if sample_limit < 0:
        return {"ok": False, "root": str(root), "errors": ["sample_limit must be >= 0"]}
    if not root.is_dir():
        return {"ok": False, "root": str(root), "errors": ["root is not a directory"]}

    files, truncated = _walk_files(root, max_files=max_files)
    by_suffix: Counter[str] = Counter()
    source_materials: Counter[str] = Counter()
    sqlite_files: list[str] = []
    manifest_candidates: list[str] = []
    bm25_candidates: list[str] = []
    bib_files: list[Path] = []
    csl_files: list[str] = []
    better_bibtex_candidates: list[str] = []
    markdown_files: list[Path] = []

    for path in files:
        suffix = path.suffix.lower().lstrip(".") or "[no_suffix]"
        by_suffix[suffix] += 1
        if suffix in SOURCE_SUFFIXES:
            source_materials[suffix] += 1
        if suffix == "md":
            markdown_files.append(path)
        if suffix == "bib":
            bib_files.append(path)
        if suffix == "csl":
            csl_files.append(_relative(path, root))
        if suffix in SQLITE_SUFFIXES:
            sqlite_files.append(_relative(path, root))
        name_lower = path.name.lower()
        if name_lower in {"manifest.json", "library-manifest.json", "index.json"}:
            manifest_candidates.append(_relative(path, root))
        if "bm25" in name_lower:
            bm25_candidates.append(_relative(path, root))
        if "better-bibtex" in name_lower or "betterbibtex" in name_lower:
            better_bibtex_candidates.append(_relative(path, root))

    text_signals = _count_text_signals(markdown_files)
    docpacks = summarize_docpacks(root, sample_limit=sample_limit)
    pdf_manifest = summarize_pdf_manifest(root, sample_limit=sample_limit)
    risks: list[str] = []
    if truncated:
        risks.append("scan_truncated")
    if not (root / "CLAUDE.md").is_file() and not (root / "AGENTS.md").is_file():
        risks.append("local_guidance_missing")
    if not (root / "knowledge").is_dir():
        risks.append("knowledge_dir_missing")
    if not (root / ".obsidian").is_dir():
        risks.append("obsidian_config_not_found")
    if docpacks["count"] and docpacks["citation_anchor_count"] == 0:
        risks.append("docpack_citations_missing")
    if docpacks["missing_manifest"]:
        risks.append("docpack_manifest_missing")
    if docpacks["missing_verify"]:
        risks.append("docpack_verify_missing")
    if not sqlite_files:
        risks.append("sqlite_index_not_found")
    if pdf_manifest["read_error"]:
        risks.append("pdf_manifest_read_error")
    if pdf_manifest["invalid_json_lines"]:
        risks.append("pdf_manifest_invalid_json")
    if pdf_manifest["duplicate_canonical_sha256"]:
        risks.append("pdf_manifest_duplicate_canonical_sha256")
    if pdf_manifest["missing_brain_files"]:
        risks.append("pdf_manifest_missing_brain_files")

    return {
        "ok": True,
        "root": str(root),
        "files_scanned": len(files),
        "truncated": truncated,
        "max_files": max_files,
        "guidance": {
            "claude_md": (root / "CLAUDE.md").is_file(),
            "agents_md": (root / "AGENTS.md").is_file(),
            "index_md": (root / "index.md").is_file(),
            "readme_md": (root / "README.md").is_file(),
        },
        "files": {
            "by_suffix": dict(sorted(by_suffix.items())),
            "source_materials": dict(sorted(source_materials.items())),
            "sqlite_files": sqlite_files[:sample_limit],
            "manifest_candidates": manifest_candidates[:sample_limit],
            "bm25_candidates": bm25_candidates[:sample_limit],
        },
        "obsidian": {
            "vault_config_exists": (root / ".obsidian").is_dir(),
            "markdown_files": len(markdown_files),
            "wikilinks": text_signals["wikilinks"],
            "embeds": text_signals["embeds"],
            "tags": text_signals["tags"],
            "large_markdown_skipped": text_signals["large_markdown_skipped"],
        },
        "zotero": {
            "bib_files": len(bib_files),
            "bib_samples": [_relative(path, root) for path in bib_files[:sample_limit]],
            "csl_files": csl_files[:sample_limit],
            "citekey_occurrences": text_signals["citekey_occurrences"],
            "zotero_links": text_signals["zotero_links"],
            "better_bibtex_candidates": better_bibtex_candidates[:sample_limit],
        },
        "docpacks": docpacks,
        "pdf_manifest": pdf_manifest,
        "risks": risks,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain-library",
        description="Read-only diagnostics for brain library indexing and display readiness.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="rtime-assistant repository root; defaults to auto-detect or RTIME_ASSISTANT_ROOT",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="Check package and local brain roots.")
    doctor_parser.add_argument("root", nargs="?", type=Path, help="brain root")
    doctor_parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    scan = subparsers.add_parser("scan", help="Scan a brain root without modifying it.")
    scan.add_argument("root", nargs="?", type=Path, help="brain root")
    scan.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    scan.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    scan.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    docpacks = subparsers.add_parser("docpacks", help="Summarize DocPack directories.")
    docpacks.add_argument("root", nargs="?", type=Path, help="brain root")
    docpacks.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    docpacks.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    index = subparsers.add_parser("index", help="Build or query derived SQLite/BM25 indexes.")
    index_sub = index.add_subparsers(dest="index_command", required=True)
    index_build = index_sub.add_parser("build", help="Build a derived SQLite FTS/BM25 index.")
    index_build.add_argument("root", type=Path, help="brain root")
    index_build.add_argument("--out", type=Path, required=True, help="output SQLite index path")
    index_build.add_argument("--force", action="store_true", help="replace an existing output index")
    index_build.add_argument(
        "--incremental", action="store_true",
        help="复用既有同模型索引中未变文档的向量(按 path+size+mtime 判定)，只重嵌入新增/改动，"
        "大幅提速重建。允许在已有索引上原地刷新。",
    )
    index_build.add_argument(
        "--allow-root-output",
        action="store_true",
        help="allow writing the output index under the brain root",
    )
    index_build.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    index_build.add_argument("--max-bytes", type=int, default=MAX_TEXT_BYTES)
    embed_group = index_build.add_mutually_exclusive_group()
    embed_group.add_argument(
        "--embed", dest="embed", action="store_true", default=None,
        help="force the vector layer on (skips with a note if no model/deps); schema 4",
    )
    embed_group.add_argument(
        "--no-embed", dest="embed", action="store_false",
        help="BM25-only build (schema 3); skip embedding even if a model is available",
    )
    index_build.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    index_status_parser = index_sub.add_parser("status", help="Read derived index metadata.")
    index_status_parser.add_argument("index", type=Path, help="SQLite index path")
    index_status_parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    index_query = index_sub.add_parser("query", help="Query a derived SQLite FTS/BM25 index.")
    index_query.add_argument("index", type=Path, help="SQLite index path")
    index_query.add_argument("query", help="search query")
    index_query.add_argument("--limit", type=int, default=10)
    index_query.add_argument("--suffix", help="restrict to a file suffix (e.g. md, pdf, bib)")
    index_query.add_argument("--path-prefix", help="restrict to paths starting with this prefix")
    index_query.add_argument("--title-only", action="store_true", help="match titles only")
    index_query.add_argument("--doc-type", help="filter by frontmatter type (ustc-program, ustc-notice, ...)")
    index_query.add_argument("--dept", help="filter by dept/institution")
    index_query.add_argument("--category", help="filter by category")
    index_query.add_argument("--date-from", help="min publish_date, inclusive (YYYY-MM-DD)")
    index_query.add_argument("--date-to", help="max publish_date, inclusive (YYYY-MM-DD)")
    index_query.add_argument(
        "--order-by", choices=["relevance", "date"], default="relevance", help="result ordering"
    )
    index_query.add_argument(
        "--mode", choices=["bm25", "vector", "hybrid"], default=None,
        help="retrieval mode; default hybrid if the index has vectors, else bm25",
    )
    index_query.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    index_courses = index_sub.add_parser("courses", help="Structured query over 培养方案 course rows.")
    index_courses.add_argument("index", type=Path, help="SQLite index path")
    index_courses.add_argument("--code", help="exact course code, e.g. PHYS1001B")
    index_courses.add_argument("--name-like", help="course name substring")
    index_courses.add_argument("--program-path", help="exact program note path")
    index_courses.add_argument("--dept", help="filter by department")
    index_courses.add_argument("--grade", help="filter by grade/year")
    index_courses.add_argument("--min-credits", type=float, help="minimum credits")
    index_courses.add_argument("--required-only", action="store_true", help="required (必修) courses only")
    index_courses.add_argument("--limit", type=int, default=200)
    index_courses.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    index_recent = index_sub.add_parser("recent", help="Most-recently-modified indexed documents.")
    index_recent.add_argument("index", type=Path, help="SQLite index path")
    index_recent.add_argument("--limit", type=int, default=20)
    index_recent.add_argument("--suffix")
    index_recent.add_argument("--path-prefix")
    index_recent.add_argument("--json", action="store_true")

    index_fresh = index_sub.add_parser("freshness", help="fresh|stale verdict vs newest knowledge/ file.")
    index_fresh.add_argument("index", type=Path, help="SQLite index path")
    index_fresh.add_argument("--brain-root", type=Path, help="brain root; defaults to auto-detect")
    index_fresh.add_argument("--json", action="store_true")

    meta = subparsers.add_parser(
        "meta", help="Read the brain _meta rule corpus (authoritative organize/spec rules)."
    )
    meta.add_argument("root", nargs="?", type=Path, help="brain root")
    meta.add_argument("--name", help="rule file to read in full (e.g. organize-rules); omit to list all")
    meta.add_argument("--query", help="keyword-search across all _meta rules instead of listing")
    meta.add_argument("--max-bytes", type=int, default=200_000)
    meta.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    read_p = subparsers.add_parser("read", help="Read one brain file's text (window-able by lines).")
    read_p.add_argument("root", nargs="?", type=Path, help="brain root")
    read_p.add_argument("--path", required=True, help="brain-relative file path")
    read_p.add_argument("--offset", type=int, default=0, help="start line (1-based; 0 = from start)")
    read_p.add_argument("--limit", type=int, default=0, help="max lines (0 = all)")
    read_p.add_argument("--max-bytes", type=int, default=MAX_TEXT_BYTES)
    read_p.add_argument("--json", action="store_true")

    tree_p = subparsers.add_parser("tree", help="List the immediate children of a brain directory.")
    tree_p.add_argument("root", nargs="?", type=Path, help="brain root")
    tree_p.add_argument("--path", default="", help="brain-relative dir (default: brain root)")
    tree_p.add_argument("--json", action="store_true")

    stat_p = subparsers.add_parser("stat", help="Metadata for one brain path (no body).")
    stat_p.add_argument("root", nargs="?", type=Path, help="brain root")
    stat_p.add_argument("--path", required=True, help="brain-relative path")
    stat_p.add_argument("--index", type=Path, help="index to check membership against")
    stat_p.add_argument("--json", action="store_true")

    contract_p = subparsers.add_parser(
        "contract",
        help="内容合同夜巡(validity/consistency,只读,报告无正文;H M0)。",
    )
    contract_p.add_argument("root", nargs="?", type=Path, help="brain root")
    contract_p.add_argument("--index", type=Path, help="SQLite index to check drift against")
    contract_p.add_argument("--path-prefix", default="", help="restrict to a brain-relative subtree")
    contract_p.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
    contract_p.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
    contract_p.add_argument("--json", action="store_true", help="Emit JSON. This is the default.")

    subparsers.add_parser("mcp", help="Run the read-only MCP stdio server.")
    return parser


META_DIRNAME = "_meta"
META_SUMMARY_MAX = 200


def _meta_summary(text: str) -> tuple[str, str]:
    """Return (title, first-paragraph summary) from a Markdown rule file."""
    title = ""
    summary = ""
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        if stripped.startswith("#"):
            if not title:
                title = stripped.lstrip("#").strip()
            continue
        summary = stripped
        break
    return title, summary[:META_SUMMARY_MAX]


def read_meta(root: Path, *, name: str | None = None, query: str | None = None, max_bytes: int = 200_000) -> JsonObject:
    """Read the brain ``_meta`` rule set -- the authoritative organize/spec corpus.

    Without ``name``: return a catalogue of ``_meta/*.md`` rule files (name,
    title, summary, bytes). With ``name``: return the full text of that one rule
    file. ``_meta`` is the single source of truth for how the library is
    organized; agents should read it before any write. The reader refuses to
    escape the ``_meta`` directory.
    """
    meta_dir = root / META_DIRNAME
    if not meta_dir.is_dir():
        return {
            "ok": False,
            "root": str(root),
            "meta_dir": str(meta_dir),
            "errors": ["_meta directory not found"],
        }
    if query:
        needle = query.lower()
        matches: list[JsonObject] = []
        for path in sorted(meta_dir.glob("*.md")):
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            hits = [f"{i + 1}: {ln.strip()}" for i, ln in enumerate(text.splitlines()) if needle in ln.lower()]
            if hits:
                matches.append({"name": path.name, "lines": hits[:12]})
        return {"ok": True, "root": str(root), "query": query, "match_count": len(matches),
                "matches": matches, "hint": "read the matching rule in full with --name <file>"}
    if name:
        stem = name[:-3] if name.endswith(".md") else name
        target = (meta_dir / f"{stem}.md").resolve()
        if target.parent != meta_dir.resolve() or not target.is_file():
            return {"ok": False, "root": str(root), "name": name, "errors": ["rule file not found in _meta"]}
        data = target.read_bytes()
        truncated = len(data) > max_bytes
        raw = data[:max_bytes].decode("utf-8", errors="ignore") if truncated else data.decode("utf-8", errors="replace")
        title, _ = _meta_summary(raw)
        return {
            "ok": True,
            "root": str(root),
            "name": f"{stem}.md",
            "title": title,
            "truncated": truncated,
            "text": raw,
        }
    rules: list[JsonObject] = []
    for path in sorted(meta_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        title, summary = _meta_summary(text)
        rules.append(
            {"name": path.name, "title": title, "summary": summary, "bytes": path.stat().st_size}
        )
    return {
        "ok": True,
        "root": str(root),
        "meta_dir": str(meta_dir),
        "count": len(rules),
        "rules": rules,
        "hint": "call meta with --name <file> (e.g. organize-rules) to read the full rule before writing",
    }


def _safe_under_root(root: Path, rel: str) -> Path | None:
    """Resolve a brain-relative path under root; reject escapes + personal-data (defense in
    depth with the gateway path gate)."""
    if not rel:
        return None
    try:
        resolved = (root / rel).resolve()
        base = root.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(base):
        return None
    parts = resolved.relative_to(base).parts
    # Case-insensitive: on Windows/macOS (where this is developed) Personal-Data
    # resolves to the same directory as personal-data and must be rejected too.
    if parts and parts[0].lower() in {d.lower() for d in EXCLUDED_TOP_DIRS}:
        return None
    return resolved


def _prefix_targets_excluded(prefix: str) -> bool:
    """True if a ``path LIKE 'prefix%'`` filter could match an excluded subtree.

    Defense-in-depth mirror of gate._check_path_prefix: a string prefix matches a
    personal-data row not only when it equals/points inside ``personal-data`` but
    when it is any prefix of that name (e.g. ``personal``). Case-insensitive, and
    strips surrounding whitespace to match the gate. The gate (which also honours
    policy-configured excluded_top_dirs) is authoritative and runs first; this
    mirror only covers the built-in default for standalone CLI use."""
    norm = prefix.strip().replace("\\", "/").lstrip("/").lower()
    if not norm:
        return True
    for raw in EXCLUDED_TOP_DIRS:
        excl = raw.lower()
        if excl.startswith(norm) or norm == excl or norm.startswith(excl + "/"):
            return True
    return False


def read_brain_file(root: Path, rel: str, *, offset: int = 0, limit: int = 0,
                    max_bytes: int = MAX_TEXT_BYTES) -> JsonObject:
    """Read a single brain file's UTF-8 text (window-able by lines, byte-capped)."""
    target = _safe_under_root(root, rel)
    if target is None:
        return {"ok": False, "path": rel, "errors": ["path not allowed (escape or personal-data)"]}
    if not target.is_file():
        return {"ok": False, "path": rel, "errors": ["file not found"]}
    data = target.read_bytes()
    byte_truncated = len(data) > max_bytes
    text = data[:max_bytes].decode("utf-8", errors="replace")
    lines = text.splitlines()
    total_lines = len(lines)
    line_truncated = False
    if offset or limit:
        # offset is 1-based (line 1 == first line); 0 means "from the start".
        start = max(0, offset - 1) if offset > 0 else 0
        end = (start + limit) if limit > 0 else total_lines
        line_truncated = end < total_lines or start > 0
        lines = lines[start:end]
    return {
        "ok": True, "path": _relative(target, root), "suffix": target.suffix.lower().lstrip("."),
        "bytes": len(data), "total_lines": total_lines, "truncated": byte_truncated or line_truncated,
        "text": "\n".join(lines),
    }


def list_tree(root: Path, rel: str) -> JsonObject:
    """List the immediate children of a brain directory (one level)."""
    target = _safe_under_root(root, rel) if rel else root.resolve()
    if target is None:
        return {"ok": False, "path": rel, "errors": ["path not allowed (escape or personal-data)"]}
    if not target.is_dir():
        return {"ok": False, "path": rel, "errors": ["not a directory"]}
    entries: list[JsonObject] = []
    try:
        for e in sorted(os.scandir(target), key=lambda x: (not x.is_dir(), x.name.lower())):
            if e.name in SKIP_DIRS_LITE:
                continue
            is_dir = e.is_dir()
            try:
                size = 0 if is_dir else e.stat().st_size
            except OSError:
                size = 0
            entries.append({
                "name": e.name, "is_dir": is_dir,
                "suffix": "" if is_dir else Path(e.name).suffix.lower().lstrip("."),
                "size_bytes": size, "docpack": is_dir and e.name.endswith(".docpack"),
            })
    except OSError as exc:
        return {"ok": False, "path": rel, "errors": [str(exc)]}
    return {"ok": True, "path": _relative(target, root), "count": len(entries), "entries": entries}


def stat_brain_path(root: Path, rel: str, index: Path | None) -> JsonObject:
    """Metadata for one path (no body): kind, size, mtime, suffix, indexed."""
    target = _safe_under_root(root, rel)
    if target is None:
        return {"ok": False, "path": rel, "errors": ["path not allowed (escape or personal-data)"]}
    if not target.exists():
        return {"ok": False, "path": rel, "errors": ["not found"]}
    st = target.stat()
    indexed = False
    if index and index.is_file():
        try:
            with sqlite3.connect(f"file:{index}?mode=ro", uri=True) as conn:
                indexed = conn.execute(
                    "SELECT 1 FROM documents WHERE path = ? LIMIT 1", (_relative(target, root),)
                ).fetchone() is not None
        except sqlite3.Error:
            indexed = False
    return {
        "ok": True, "path": _relative(target, root),
        "kind": "dir" if target.is_dir() else "file",
        "suffix": target.suffix.lower().lstrip(".") if target.is_file() else "",
        "size_bytes": st.st_size,
        "mtime": datetime.fromtimestamp(st.st_mtime, timezone.utc).isoformat(),
        "indexed": indexed,
    }


def index_freshness(root: Path, index: Path) -> JsonObject:
    """fresh|stale verdict: index build time vs the newest text file under knowledge/."""
    status = index_status(index)
    if not status.get("ok"):
        return {"ok": False, "fresh": False, "errors": status.get("errors", ["index status failed"])}
    idx_mtime = index.expanduser().resolve().stat().st_mtime
    knowledge = root / "knowledge"
    newest = 0.0
    newest_area = ""
    if knowledge.is_dir():
        for cur, dirs, files in os.walk(knowledge):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS_LITE]
            for f in files:
                if Path(f).suffix.lower().lstrip(".") not in INDEX_TEXT_SUFFIXES:
                    continue
                try:
                    m = (Path(cur) / f).stat().st_mtime
                except OSError:
                    continue
                if m > newest:
                    newest, newest_area = m, _relative(Path(cur), root)
    fresh = newest <= idx_mtime
    return {
        "ok": True, "fresh": fresh,
        "index_mtime": datetime.fromtimestamp(idx_mtime, timezone.utc).isoformat(),
        "newest_knowledge_mtime": datetime.fromtimestamp(newest, timezone.utc).isoformat() if newest else None,
        "lag_seconds": round(newest - idx_mtime, 1) if not fresh else 0,
        "newest_area": newest_area,
        "document_count": status.get("document_count"),
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    repo = None
    if getattr(args, "repo_root", None):
        repo = args.repo_root.expanduser().resolve()

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main([])

    _force_utf8_streams()

    if args.command == "index":
        if args.index_command == "build":
            data = build_index(
                args.root,
                args.out,
                force=args.force,
                allow_root_output=args.allow_root_output,
                max_files=args.max_files,
                max_bytes=args.max_bytes,
                embed=args.embed,
                incremental=args.incremental,
            )
        elif args.index_command == "status":
            data = index_status(args.index)
        elif args.index_command == "query":
            if args.path_prefix and _prefix_targets_excluded(args.path_prefix):
                data = {"ok": False, "errors": ["path_prefix may not target personal-data"]}
            else:
                data = query_index(
                    args.index, args.query, limit=args.limit,
                    suffix=args.suffix, path_prefix=args.path_prefix, title_only=args.title_only,
                    doc_type=args.doc_type, dept=args.dept, category=args.category,
                    date_from=args.date_from, date_to=args.date_to, order_by=args.order_by,
                    mode=args.mode,
                )
        elif args.index_command == "courses":
            data = query_courses(
                args.index, code=args.code, name_like=args.name_like,
                program_path=args.program_path, dept=args.dept, grade=args.grade,
                min_credits=args.min_credits, required_only=args.required_only, limit=args.limit,
            )
        elif args.index_command == "recent":
            if args.path_prefix and _prefix_targets_excluded(args.path_prefix):
                data = {"ok": False, "errors": ["path_prefix may not target personal-data"]}
            else:
                data = recent_documents(
                    args.index, limit=args.limit, suffix=args.suffix, path_prefix=args.path_prefix
                )
        elif args.index_command == "freshness":
            froot = resolve_brain_root(getattr(args, "brain_root", None))
            if froot is None:
                data = {"ok": False, "errors": ["brain root not found"]}
            else:
                data = index_freshness(froot, args.index)
        else:
            parser.error(f"unsupported index command: {args.index_command}")
            return 2
        _json_print(data)
        return 0 if data["ok"] else 1

    root = resolve_brain_root(getattr(args, "root", None))
    if args.command == "doctor":
        data = doctor(root, repo=repo)
        _json_print(data)
        return 0 if data["ok"] else 1

    if root is None:
        _json_print({"ok": False, "root": None, "errors": ["brain root not found"]})
        return 1

    if args.command == "scan":
        data = scan_library(root, max_files=args.max_files, sample_limit=args.sample_limit)
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "contract":
        from .contract import contract_report

        data = contract_report(
            root,
            index=args.index,
            path_prefix=args.path_prefix,
            max_files=args.max_files,
            sample_limit=args.sample_limit,
        )
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "docpacks":
        data = {"ok": root.is_dir(), "root": str(root), **summarize_docpacks(root, sample_limit=args.sample_limit)}
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "meta":
        data = read_meta(root, name=args.name, query=args.query, max_bytes=args.max_bytes)
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "read":
        data = read_brain_file(root, args.path, offset=args.offset, limit=args.limit, max_bytes=args.max_bytes)
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "tree":
        data = list_tree(root, args.path)
        _json_print(data)
        return 0 if data["ok"] else 1

    if args.command == "stat":
        data = stat_brain_path(root, args.path, args.index)
        _json_print(data)
        return 0 if data["ok"] else 1

    parser.error(f"unsupported command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
