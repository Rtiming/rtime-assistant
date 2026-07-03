# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-docpack.py"


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_docpack", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _write_json(path: Path, data: dict):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _make_docpack(tmp_path: Path) -> Path:
    docpack = tmp_path / "sample.docpack"
    (docpack / "pages").mkdir(parents=True)
    (docpack / "source").mkdir()
    (docpack / "pages" / "page-0001.png").write_bytes(b"png")
    (docpack / "source" / "original.pdf").write_bytes(b"pdf")
    (docpack / "content.md").write_text("# Sample\n\nPage text.\n", encoding="utf-8")

    sha = "a" * 64
    _write_json(
        docpack / "manifest.json",
        {
            "schema_version": 1,
            "docpack_id": "sample",
            "created_at": "2026-06-10T00:00:00Z",
            "source": {
                "path": "source/original.pdf",
                "sha256": sha,
                "media_type": "application/pdf",
                "size_bytes": 3,
            },
            "display": {
                "pages_dir": "pages",
                "page_count": 1,
            },
            "outputs": {
                "content_md": "content.md",
                "layout_json": "layout.json",
                "verify_json": "verify.json",
                "citations_json": "citations.json",
                "chunks_jsonl": "chunks.jsonl",
            },
            "risks": [],
        },
    )
    _write_json(
        docpack / "layout.json",
        {
            "schema_version": 1,
            "pages": [
                {
                    "page": 1,
                    "width": 100,
                    "height": 100,
                    "blocks": [
                        {
                            "block_id": "block-p1-text",
                            "type": "text",
                            "page": 1,
                            "bbox": [0, 0, 100, 100],
                            "text": "Page text.",
                            "confidence": 1.0,
                            "risks": [],
                        }
                    ],
                }
            ],
        },
    )
    _write_json(
        docpack / "verify.json",
        {
            "schema_version": 1,
            "status": "ok",
            "source_sha256": sha,
            "risks": [],
            "pages": [
                {
                    "page": 1,
                    "image": "pages/page-0001.png",
                    "render_status": "ok",
                    "text_status": "ok",
                    "risks": [],
                }
            ],
        },
    )
    _write_json(
        docpack / "citations.json",
        {
            "schema_version": 1,
            "anchors": [
                {
                    "anchor_id": "p1",
                    "target_type": "page",
                    "source_path": "source/original.pdf",
                    "page": 1,
                    "confidence": 1.0,
                    "risks": [],
                }
            ],
        },
    )
    chunk = {
        "chunk_id": "chunk-p1",
        "kind": "page",
        "text": "Page text.",
        "page_start": 1,
        "page_end": 1,
        "citations": ["p1"],
        "confidence": 1.0,
        "risks": [],
    }
    (docpack / "chunks.jsonl").write_text(json.dumps(chunk) + "\n", encoding="utf-8")
    return docpack


def test_valid_docpack_passes(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)

    errors = validator.validate_docpack(docpack)

    assert errors == []


def test_missing_rendered_page_fails(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)
    (docpack / "pages" / "page-0001.png").unlink()

    errors = validator.validate_docpack(docpack)

    assert any("missing rendered page" in error for error in errors)


def test_unknown_chunk_citation_fails(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)
    chunk = {
        "chunk_id": "chunk-p1",
        "kind": "page",
        "text": "Page text.",
        "page_start": 1,
        "citations": ["missing-anchor"],
        "risks": [],
    }
    (docpack / "chunks.jsonl").write_text(json.dumps(chunk) + "\n", encoding="utf-8")

    errors = validator.validate_docpack(docpack)

    assert any("unknown citation anchor missing-anchor" in error for error in errors)


def test_schema_required_fields_are_enforced(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)
    manifest = json.loads((docpack / "manifest.json").read_text(encoding="utf-8"))
    del manifest["source"]["sha256"]
    _write_json(docpack / "manifest.json", manifest)

    errors = validator.validate_docpack(docpack)

    assert any("manifest.source.sha256" in error for error in errors)


def test_layout_page_mismatch_fails(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)
    layout = json.loads((docpack / "layout.json").read_text(encoding="utf-8"))
    layout["pages"][0]["page"] = 2
    layout["pages"][0]["blocks"][0]["page"] = 2
    _write_json(docpack / "layout.json", layout)

    errors = validator.validate_docpack(docpack)

    assert any("layout.pages" in error for error in errors)


def test_missing_manifest_source_file_fails(tmp_path):
    validator = _load_validator()
    docpack = _make_docpack(tmp_path)
    (docpack / "source" / "original.pdf").unlink()

    errors = validator.validate_docpack(docpack)

    assert any("manifest.source.path" in error for error in errors)
