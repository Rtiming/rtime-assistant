# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Batch planning: split a page range into contiguous batches for transcription."""

from __future__ import annotations

from .models import Batch

DEFAULT_BATCH_PAGES = 22  # matches the seed project's pages-001-022 batches


def make_batches(pages: int, batch_pages: int = DEFAULT_BATCH_PAGES) -> list[Batch]:
    if pages < 1:
        raise ValueError("pages must be >= 1")
    if batch_pages < 1:
        raise ValueError("batch_pages must be >= 1")
    batches: list[Batch] = []
    start = 1
    index = 0
    while start <= pages:
        end = min(start + batch_pages - 1, pages)
        batches.append(Batch(index=index, start=start, end=end))
        start = end + 1
        index += 1
    return batches
