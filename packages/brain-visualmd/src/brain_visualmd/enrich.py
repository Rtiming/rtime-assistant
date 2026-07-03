# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Enrich an existing docpack's 公式 sections with a formula recognizer.

A cheap post-pass: it does NOT re-transcribe the text. For each page it runs the
recognizer on the already-rendered ``images/p-NNN.png`` and replaces that page's
``### 公式`` section with the recognized block LaTeX (or ``- 无``). Use it to fill
in formulas a fast doc model left as a stub, without paying the transcription cost
again — e.g. ``pix2text`` (Chinese-aware, self-detecting) over a GLM-OCR draft.

  brain-visualmd enrich <docpack> --recognizer pix2text
"""

from __future__ import annotations

import re
from pathlib import Path

from .formula import get_formula_recognizer
from .spec import page_image_name

_PAGE_SPLIT = re.compile(r"(?=<!-- page: \d+ -->)")
_PAGE_NO = re.compile(r"<!-- page: (\d+) -->")
_F_START = "### 公式\n"
_F_END = "\n### 图表"


def _merged_md(docpack: Path) -> Path:
    cands = [p for p in docpack.glob("*.md") if p.parent == docpack]
    if not cands:
        raise FileNotFoundError(f"no merged <slug>.md in {docpack}")
    return cands[0]


def _replace_formula_section(block: str, formulas: list[str]) -> str:
    """Splice the 公式 section body (string ops — LaTeX backslashes break re.sub)."""
    start = block.find(_F_START)
    if start < 0:
        return block
    end = block.find(_F_END, start)  # index of the "\n" before "### 图表"
    if end < 0:
        return block
    body = "\n\n".join(f"$$\n{t}\n$$" for t in formulas) if formulas else "- 无"
    # blank line before the next section, matching the spec block layout
    return block[:start] + _F_START + body + "\n\n" + block[end + 1 :]


def enrich_docpack(docpack_dir: Path, recognizer_name: str = "pix2text") -> dict:
    """Fill every page's 公式 section from the page image. Returns a small summary."""
    docpack = Path(docpack_dir)
    md_path = _merged_md(docpack)
    images = docpack / "images"
    recognizer = get_formula_recognizer(recognizer_name)

    parts = _PAGE_SPLIT.split(md_path.read_text("utf-8"))
    out: list[str] = []
    pages = filled = total_formulas = 0
    for part in parts:
        m = _PAGE_NO.match(part)
        if not m:
            out.append(part)
            continue
        pages += 1
        png = images / page_image_name(int(m.group(1)))
        if png.exists():
            formulas = recognizer.detect_and_recognize(png.read_bytes()) or []
            new_part = _replace_formula_section(part, formulas)
            if new_part != part:
                filled += 1
                total_formulas += len(formulas)
            part = new_part
        out.append(part)

    md_path.write_text("".join(out), encoding="utf-8")
    return {
        "md": str(md_path),
        "recognizer": recognizer.name,
        "pages": pages,
        "pages_filled": filled,
        "formulas": total_formulas,
    }
