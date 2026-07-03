# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Escalation backend: fast base model for the bulk, strong model on hard pages.

Runs a fast (non-thinking) backend on every page, then re-does ONLY the pages
that need judgment — those that fail the machine gate or contain a block formula
— with a stronger (thinking) backend. Title / outline / figure-only pages stay
fast; formula pages get the slow-but-accurate model. This is the practical way
to process a whole library without paying the ~4-5 min/page thinking cost on
every page. See ``docs/brain-visualmd-module.zh-CN.md`` §6b/§6c.

Config (env): ``VISUALMD_ESCALATE_BASE_MODEL`` (default hf.co/ggml-org/GLM-OCR-GGUF:Q8_0),
``VISUALMD_ESCALATE_STRONG_MODEL`` (default qwen3-vl:8b). Both use the same
``VISUALMD_VISION_BASE_URL``.
"""

from __future__ import annotations

import os

from ..models import PageRequest, PageResult
from .base import SyncPageBackend


class EscalationBackend(SyncPageBackend):
    name = "escalate"
    max_page_retries = 0  # escalation to the strong model replaces the gate retry

    def __init__(self, base=None, strong=None) -> None:
        from .doc import DocOcrBackend
        from .vision_api import VisionApiBackend

        # base = fast dedicated doc model (GLM-OCR) on every page;
        # strong = thinking VLM (qwen3-vl) only on hard pages, for judgment.
        base_model = os.environ.get(
            "VISUALMD_ESCALATE_BASE_MODEL", "hf.co/ggml-org/GLM-OCR-GGUF:Q8_0"
        )
        strong_model = os.environ.get("VISUALMD_ESCALATE_STRONG_MODEL", "qwen3-vl:8b")
        self.base = base if base is not None else DocOcrBackend(model=base_model)
        self.strong = (
            strong if strong is not None else VisionApiBackend(model=strong_model)
        )
        self._layout_cache: dict = {}

    @property
    def version(self) -> str:
        return f"{self.base.backend_id}->{self.strong.backend_id}"

    def _page_layout(self, req: PageRequest):
        """The detected layout for this page, if `analyze` ran (layout.json), else None."""
        if not req.page_png_path:
            return None
        from pathlib import Path

        from ..layout import load_layout

        docpack = Path(req.page_png_path).parent.parent
        if docpack not in self._layout_cache:
            self._layout_cache[docpack] = load_layout(docpack)
        return self._layout_cache[docpack].get(req.page_no)

    def _should_escalate(self, req: PageRequest, base_md: str) -> bool:
        from ..layout import TABLE
        from ..validate import is_ok, validate_page_block

        if not is_ok(validate_page_block(base_md, req.page_no)):
            return True  # malformed base output -> always escalate
        layout = self._page_layout(req)
        if layout is not None:  # prefer the pixel-level signal from pre-analysis
            return layout.has_formula or any(r.cls == TABLE for r in layout.regions)
        return "$$" in base_md  # fallback: guess from the base output

    def transcribe_page(self, req: PageRequest, attempt: int = 0) -> PageResult:
        from ..validate import is_ok, validate_page_block

        base_result = self.base.transcribe_page(req, attempt=attempt)
        if not self._should_escalate(req, base_result.markdown):
            return base_result
        # hard page: try the strong model (its own gate retries). If it fails for
        # ANY reason — empty output (RuntimeError), timeout, connection error —
        # or never passes the gate, FALL BACK to the base result. Never worse,
        # never empty, never aborts the batch on one slow/bad page.
        for a in range(3):
            try:
                strong_result = self.strong.transcribe_page(req, attempt=a)
            except Exception:
                break
            if is_ok(validate_page_block(strong_result.markdown, req.page_no)):
                return strong_result
        return base_result
