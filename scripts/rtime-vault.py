#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only command line helpers for the rtime brain/vault view.

The CLI intentionally only reads ``brain`` and the Obsidian vault. It never
creates files, opens Obsidian by default, or mutates manifests.
"""

from __future__ import annotations

import argparse
import json
import os
import urllib.parse
from pathlib import Path
from typing import Any

DEFAULT_BRAIN_ROOT = Path(
    os.environ.get("RTIME_BRAIN_ROOT", str(Path.home() / "OrangePi-Store" / "sync" / "brain"))
)
DEFAULT_VAULT_ROOT = Path(os.environ.get("RTIME_VAULT_ROOT", str(Path.home() / "Desktop" / "brain-notes")))
DEFAULT_VAULT_NAME = os.environ.get("RTIME_VAULT_NAME", "brain-notes")

DERIVED_DIR_NAMES = {"images", "text", "__pycache__"}
DERIVED_SUFFIXES = {".json", ".jsonl", ".log", ".tmp", ".png", ".jpg", ".jpeg", ".webp"}
VISIBLE_SUFFIXES = {".pdf", ".md", ".ppt", ".pptx", ".doc", ".docx"}


def _json_default(value: Any) -> str:
    if isinstance(value, Path):
        return str(value)
    raise TypeError(f"cannot encode {type(value)!r}")


def emit(payload: dict[str, Any], *, as_json: bool = True) -> None:
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, default=_json_default))
        return
    print(json.dumps(payload, ensure_ascii=False, default=_json_default))


def read_manifest(brain_root: Path) -> list[dict[str, Any]]:
    manifest = brain_root / "_indexes" / "pdf-manifest.jsonl"
    rows: list[dict[str, Any]] = []
    if not manifest.exists():
        return rows
    with manifest.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"_invalid": True, "lineno": lineno})
                continue
            row["_lineno"] = lineno
            rows.append(row)
    return rows


def manifest_path(row: dict[str, Any], brain_root: Path) -> Path | None:
    raw = row.get("brain_path") or row.get("path") or row.get("canonical_path")
    if not raw:
        return None
    path = Path(str(raw))
    if not path.is_absolute():
        path = brain_root / path
    return path


def companion_paths(pdf_path: Path) -> dict[str, str | bool]:
    md = pdf_path.with_suffix(".md")
    image_dir = pdf_path.parent / "images" / pdf_path.stem
    text_dir = pdf_path.parent / "text" / pdf_path.stem
    return {
        "original": str(pdf_path),
        "original_exists": pdf_path.exists(),
        "companion_md": str(md),
        "companion_md_exists": md.exists(),
        "page_image_dir": str(image_dir),
        "page_image_dir_exists": image_dir.exists(),
        "page_text_dir": str(text_dir),
        "page_text_dir_exists": text_dir.exists(),
    }


def resolve_pdf(query: str, brain_root: Path) -> dict[str, Any]:
    rows = read_manifest(brain_root)
    needle = query.casefold()
    matches: list[dict[str, Any]] = []
    for row in rows:
        if row.get("_invalid"):
            continue
        pdf_path = manifest_path(row, brain_root)
        if pdf_path is None:
            continue
        basename = pdf_path.name
        stem = pdf_path.stem
        haystacks = [
            basename.casefold(),
            stem.casefold(),
            str(row.get("title") or "").casefold(),
            str(row.get("citekey") or "").casefold(),
            str(row.get("zotero_key") or row.get("zotero_item_key") or "").casefold(),
        ]
        exact = needle in {basename.casefold(), stem.casefold()}
        if exact or any(needle and needle in item for item in haystacks):
            evidence = companion_paths(pdf_path)
            matches.append(
                {
                    "basename": basename,
                    "brain_path": str(pdf_path.relative_to(brain_root))
                    if pdf_path.is_absolute() and brain_root in pdf_path.parents
                    else str(pdf_path),
                    "manifest_line": row.get("_lineno"),
                    "sha256": row.get("sha256"),
                    "citekey": row.get("citekey"),
                    "zotero_item_key": row.get("zotero_item_key") or row.get("zotero_key"),
                    "canonical": bool(row.get("canonical", True)),
                    "match": "exact" if exact else "partial",
                    **evidence,
                }
            )
    matches.sort(key=lambda item: (item["match"] != "exact", not item.get("canonical"), item["basename"]))
    return {"query": query, "brain_root": str(brain_root), "match_count": len(matches), "matches": matches}


def visible_entry(path: Path, names_in_dir: set[str]) -> dict[str, Any] | None:
    if path.name.startswith(".") or path.name in DERIVED_DIR_NAMES:
        return None
    if path.is_dir():
        return {"name": path.name, "kind": "directory", "path": str(path)}
    suffix = path.suffix.casefold()
    if suffix in DERIVED_SUFFIXES:
        return None
    if suffix == ".md" and f"{path.stem}.pdf" in names_in_dir:
        return None
    if suffix and suffix not in VISIBLE_SUFFIXES:
        return None
    kind = {
        ".pdf": "pdf",
        ".md": "note",
        ".ppt": "slides",
        ".pptx": "slides",
        ".doc": "document",
        ".docx": "document",
    }.get(suffix, "file")
    return {"name": path.name, "kind": kind, "path": str(path)}


def list_entries(presentation_dir: str, vault_root: Path) -> dict[str, Any]:
    root = Path(presentation_dir)
    if not root.is_absolute():
        root = vault_root / root
    if not root.exists():
        return {"presentation_dir": str(root), "exists": False, "entries": []}
    names = {item.name for item in root.iterdir()} if root.is_dir() else set()
    entries: list[dict[str, Any]] = []
    if root.is_dir():
        for item in sorted(root.iterdir(), key=lambda p: (not p.is_dir(), p.name.casefold())):
            entry = visible_entry(item, names)
            if entry is not None:
                entry["relative_path"] = str(item.relative_to(vault_root)) if vault_root in item.parents else str(item)
                entries.append(entry)
    return {"presentation_dir": str(root), "exists": True, "entry_count": len(entries), "entries": entries}


def obsidian_uri(note_relative_path: str, *, heading: str | None, vault_name: str) -> dict[str, Any]:
    query = {"vault": vault_name, "file": note_relative_path}
    if heading:
        query["heading"] = heading
    uri = "obsidian://open?" + urllib.parse.urlencode(query, quote_via=urllib.parse.quote)
    return {
        "vault": vault_name,
        "file": note_relative_path,
        "heading": heading,
        "uri": uri,
        "open_command": ["open", uri],
        "gui_self_check": "Run the open_command on the Mac and confirm Obsidian jumps to the target note.",
    }


def read_relations(brain_root: Path) -> list[dict[str, Any]]:
    path = brain_root / "_indexes" / "relations.jsonl"
    rows: list[dict[str, Any]] = []
    if not path.exists():
        return rows
    with path.open("r", encoding="utf-8") as fh:
        for lineno, line in enumerate(fh, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"_invalid": True, "lineno": lineno})
                continue
            row["_lineno"] = lineno
            rows.append(row)
    return rows


def _relation_aliases(query: str, brain_root: Path) -> set[str]:
    raw = query.strip()
    aliases = {raw}
    path = Path(raw)
    if path.is_absolute():
        try:
            aliases.add(path.resolve().relative_to(brain_root.resolve()).as_posix())
        except (OSError, ValueError):
            pass
    if not path.suffix:
        aliases.add(raw + ".md")
        aliases.add(raw + ".pdf")
    aliases.add(path.name)
    return {item for item in aliases if item}


def related_materials(query: str, brain_root: Path, limit: int = 8) -> dict[str, Any]:
    rows = [row for row in read_relations(brain_root) if not row.get("_invalid")]
    aliases = _relation_aliases(query, brain_root)
    matches = [
        row
        for row in rows
        if row.get("src") in aliases
        or Path(str(row.get("src") or "")).name in aliases
    ]
    matches.sort(key=lambda row: (-float(row.get("score") or 0), str(row.get("rel") or ""), str(row.get("dst") or "")))
    return {
        "query": query,
        "brain_root": str(brain_root),
        "relations_path": str(brain_root / "_indexes" / "relations.jsonl"),
        "match_count": len(matches),
        "matches": matches[:limit],
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    parser.add_argument("--vault-root", type=Path, default=DEFAULT_VAULT_ROOT)
    parser.add_argument("--vault-name", default=DEFAULT_VAULT_NAME)
    parser.add_argument("--json", action="store_true", default=True, help="emit JSON (default)")
    sub = parser.add_subparsers(dest="command", required=True)

    p_list = sub.add_parser("list", help="list visible source entries in a vault presentation directory")
    p_list.add_argument("presentation_dir")

    p_resolve = sub.add_parser("resolve", help="resolve a PDF basename/title through brain _indexes/pdf-manifest.jsonl")
    p_resolve.add_argument("query")

    p_uri = sub.add_parser("uri", help="generate an Obsidian open URI for a vault-relative note path")
    p_uri.add_argument("note_relative_path")
    p_uri.add_argument("--heading")

    p_related = sub.add_parser("related", help="read _indexes/relations.jsonl and list related materials")
    p_related.add_argument("query")
    p_related.add_argument("--limit", type=int, default=8)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    brain_root = args.brain_root.expanduser().resolve()
    vault_root = args.vault_root.expanduser().resolve()
    if args.command == "list":
        emit(list_entries(args.presentation_dir, vault_root), as_json=args.json)
        return 0
    if args.command == "resolve":
        payload = resolve_pdf(args.query, brain_root)
        emit(payload, as_json=args.json)
        return 0 if payload["match_count"] else 2
    if args.command == "uri":
        emit(
            obsidian_uri(args.note_relative_path, heading=args.heading, vault_name=args.vault_name),
            as_json=args.json,
        )
        return 0
    if args.command == "related":
        payload = related_materials(args.query, brain_root, limit=max(1, args.limit))
        emit(payload, as_json=args.json)
        return 0 if payload["match_count"] else 2
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
