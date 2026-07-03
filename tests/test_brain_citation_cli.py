# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-citation" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("brain_citation.cli")


def _make_brain_fixture(root: Path, *, include_missing_bib: bool = False) -> Path:
    brain = root / "brain"
    (brain / ".obsidian").mkdir(parents=True)
    (brain / "_indexes").mkdir(parents=True)
    (brain / "knowledge" / "papers").mkdir(parents=True)
    (brain / "CLAUDE.md").write_text("brain guidance\n", encoding="utf-8")
    keys = "[@smith2024] @chen2025"
    if include_missing_bib:
        keys += " @missing2026"
    (brain / "knowledge" / "note.md").write_text(
        f"# Literature\n[[Concept|concept]] {keys} zotero://select/items/ABC123\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "papers" / "refs.bib").write_text(
        "@article{smith2024,title={Example}}\n"
        "@book{chen2025,title={Second}}\n"
        "@misc{unused2020,title={Unused}}\n",
        encoding="utf-8",
    )
    (brain / "_indexes" / "pdf-manifest.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "pdf-manifest-v1",
                "sha256": "abc123",
                "canonical": True,
                "brain_path": "knowledge/papers/paper.pdf",
                "attachment_mode": "canonical-linked",
                "mobile_cache": False,
                "zotero_item_key": "ITEM123",
                "zotero_linked_attachment_key": "ATTACH123",
                "citekey": "smith2024",
                "obsidian_note": "论文/smith2024.md",
            }
        )
        + "\n"
        + json.dumps(
            {
                "schema_version": "pdf-manifest-v1",
                "canonical": False,
                "canonical_sha256": "abc123",
                "canonical_brain_path": "knowledge/papers/paper.pdf",
                "attachment_mode": "stored-mobile-cache",
                "mobile_cache": True,
                "zotero_item_key": "ITEM123",
                "zotero_stored_attachment_key": "STORE123",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "papers" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    docpack = brain / "knowledge" / "lesson.docpack"
    docpack.mkdir()
    (docpack / "citations.json").write_text(
        json.dumps({"anchors": [{"anchor_id": "p1"}, {"anchor_id": "p2"}]}),
        encoding="utf-8",
    )
    return brain


def test_doctor_reports_repo_and_brain_root(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["--repo-root", str(ROOT), "doctor", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["checks"]["brain_root"] == "ok"
    assert data["checks"]["obsidian_config"] == "ok"
    assert data["checks"]["repo_package"] == "ok"


def test_scan_reports_obsidian_zotero_crosswalk_and_docpacks(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path, include_missing_bib=True)

    assert cli.main(["scan", str(brain), "--sample-limit", "5"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["obsidian"]["vault_config_exists"] is True
    assert data["obsidian"]["wikilink_count"] == 1
    assert data["zotero"]["bib_file_count"] == 1
    assert data["zotero"]["bib_entry_count"] == 3
    assert data["zotero"]["citation_key_occurrences"] == 3
    assert data["zotero"]["zotero_uri_count"] == 1
    assert data["crosswalk"]["keys_with_bib_entries"] == ["chen2025", "smith2024"]
    assert data["crosswalk"]["missing_bib_keys"] == ["missing2026"]
    assert data["crosswalk"]["unused_bib_keys"] == ["unused2020"]
    assert data["docpacks"]["citation_file_count"] == 1
    assert data["docpacks"]["anchor_count"] == 2
    assert data["pdf_manifest"]["exists"] is True
    assert data["pdf_manifest"]["valid_entries"] == 2
    assert data["pdf_manifest"]["attachment_modes"] == {
        "canonical-linked": 1,
        "stored-mobile-cache": 1,
    }
    assert data["pdf_manifest"]["zotero_item_key_count"] == 2
    assert data["pdf_manifest"]["stored_attachment_key_count"] == 1
    assert data["precision"]["write_enabled"] is False
    assert data["precision"]["pdf_manifest_crosswalk"] is True
    assert "missing_bib_entries" in data["risks"]


def test_panel_promotes_missing_bib_key_to_review_risk(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path, include_missing_bib=True)

    assert cli.main(["panel", str(brain), "--sample-limit", "5"]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert data["risks"] == ["missing_bib_entries"]
    assert data["panels"]["crosswalk"]["missing_bib_key_count"] == 1
    assert data["panels"]["pdf_manifest"]["valid_entries"] == 2


def test_panel_without_missing_bib_key_is_ok(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["panel", str(brain), "--sample-limit", "5"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["risks"] == []
    assert data["panels"]["crosswalk"]["missing_bib_key_count"] == 0


def test_scan_rejects_missing_root(tmp_path, capfd):
    cli = _load_cli()

    assert cli.main(["scan", str(tmp_path / "missing")]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert data["errors"] == ["root is not a directory"]
