# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Doc-OCR backend: fast dedicated document models (GLM-OCR, PaddleOCR-VL, dots.ocr).

These small (~0.9-3B) models transcribe a page DIRECTLY into Markdown+LaTeX with no
slow "thinking" — ~10x faster than a reasoning VLM (M4: ~5-40s/page vs ~4-5 min)
and strong on Chinese formulas. Trade-off: they transcribe but do not *judge* (no
存疑 anomaly flagging, no figure reasoning). So use this for fast faithful
transcription of the bulk, and pair with `escalate` to add judgment on hard pages.

Serve with an OpenAI-compatible endpoint and point the usual env at it, e.g.:
  ollama run hf.co/ggml-org/GLM-OCR-GGUF:Q8_0      # or: llama-server -hf <repo>
  export VISUALMD_VISION_BASE_URL=http://localhost:11434/v1
  export VISUALMD_VISION_MODEL=hf.co/ggml-org/GLM-OCR-GGUF:Q8_0
  brain-visualmd build <src> --backend doc

Optional per-region formula refine: if ``analyze`` produced ``layout.json`` and
``VISUALMD_FORMULA_RECOGNIZER=rapid`` is set, the 公式 section is filled with
LaTeX recognized from each cropped formula region by a specialist (CDM ~0.97),
instead of pointing at the inline LaTeX.
"""

from __future__ import annotations

import os
from pathlib import Path

from ..formula import get_formula_recognizer, recognize_page_formulas
from ..layout import load_layout
from ..models import PageRequest, PageResult
from ..spec import page_image_ref, page_marker
from .vision_api import VisionApiBackend, _normalize_inline_math

_DOC_PROMPT = (
    "把这一页文档转写成 Markdown:正文按阅读顺序;公式一律块级 $$ LaTeX;"
    "表格用 Markdown 表格。只输出页面内容,不要解释、不要寒暄。"
)


class DocOcrBackend(VisionApiBackend):
    name = "doc"
    max_page_retries = 1  # doc models are direct; one retry is plenty

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self._formula = get_formula_recognizer(
            os.environ.get("VISUALMD_FORMULA_RECOGNIZER", "none")
        )
        self._layout_cache: dict = {}

    def transcribe_page(self, req: PageRequest, attempt: int = 0) -> PageResult:
        content = self._run(req, _DOC_PROMPT, 0.0 if attempt == 0 else 0.2)
        return PageResult(
            page_no=req.page_no,
            markdown=_wrap_doc_page(req.page_no, content, self._formula_section(req)),
            backend_id=self.backend_id,
        )

    def _formula_section(self, req: PageRequest) -> str | None:
        """Per-region LaTeX for the 公式 section, if a recognizer + layout are present."""
        if self._formula.name == "none" or not req.page_png_path:
            return None
        layout = self._page_layout(req)
        latexes = recognize_page_formulas(req.page_png_path, layout, self._formula)
        if not latexes:
            return None
        return "\n\n".join(f"$$\n{tex}\n$$" for tex in latexes)

    def _page_layout(self, req: PageRequest):
        docpack = Path(req.page_png_path).parent.parent
        if docpack not in self._layout_cache:
            self._layout_cache[docpack] = load_layout(docpack)
        return self._layout_cache[docpack].get(req.page_no)


def _wrap_doc_page(
    page_no: int, content: str, formula_section: str | None = None
) -> str:
    """Wrap a doc model's free Markdown into a spec-conformant page block.

    The transcription (text + block ``$$`` formulas) goes under 文字. The 公式
    section is the specialist's per-region LaTeX when available, else a pointer to
    the inline LaTeX. 图表/存疑 are stubbed; a later `escalate` pass fills them.
    """
    content = _normalize_inline_math(content.strip())  # inline $...$ -> \(...\)
    formula = formula_section if formula_section else "- 见正文内 LaTeX"
    return (
        f"{page_marker(page_no)}\n"
        f"## 第 {page_no} 页：{_first_line(content)}\n\n"
        f"{page_image_ref(page_no)}\n\n"
        f"### 文字\n{content}\n\n"
        f"### 公式\n{formula}\n\n"
        "### 图表\n- 无\n\n"
        "### 存疑\n- 无\n"
    )


def _first_line(content: str) -> str:
    """First real text line for the heading — skip formulas/tables, never emit ``$``."""
    for line in content.splitlines():
        s = line.strip().lstrip("#").strip()
        if not s or s.startswith(("$", "\\(", "\\[", "|")):
            continue  # formula / table-row / blank
        s = s.replace("$", "").strip()  # a heading must never carry a stray $
        if s:
            return s[:60]
    return ""
