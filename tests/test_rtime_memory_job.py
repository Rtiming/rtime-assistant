# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Unit tests for the rtime-memory-job extract wiring (materials -> candidates)."""

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
_loader = SourceFileLoader("rtime_memory_job", str(REPO / "deploy/bin/rtime-memory-job"))
_spec = importlib.util.spec_from_loader("rtime_memory_job", _loader)
job = importlib.util.module_from_spec(_spec)
_loader.exec_module(job)


def test_materials_to_extract_selects_new_skips_processed_and_future(tmp_path):
    mdir = tmp_path / "materials"
    mdir.mkdir()
    (mdir / "2026-06-15.jsonl").write_text("a\nb\n", encoding="utf-8")  # processed
    (mdir / "2026-06-16.jsonl").write_text("x\n", encoding="utf-8")  # new
    (mdir / "2026-06-17.jsonl").write_text("future\n", encoding="utf-8")  # after date
    state = {"2026-06-15.jsonl": 2}
    pending = job.materials_to_extract(mdir, "2026-06-16", state)
    assert [p.name for p in pending] == ["2026-06-16.jsonl"]


def test_materials_to_extract_reprocesses_grown_file(tmp_path):
    mdir = tmp_path / "materials"
    mdir.mkdir()
    (mdir / "2026-06-16.jsonl").write_text("a\nb\nc\n", encoding="utf-8")
    state = {"2026-06-16.jsonl": 1}  # grew 1 -> 3
    pending = job.materials_to_extract(mdir, "2026-06-16", state)
    assert [p.name for p in pending] == ["2026-06-16.jsonl"]


def test_materials_to_extract_empty_when_dir_missing(tmp_path):
    assert job.materials_to_extract(tmp_path / "nope", "2026-06-16", {}) == []
