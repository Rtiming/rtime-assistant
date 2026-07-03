# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_vault_module():
    spec = importlib.util.spec_from_file_location("rtime_vault_cli", ROOT / "scripts" / "rtime-vault.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_resolve_pdf_reports_original_companion_and_images(tmp_path):
    mod = load_vault_module()
    brain = tmp_path / "brain"
    pdf = brain / "knowledge" / "courses" / "solid-state" / "lesson1.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    pdf.with_suffix(".md").write_text("---\ntitle: Lesson 1\n---\n", encoding="utf-8")
    (pdf.parent / "images" / pdf.stem).mkdir(parents=True)
    (brain / "_indexes").mkdir()
    (brain / "_indexes" / "pdf-manifest.jsonl").write_text(
        json.dumps(
            {
                "brain_path": "knowledge/courses/solid-state/lesson1.pdf",
                "sha256": "abc",
                "citekey": "lesson1",
            }
        )
        + "\n",
        encoding="utf-8",
    )

    payload = mod.resolve_pdf("lesson1.pdf", brain)

    assert payload["match_count"] == 1
    match = payload["matches"][0]
    assert match["original_exists"] is True
    assert match["companion_md_exists"] is True
    assert match["page_image_dir_exists"] is True
    assert match["sha256"] == "abc"


def test_list_filters_derived_entries(tmp_path):
    mod = load_vault_module()
    vault = tmp_path / "vault"
    course = vault / "courses" / "solid-state"
    (course / "images").mkdir(parents=True)
    (course / "lesson1.pdf").write_bytes(b"%PDF")
    (course / "lesson1.md").write_text("companion", encoding="utf-8")
    (course / "README.md").write_text("index", encoding="utf-8")
    (course / "meta.json").write_text("{}", encoding="utf-8")

    payload = mod.list_entries("courses/solid-state", vault)
    names = {entry["name"] for entry in payload["entries"]}

    assert "lesson1.pdf" in names
    assert "README.md" in names
    assert "lesson1.md" not in names
    assert "images" not in names
    assert "meta.json" not in names


def test_uri_generates_obsidian_open_link():
    mod = load_vault_module()

    payload = mod.obsidian_uri("courses/solid-state/lesson1.md", heading="导论", vault_name="brain-notes")

    assert payload["uri"].startswith("obsidian://open?")
    assert "vault=brain-notes" in payload["uri"]
    assert "file=courses%2Fsolid-state%2Flesson1.md" in payload["uri"]
    assert "heading=%E5%AF%BC%E8%AE%BA" in payload["uri"]
