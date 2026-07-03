# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Brain unlock resolution for the assistant gateway.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Turns an Obsidian request (frontmatter path keys, active PDF, or selection) into
read-only brain paths, with a small manifest-basename cache. Pure given its
inputs — no subprocess, no model run, no cross-cluster calls; depends only on the
already-carved _common leaf. Behavior-invariant move.
"""

from __future__ import annotations

import json
from pathlib import Path

from _common import (
    FRONTMATTER_PATH_KEYS,
    extract_frontmatter,
    safe_brain_path,
)


def collect_unlocks(frontmatter: dict, brain_root: Path) -> list[tuple[str, Path]]:
    """Map frontmatter path fields to verified absolute brain paths."""
    unlocks: list[tuple[str, Path]] = []
    anchor: Path | None = None
    source = frontmatter.get("source") or frontmatter.get("brain_path")
    if source:
        resolved = safe_brain_path(source, brain_root)
        if resolved is not None:
            anchor = resolved.parent
            unlocks.append(("原件", resolved))
    for key in FRONTMATTER_PATH_KEYS:
        if key in ("source", "brain_path"):
            continue
        raw = frontmatter.get(key)
        if not raw:
            continue
        resolved = safe_brain_path(raw, brain_root, anchor_dir=anchor)
        if resolved is not None and all(resolved != p for _, p in unlocks):
            label = {
                "pdf_file": "原件",
                "page_image_dir": "页图目录",
                "raw_text_dir": "诊断文本层",
                "page_text_dir": "诊断文本层",
            }.get(key, key)
            unlocks.append((label, resolved))
    return unlocks

_MANIFEST_CACHE: dict = {"mtime": None, "by_basename": {}}

def manifest_lookup(basename: str, brain_root: Path) -> str | None:
    """Resolve a PDF basename to its brain_path via pdf-manifest.jsonl.

    Cached by manifest mtime. Canonical entries win over duplicates."""
    manifest = brain_root / "_indexes" / "pdf-manifest.jsonl"
    try:
        mtime = manifest.stat().st_mtime
    except OSError:
        return None
    if _MANIFEST_CACHE["mtime"] != mtime:
        by_name: dict = {}
        try:
            for line in manifest.read_text(encoding="utf-8").splitlines():
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                bp = rec.get("brain_path") or rec.get("canonical_brain_path")
                if not bp:
                    continue
                name = bp.rsplit("/", 1)[-1]
                if name not in by_name or rec.get("canonical"):
                    by_name[name] = bp
        except OSError:
            return None
        _MANIFEST_CACHE.update(mtime=mtime, by_basename=by_name)
    return _MANIFEST_CACHE["by_basename"].get(basename)

def resolve_pdf_unlocks(active_path: str, brain_root: Path) -> list[tuple[str, Path]]:
    """When the user is viewing a PDF, unlock its brain canonical, the
    companion md, and the page-image dir — via manifest basename lookup,
    so the vault symlink layout never needs a mapping table."""
    basename = active_path.rsplit("/", 1)[-1]
    brain_rel = manifest_lookup(basename, brain_root)
    if not brain_rel:
        return []
    unlocks: list[tuple[str, Path]] = []
    pdf = safe_brain_path(brain_rel, brain_root)
    if pdf is None:
        return []
    unlocks.append(("正在阅读的PDF原件", pdf))
    stem = pdf.name[: -len(pdf.suffix)] if pdf.suffix else pdf.name
    for label, rel in (
        ("伴生笔记", f"{stem}.md"),
        ("页图目录", f"images/{stem}"),
        ("诊断文本层", f"text/{stem}"),
    ):
        cand = safe_brain_path(rel, brain_root, anchor_dir=pdf.parent)
        if cand is not None:
            unlocks.append((label, cand))
    return unlocks

def resolve_request_unlocks(body: dict, cfg: dict) -> list[tuple[str, Path]]:
    """Resolve the Obsidian payload into read-only brain paths."""
    context = body.get("context") or {}
    note_text = (context.get("note") or {}).get("text") or ""
    active_path = (context.get("active_file") or {}).get("path") or ""
    if active_path.lower().endswith(".pdf"):
        return resolve_pdf_unlocks(active_path, cfg["brain_root"])
    return collect_unlocks(extract_frontmatter(note_text), cfg["brain_root"])

def _brain_rel(path: Path, cfg: dict) -> str:
    try:
        return path.resolve().relative_to(Path(cfg["brain_root"]).resolve()).as_posix()
    except (OSError, ValueError):
        return path.as_posix()

def public_unlocks(unlocks: list[tuple[str, Path]], cfg: dict) -> list[dict]:
    return [{"label": label, "path": _brain_rel(path, cfg)} for label, path in unlocks]
