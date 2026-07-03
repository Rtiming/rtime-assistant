# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Merge per-batch Markdown into a single ``<slug>.md`` with frontmatter."""

from __future__ import annotations

from datetime import date
from pathlib import Path

from .models import Plan
from .spec import BANNER, SPEC_VERSION, TIER, render_frontmatter


class MergeError(RuntimeError):
    pass


def pending_batches(docpack_dir: Path, plan: Plan) -> list[str]:
    """Batch names whose ``.md`` is not yet written (still awaiting transcription)."""
    batches_dir = docpack_dir / "_batches"
    missing = []
    for b in plan.batches:
        if not (batches_dir / f"{b.name}.md").exists():
            missing.append(b.name)
    return missing


def merge_docpack(
    docpack_dir: Path,
    *,
    backend_id: str = "",
    status: str = "draft",
    title: str | None = None,
    created: str | None = None,
) -> Path:
    docpack_dir = Path(docpack_dir)
    plan = Plan.read(docpack_dir)
    missing = pending_batches(docpack_dir, plan)
    if missing:
        raise MergeError(
            "cannot merge; these batches have no .md yet (transcription pending): "
            + ", ".join(missing)
        )

    batches_dir = docpack_dir / "_batches"
    body_parts = [
        (batches_dir / f"{b.name}.md").read_text("utf-8").rstrip() for b in plan.batches
    ]

    meta = {
        "title": title or plan.slug,
        "source": plan.source,
        "source_sha256": plan.source_sha256,
        "tier": TIER,
        "status": status,
        "pages": plan.pages,
        "backend": backend_id or "unknown",
        "spec_version": SPEC_VERSION,
        "created": created or date.today().isoformat(),
    }

    doc = (
        render_frontmatter(meta)
        + f"\n# {meta['title']}\n\n{BANNER}\n\n"
        + "\n\n".join(body_parts)
        + "\n"
    )
    out = docpack_dir / f"{plan.slug}.md"
    out.write_text(doc, encoding="utf-8")
    return out
