# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Default backend: emit per-batch task specs for Claude/Codex agents.

This reproduces the seed project's proven approach: deterministic scaffold, then
an agent (or Workflow) looks at the page PNGs and writes the batch Markdown. The
backend does not call any model itself — it writes a self-contained
``_batches/<name>.task.md`` and leaves ``_batches/<name>.md`` to be filled by the
dispatched agent. ``merge`` consumes the ``.md`` once present.
"""

from __future__ import annotations

from .. import prompt
from .base import BatchContext, BatchResult, TranscribeBackend


class AgentBackend(TranscribeBackend):
    name = "agent"
    version = "1"

    def process_batch(self, ctx: BatchContext) -> BatchResult:
        batches_dir = self._batches_dir(ctx)
        out_md = batches_dir / f"{ctx.batch.name}.md"
        draft_note = ""
        if ctx.draft_texts:
            have = [p for p in ctx.batch.pages if p in ctx.draft_texts]
            if have:
                draft_note = f"草稿 OCR 可参考(仅定位脉络):{have}"
        task = prompt.build_batch_task(
            slug=ctx.plan.slug,
            doc_title=ctx.doc_title,
            pages=ctx.batch.pages,
            image_refs=ctx.image_refs(),
            out_path=str(out_md),
            draft_note=draft_note,
        )
        task_path = batches_dir / f"{ctx.batch.name}.task.md"
        task_path.write_text(task, encoding="utf-8")
        return BatchResult(status="pending", path=task_path)
