#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Select representative DocPack sample candidates from a knowledge library.

The script is read-only. It emits JSON and never writes into `brain`.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any


CATEGORIES = {
    "pdf_text": {".pdf"},
    "pdf_low_text": {".pdf"},
    "ppt": {".ppt"},
    "pptx": {".pptx"},
    "doc": {".doc"},
    "docx": {".docx"},
    "xlsx": {".xlsx"},
    "image": {".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff"},
    "gif": {".gif"},
    "markdown": {".md", ".markdown"},
}


@dataclass(frozen=True)
class Candidate:
    category: str
    path: Path
    suffix: str
    reason: str
    risks: tuple[str, ...] = ()
    page_count: int | None = None


def _default_root() -> Path | None:
    for raw in (
        "/mnt/brain/knowledge",
        str(Path.home() / "brain" / "knowledge"),
        str(Path.home() / "OrangePi-Store" / "sync" / "brain" / "knowledge"),
    ):
        path = Path(raw)
        if path.is_dir():
            return path
    return None


def _relative(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _first_page_text_chars(path: Path) -> int | None:
    tool = shutil.which("pdftotext")
    if not tool:
        return None
    try:
        result = subprocess.run(
            [tool, "-enc", "UTF-8", "-f", "1", "-l", "1", str(path), "-"],
            capture_output=True,
            check=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return 0
    return len("".join((result.stdout or "").split()))


def _pdf_page_count(path: Path) -> int | None:
    tool = shutil.which("pdfinfo")
    if not tool:
        return None
    try:
        result = subprocess.run(
            [tool, "-enc", "UTF-8", str(path)],
            capture_output=True,
            check=True,
            timeout=15,
            encoding="utf-8",
            errors="replace",
        )
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        return None
    for line in (result.stdout or "").splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _candidate_sort_key(root: Path, path: Path) -> tuple[int, str]:
    relative = _relative(root, path)
    return (len(relative), relative.lower())


def _scan_files(root: Path) -> list[Path]:
    return sorted(
        (path for path in root.rglob("*") if path.is_file()),
        key=lambda path: _candidate_sort_key(root, path),
    )


def select_samples(root: Path, limit_per_category: int = 1) -> list[Candidate]:
    root = root.resolve()
    selected: dict[str, list[Candidate]] = {category: [] for category in CATEGORIES}

    for path in _scan_files(root):
        suffix = path.suffix.lower()
        if suffix == ".pdf":
            text_chars = _first_page_text_chars(path)
            page_count = _pdf_page_count(path)
            if text_chars == 0:
                category = "pdf_low_text"
                risks = ("first_page_zero_text",)
                reason = "first page has no extracted text"
            else:
                category = "pdf_text"
                risks = ()
                reason = "PDF has first-page text layer"
            if len(selected[category]) < limit_per_category:
                selected[category].append(
                    Candidate(category, path, suffix, reason, risks, page_count=page_count)
                )
            continue

        for category, suffixes in CATEGORIES.items():
            if category.startswith("pdf_"):
                continue
            if suffix in suffixes and len(selected[category]) < limit_per_category:
                selected[category].append(
                    Candidate(category, path, suffix, f"first {category} candidate")
                )
                break

        if all(len(items) >= limit_per_category for items in selected.values()):
            break

    return [candidate for category in CATEGORIES for candidate in selected[category]]


def _to_json(root: Path, samples: list[Candidate]) -> dict[str, Any]:
    selected_categories = {sample.category for sample in samples}
    missing_categories = [category for category in CATEGORIES if category not in selected_categories]
    return {
        "schema_version": 1,
        "root": str(root.resolve()),
        "sample_count": len(samples),
        "missing_categories": missing_categories,
        "samples": [
            {
                "category": sample.category,
                "path": sample.path.as_posix(),
                "relative_path": _relative(root.resolve(), sample.path),
                "suffix": sample.suffix,
                "reason": sample.reason,
                "risks": list(sample.risks),
                "page_count": sample.page_count,
            }
            for sample in samples
        ],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Select representative DocPack sample candidates.")
    parser.add_argument("root", nargs="?", type=Path, help="brain/knowledge root")
    parser.add_argument("--limit-per-category", type=int, default=1)
    parser.add_argument("--json", action="store_true", help="Emit JSON. This is the default format.")
    args = parser.parse_args(argv)

    root = args.root or _default_root()
    if root is None or not root.is_dir():
        print("error: knowledge root not found", file=sys.stderr)
        return 2
    if args.limit_per_category < 1:
        print("error: --limit-per-category must be >= 1", file=sys.stderr)
        return 2

    samples = select_samples(root, args.limit_per_category)
    print(json.dumps(_to_json(root, samples), ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
