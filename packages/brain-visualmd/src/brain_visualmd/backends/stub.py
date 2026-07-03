# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Stub backend — deterministic, no model. For tests and dry pipeline runs.

Produces spec-conformant placeholder Markdown so the full pipeline
(render -> plan -> transcribe -> merge -> validate) can run green without any
real transcription. Never use stub output as a real product.
"""

from __future__ import annotations

from ..models import PageRequest, PageResult
from ..spec import page_image_ref, page_marker
from .base import SyncPageBackend


class StubBackend(SyncPageBackend):
    name = "stub"
    version = "1"

    def transcribe_page(self, req: PageRequest, attempt: int = 0) -> PageResult:
        n = req.page_no
        md = (
            f"{page_marker(n)}\n"
            f"## 第 {n} 页：(stub)\n\n"
            f"{page_image_ref(n)}\n\n"
            "### 文字\n- (stub transcription placeholder)\n\n"
            "### 公式\n- 无\n\n"
            "### 图表\n- (stub)\n\n"
            "### 存疑\n- 无\n"
        )
        return PageResult(
            page_no=n, markdown=md, confidence=0.0, backend_id=self.backend_id
        )
