# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from test_docpack_builder import _write_minimal_pdf


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "select-docpack-samples.py"


def _load_samples_module():
    spec = importlib.util.spec_from_file_location("select_docpack_samples", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["select_docpack_samples"] = module
    spec.loader.exec_module(module)
    return module


def _make_fixture_tree(root: Path):
    (root / "pdfs").mkdir(parents=True)
    (root / "slides").mkdir()
    (root / "office").mkdir()
    (root / "media").mkdir()
    (root / "notes").mkdir()

    _write_minimal_pdf(root / "pdfs" / "text-layer.pdf", "PDF text sample for selection.")
    (root / "pdfs" / "low-text.pdf").write_bytes(
        b"%PDF-1.4\n1 0 obj << /Type /Catalog /Pages 2 0 R >> endobj\n"
        b"2 0 obj << /Type /Pages /Kids [3 0 R] /Count 1 >> endobj\n"
        b"3 0 obj << /Type /Page /Parent 2 0 R /MediaBox [0 0 100 100] >> endobj\n"
        b"trailer << /Root 1 0 R >>\n%%EOF\n"
    )
    for name in ("deck.ppt", "deck.pptx"):
        (root / "slides" / name).write_bytes(b"fixture")
    for name in ("paper.doc", "paper.docx", "sheet.xlsx"):
        (root / "office" / name).write_bytes(b"fixture")
    (root / "media" / "image.png").write_bytes(b"fixture")
    (root / "media" / "animated.gif").write_bytes(b"fixture")
    (root / "notes" / "note.md").write_text("# Note\n", encoding="utf-8")


def test_select_samples_covers_expected_categories(tmp_path):
    samples_mod = _load_samples_module()
    _make_fixture_tree(tmp_path)

    samples = samples_mod.select_samples(tmp_path)
    data = samples_mod._to_json(tmp_path, samples)

    categories = {sample["category"] for sample in data["samples"]}
    assert {
        "pdf_text",
        "pdf_low_text",
        "ppt",
        "pptx",
        "doc",
        "docx",
        "xlsx",
        "image",
        "gif",
        "markdown",
    } <= categories
    assert data["missing_categories"] == []
    assert any(sample["risks"] == ["first_page_zero_text"] for sample in data["samples"])


def test_select_samples_cli_outputs_json(tmp_path, capsys):
    samples_mod = _load_samples_module()
    _make_fixture_tree(tmp_path)

    assert samples_mod.main([str(tmp_path)]) == 0
    captured = capsys.readouterr()
    data = json.loads(captured.out)

    assert data["sample_count"] >= 10
    assert data["root"] == str(tmp_path.resolve())
