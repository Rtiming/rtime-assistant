# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Backend contract — see ``docs/brain-visualmd-module.zh-CN.md`` §5.

A backend turns one batch of rendered page PNGs into per-page Markdown that
satisfies the standard (``docs/ai-readable-markdown-standard.zh-CN.md`` §4).
Two shapes are supported:

- synchronous (api / local VLM): subclass :class:`SyncPageBackend` and implement
  ``transcribe_page``; the base writes ``_batches/<name>.md``.
- task-emitting (agent / remote / human-in-loop): subclass
  :class:`TranscribeBackend` and implement ``process_batch`` to write a
  ``_batches/<name>.task.md`` spec; the Markdown is filled in later, then
  ``merge`` picks it up.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass
from pathlib import Path

from ..models import Batch, PageRequest, PageResult, Plan
from ..spec import page_image_ref, page_marker


class EmptyContentError(RuntimeError):
    """A model returned empty output for ONE page (degenerate scan / finish=length).

    A page-level, recoverable failure — distinct from a fatal misconfiguration
    (plain RuntimeError) so the batch loop can placeholder the page and continue
    instead of aborting the whole run.
    """


def _failed_page_block(page_no: int) -> str:
    """A spec-conformant placeholder for a page the model couldn't transcribe.

    Keeps the full-page PNG ref and flags the page under 存疑 for human follow-up,
    so a single degenerate page does not abort an otherwise-good run.
    """
    return (
        f"{page_marker(page_no)}\n"
        f"## 第 {page_no} 页：(自动转写失败)\n\n"
        f"{page_image_ref(page_no)}\n\n"
        "### 文字\n- (本页未能自动转写,请见整页 PNG)\n\n"
        "### 公式\n- 无\n\n"
        "### 图表\n- 无\n\n"
        "### 存疑\n- 本页自动转写失败(模型返回空),需人工对照整页 PNG 补全\n"
    )


@dataclass
class BatchContext:
    docpack_dir: Path
    plan: Plan
    batch: Batch
    draft_texts: dict[int, str] | None = None

    @property
    def doc_title(self) -> str:
        return self.plan.slug

    def page_request(self, page_no: int) -> PageRequest:
        draft = (self.draft_texts or {}).get(page_no)
        rel = f"images/p-{page_no:03d}.png"
        return PageRequest(
            page_no=page_no,
            page_png=rel,
            page_png_path=str(self.docpack_dir / rel),
            doc_title=self.doc_title,
            draft_text=draft,
        )

    def image_refs(self) -> list[str]:
        return [page_image_ref(p) for p in self.batch.pages]


@dataclass
class BatchResult:
    status: str  # "written" (md ready) | "pending" (task emitted, awaiting fill)
    path: Path


class TranscribeBackend(abc.ABC):
    name: str = "base"
    version: str = "0"

    @property
    def backend_id(self) -> str:
        return f"{self.name}:{self.version}"

    @abc.abstractmethod
    def process_batch(self, ctx: BatchContext) -> BatchResult:
        """Produce ``_batches/<batch.name>.md`` or emit a ``.task.md`` spec."""

    def _batches_dir(self, ctx: BatchContext) -> Path:
        d = ctx.docpack_dir / "_batches"
        d.mkdir(parents=True, exist_ok=True)
        return d


class SyncPageBackend(TranscribeBackend):
    """Base for backends that synchronously transcribe page by page.

    Quality guard for weak local models: if a page fails the per-page machine
    gate (missing section / inline ``$`` / no PNG ref), re-call up to
    ``max_page_retries`` times (``transcribe_page`` gets ``attempt`` so a backend
    can vary temperature / add a corrective hint). Background runs can afford it.
    """

    max_page_retries: int = 0

    @abc.abstractmethod
    def transcribe_page(self, req: PageRequest, attempt: int = 0) -> PageResult: ...

    def process_batch(self, ctx: BatchContext) -> BatchResult:
        from ..validate import is_ok, validate_page_block

        batches_dir = self._batches_dir(ctx)
        parts: list[str] = []
        for page_no in ctx.batch.pages:
            # per-page checkpoint: an interrupted long run resumes at the page,
            # not the whole batch (matters on a slow box / overnight batch).
            part = batches_dir / f"{ctx.batch.name}.p{page_no:03d}.md"
            if part.exists() and part.read_text("utf-8").strip():
                parts.append(part.read_text("utf-8").rstrip() + "\n")
                continue
            req = ctx.page_request(page_no)
            best: PageResult | None = None
            for attempt in range(self.max_page_retries + 1):
                try:
                    result = self.transcribe_page(req, attempt=attempt)
                except (EmptyContentError, OSError):
                    # page-level recoverable error (empty output / timeout / network)
                    # -> retry, then placeholder. A fatal config error (plain
                    # RuntimeError) is NOT caught: it propagates and fails loudly.
                    continue
                best = result
                if is_ok(
                    validate_page_block(result.markdown, page_no, ctx.docpack_dir)
                ):
                    break
            if best is None:
                # every attempt errored — emit a 存疑-flagged placeholder so one bad
                # page (degenerate scan / empty model output) can't abort the whole
                # run. The PNG ref is kept; the page is marked for human follow-up.
                best = PageResult(
                    page_no=page_no,
                    markdown=_failed_page_block(page_no),
                    backend_id=self.backend_id,
                )
            md = best.markdown.rstrip() + "\n"
            part.write_text(md, encoding="utf-8")
            parts.append(md)
        out = batches_dir / f"{ctx.batch.name}.md"
        out.write_text("\n".join(parts), encoding="utf-8")
        return BatchResult(status="written", path=out)
