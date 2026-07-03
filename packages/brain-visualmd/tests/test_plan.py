# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from brain_visualmd.plan import make_batches


def test_batches_cover_all_pages_contiguously():
    batches = make_batches(45, 22)
    assert [(b.start, b.end) for b in batches] == [(1, 22), (23, 44), (45, 45)]
    assert batches[0].name == "pages-001-022"
    # full coverage, no gaps/overlaps
    covered = [p for b in batches for p in b.pages]
    assert covered == list(range(1, 46))


def test_single_page():
    batches = make_batches(1)
    assert len(batches) == 1
    assert batches[0].start == 1 and batches[0].end == 1


def test_exact_multiple():
    batches = make_batches(44, 22)
    assert [(b.start, b.end) for b in batches] == [(1, 22), (23, 44)]
