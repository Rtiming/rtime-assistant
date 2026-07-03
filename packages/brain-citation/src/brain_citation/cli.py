# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only Obsidian/Zotero citation diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


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

BIB_ENTRY_RE = re.compile(r"@\w+\s*\{\s*([^,\s]+)\s*,")
CITEKEY_RE = re.compile(r"(?<![\w@])@([A-Za-z0-9][A-Za-z0-9_:.:-]*)")
WIKILINK_RE = re.compile(r"(?<!!)\[\[([^\]\n]+)\]\]")
ZOTERO_URI_RE = re.compile(r"zotero://[^\s)<>\]]+", re.IGNORECASE)
PDF_MANIFEST_RELATIVE = Path("_indexes") / "pdf-manifest.jsonl"
PDF_MANIFEST_SAMPLE_FIELDS = (
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
            (root / "packages" / "brain-citation").is_dir()
            and (root / "docs" / "tooling-packaging.md").is_file()
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
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _walk_files(root: Path, *, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [
            name
            for name in dirs
            if name not in SKIP_DIRS
        ]
        current_path = Path(current)
        for name in names:
            files.append(current_path / name)
            if len(files) >= max_files:
                return files, True
    return files, False


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _sample_manifest_entry(entry: JsonObject) -> JsonObject:
    return {
        field: entry.get(field)
        for field in PDF_MANIFEST_SAMPLE_FIELDS
        if field in entry
    }


def _scan_pdf_manifest(
    root: Path,
    *,
    sample_limit: int,
) -> JsonObject:
    path = root / PDF_MANIFEST_RELATIVE
    result: JsonObject = {
        "exists": path.is_file(),
        "path": str(PDF_MANIFEST_RELATIVE),
        "line_count": 0,
        "valid_entries": 0,
        "invalid_json_lines": 0,
        "attachment_modes": {},
        "zotero_item_key_count": 0,
        "linked_attachment_key_count": 0,
        "stored_attachment_key_count": 0,
        "citekey_count": 0,
        "obsidian_note_count": 0,
        "brain_path_count": 0,
        "mobile_cache_count": 0,
        "samples": [],
        "read_error": "",
    }
    if not path.is_file():
        return result
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        result["read_error"] = str(exc)
        return result

    modes: Counter[str] = Counter()
    samples: list[JsonObject] = []
    result["line_count"] = len(lines)
    for line_no, line in enumerate(lines, start=1):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            loaded = json.loads(stripped)
        except json.JSONDecodeError:
            result["invalid_json_lines"] += 1
            continue
        if not isinstance(loaded, dict):
            result["invalid_json_lines"] += 1
            continue
        entry: JsonObject = loaded
        result["valid_entries"] += 1
        modes[str(entry.get("attachment_mode") or "unknown")] += 1
        if entry.get("zotero_item_key"):
            result["zotero_item_key_count"] += 1
        if entry.get("zotero_linked_attachment_key"):
            result["linked_attachment_key_count"] += 1
        if entry.get("zotero_stored_attachment_key") or entry.get("legacy_zotero_stored_attachment_key"):
            result["stored_attachment_key_count"] += 1
        if entry.get("citekey"):
            result["citekey_count"] += 1
        if entry.get("obsidian_note"):
            result["obsidian_note_count"] += 1
        if entry.get("brain_path") or entry.get("canonical_brain_path"):
            result["brain_path_count"] += 1
        if entry.get("mobile_cache") is True:
            result["mobile_cache_count"] += 1
        if len(samples) < sample_limit:
            sample = _sample_manifest_entry(entry)
            sample["line"] = line_no
            samples.append(sample)
    result["attachment_modes"] = dict(sorted(modes.items()))
    result["samples"] = samples
    return result


def _line_samples(
    samples: list[JsonObject],
    *,
    limit: int,
    item: JsonObject,
) -> None:
    if len(samples) < limit:
        samples.append(item)


def _parse_bib_entries(path: Path, *, root: Path, sample_limit: int) -> tuple[Counter[str], list[JsonObject]]:
    entries: Counter[str] = Counter()
    samples: list[JsonObject] = []
    for line_no, line in enumerate(_read_text(path).splitlines(), start=1):
        for match in BIB_ENTRY_RE.finditer(line):
            key = match.group(1).strip()
            if not key:
                continue
            entries[key] += 1
            _line_samples(
                samples,
                limit=sample_limit,
                item={"key": key, "path": _relative(path, root), "line": line_no},
            )
    return entries, samples


def _parse_markdown(
    path: Path,
    *,
    root: Path,
    sample_limit: int,
) -> JsonObject:
    citekeys: Counter[str] = Counter()
    wikilinks: Counter[str] = Counter()
    zotero_uris: list[JsonObject] = []
    citekey_samples: list[JsonObject] = []
    wikilink_samples: list[JsonObject] = []
    for line_no, line in enumerate(_read_text(path).splitlines(), start=1):
        line_keys = [match.group(1) for match in CITEKEY_RE.finditer(line)]
        if line_keys:
            for key in line_keys:
                citekeys[key] += 1
            _line_samples(
                citekey_samples,
                limit=sample_limit,
                item={
                    "path": _relative(path, root),
                    "line": line_no,
                    "keys": sorted(set(line_keys)),
                },
            )
        line_links = [match.group(1).split("|", 1)[0].strip() for match in WIKILINK_RE.finditer(line)]
        filtered_line_links = [target for target in line_links if target]
        if filtered_line_links:
            for target in filtered_line_links:
                wikilinks[target] += 1
            _line_samples(
                wikilink_samples,
                limit=sample_limit,
                item={
                    "path": _relative(path, root),
                    "line": line_no,
                    "targets": sorted(set(filtered_line_links)),
                },
            )
        for uri in ZOTERO_URI_RE.findall(line):
            _line_samples(
                zotero_uris,
                limit=sample_limit,
                item={"path": _relative(path, root), "line": line_no, "uri": uri},
            )
    return {
        "citekeys": citekeys,
        "wikilinks": wikilinks,
        "zotero_uris": zotero_uris,
        "citekey_samples": citekey_samples,
        "wikilink_samples": wikilink_samples,
    }


def _docpack_citation_summary(path: Path, *, root: Path) -> JsonObject:
    item: JsonObject = {
        "path": _relative(path, root),
        "anchor_count": 0,
        "status": "unknown",
        "read_error": "",
    }
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        item["read_error"] = str(exc)
        return item
    if isinstance(loaded, dict):
        anchors = loaded.get("anchors")
        if isinstance(anchors, list):
            item["anchor_count"] = len(anchors)
        status = loaded.get("status")
        if isinstance(status, str):
            item["status"] = status
    return item


def doctor(root: Path | None = None, *, repo: Path | None = None) -> JsonObject:
    resolved = resolve_brain_root(root)
    repo_root = repo
    repo_error = ""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)

    checks: JsonObject = {
        "brain_root": "ok" if resolved and resolved.is_dir() else "missing",
        "obsidian_config": "missing",
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
    }
    if resolved:
        checks["obsidian_config"] = "ok" if (resolved / ".obsidian").is_dir() else "missing"
    if repo_root:
        checks["repo_package"] = (
            "ok"
            if (repo_root / "packages" / "brain-citation" / "src" / "brain_citation" / "cli.py").is_file()
            else "missing"
        )
        checks["repo_skill"] = "ok" if (repo_root / "skills" / "brain-citation").is_dir() else "missing"
        checks["repo_plugin"] = "ok" if (repo_root / "plugins" / "brain-citation").is_dir() else "missing"
    risks = [name for name, status in checks.items() if status != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": checks["brain_root"] == "ok" and checks["repo_package"] == "ok",
        "root": str(resolved) if resolved else None,
        "repo_root": str(repo_root) if repo_root else None,
        "candidate_roots": [str(path) for path in candidate_brain_roots()],
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
    }


def scan_citations(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    files, truncated = _walk_files(root, max_files=max_files)
    markdown_files = [path for path in files if path.suffix.lower() == ".md"]
    bib_files = [path for path in files if path.suffix.lower() == ".bib"]
    citation_json_files = [
        path
        for path in files
        if path.name == "citations.json" and path.parent.name.endswith(".docpack")
    ]

    bib_entries: Counter[str] = Counter()
    bib_samples: list[JsonObject] = []
    for path in bib_files:
        entries, samples = _parse_bib_entries(path, root=root, sample_limit=sample_limit)
        bib_entries.update(entries)
        for sample in samples:
            _line_samples(bib_samples, limit=sample_limit, item=sample)

    cited_keys: Counter[str] = Counter()
    wikilinks: Counter[str] = Counter()
    citekey_samples: list[JsonObject] = []
    wikilink_samples: list[JsonObject] = []
    zotero_uri_samples: list[JsonObject] = []
    for path in markdown_files:
        parsed = _parse_markdown(path, root=root, sample_limit=sample_limit)
        cited_keys.update(parsed["citekeys"])
        wikilinks.update(parsed["wikilinks"])
        for sample in parsed["citekey_samples"]:
            _line_samples(citekey_samples, limit=sample_limit, item=sample)
        for sample in parsed["wikilink_samples"]:
            _line_samples(wikilink_samples, limit=sample_limit, item=sample)
        for sample in parsed["zotero_uris"]:
            _line_samples(zotero_uri_samples, limit=sample_limit, item=sample)

    docpack_samples: list[JsonObject] = []
    docpack_anchor_count = 0
    docpack_read_errors = 0
    for path in citation_json_files:
        item = _docpack_citation_summary(path, root=root)
        docpack_anchor_count += int(item["anchor_count"])
        if item["read_error"]:
            docpack_read_errors += 1
        _line_samples(docpack_samples, limit=sample_limit, item=item)

    pdf_manifest = _scan_pdf_manifest(root, sample_limit=sample_limit)
    missing_bib_keys = sorted(set(cited_keys) - set(bib_entries))
    unused_bib_keys = sorted(set(bib_entries) - set(cited_keys))
    risks: list[str] = []
    if missing_bib_keys:
        risks.append("missing_bib_entries")
    if zotero_uri_samples and not bib_entries:
        risks.append("zotero_uri_without_bib_entries")
    if docpack_read_errors:
        risks.append("docpack_citation_read_errors")
    if pdf_manifest["read_error"]:
        risks.append("pdf_manifest_read_error")
    if pdf_manifest["invalid_json_lines"]:
        risks.append("pdf_manifest_invalid_json")

    return {
        "ok": True,
        "root": str(root),
        "truncated": truncated,
        "files": {
            "scanned": len(files),
            "markdown": len(markdown_files),
            "bib": len(bib_files),
            "docpack_citations": len(citation_json_files),
        },
        "obsidian": {
            "vault_config_exists": (root / ".obsidian").is_dir(),
            "wikilink_count": sum(wikilinks.values()),
            "unique_wikilink_targets": len(wikilinks),
            "wikilink_samples": wikilink_samples,
        },
        "zotero": {
            "bib_file_count": len(bib_files),
            "bib_entry_count": sum(bib_entries.values()),
            "unique_bib_keys": len(bib_entries),
            "citation_key_occurrences": sum(cited_keys.values()),
            "unique_citation_keys": len(cited_keys),
            "zotero_uri_count": len(zotero_uri_samples),
            "bib_samples": bib_samples,
            "citekey_samples": citekey_samples,
            "zotero_uri_samples": zotero_uri_samples,
        },
        "crosswalk": {
            "keys_with_bib_entries": sorted(set(cited_keys) & set(bib_entries)),
            "missing_bib_keys": missing_bib_keys[:sample_limit],
            "missing_bib_key_count": len(missing_bib_keys),
            "unused_bib_keys": unused_bib_keys[:sample_limit],
            "unused_bib_key_count": len(unused_bib_keys),
        },
        "docpacks": {
            "citation_file_count": len(citation_json_files),
            "anchor_count": docpack_anchor_count,
            "read_error_count": docpack_read_errors,
            "samples": docpack_samples,
        },
        "pdf_manifest": pdf_manifest,
        "precision": {
            "markdown_line_refs": True,
            "bibtex_key_refs": True,
            "docpack_anchor_counts": True,
            "pdf_manifest_crosswalk": True,
            "zotero_annotation_level": False,
            "write_enabled": False,
        },
        "risks": risks,
    }


def panel(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    scan = scan_citations(root, max_files=max_files, sample_limit=sample_limit)
    review_risks = [
        risk
        for risk in scan["risks"]
        if risk in {"missing_bib_entries", "docpack_citation_read_errors"}
    ]
    return {
        "ok": not review_risks,
        "root": str(root),
        "panels": {
            "obsidian": scan["obsidian"],
            "zotero": scan["zotero"],
            "crosswalk": scan["crosswalk"],
            "docpacks": scan["docpacks"],
            "pdf_manifest": scan["pdf_manifest"],
            "precision": scan["precision"],
        },
        "risks": review_risks,
    }


def _path_arg(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def _require_root(root: Path | None) -> tuple[Path | None, JsonObject | None]:
    resolved = resolve_brain_root(root)
    if resolved is None:
        return None, {"ok": False, "errors": ["brain root not found; pass root or set BRAIN_ROOT"]}
    if not resolved.is_dir():
        return None, {"ok": False, "root": str(resolved), "errors": ["root is not a directory"]}
    return resolved, None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="brain-citation",
        description="Read-only Obsidian/Zotero citation diagnostics.",
    )
    parser.add_argument("--repo-root", dest="global_repo_root", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_scan_options(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("root", nargs="?", type=Path, help="brain root")
        subparser.add_argument("--sample-limit", type=int, default=DEFAULT_SAMPLE_LIMIT)
        subparser.add_argument("--max-files", type=int, default=DEFAULT_MAX_FILES)
        subparser.add_argument("--repo-root", type=Path, default=None)

    doctor_parser = subparsers.add_parser("doctor", help="check citation tooling and brain root")
    doctor_parser.add_argument("root", nargs="?", type=Path, help="brain root")
    doctor_parser.add_argument("--repo-root", type=Path, default=None)

    add_scan_options(subparsers.add_parser("scan", help="scan citation signals"))
    add_scan_options(subparsers.add_parser("panel", help="build a review-friendly citation panel"))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_repo_root = getattr(args, "repo_root", None) or args.global_repo_root
    repo_root = raw_repo_root.expanduser().resolve() if raw_repo_root else None

    if args.command == "doctor":
        data = doctor(_path_arg(str(args.root)) if args.root else None, repo=repo_root)
    else:
        root, error = _require_root(_path_arg(str(args.root)) if args.root else None)
        if error:
            _json_print(error)
            return 1
        assert root is not None
        if args.command == "scan":
            data = scan_citations(root, max_files=args.max_files, sample_limit=args.sample_limit)
        elif args.command == "panel":
            data = panel(root, max_files=args.max_files, sample_limit=args.sample_limit)
        else:  # pragma: no cover - argparse enforces valid commands
            raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
