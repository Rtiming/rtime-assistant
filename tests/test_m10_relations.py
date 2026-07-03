# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = ROOT / "scripts" / "brain-intake"
sys.path.insert(0, str(SCRIPT_DIR))

import m10_relations  # noqa: E402


def _brain_fixture(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    course = brain / "knowledge" / "courses" / "solid-state" / "slides"
    course.mkdir(parents=True)
    (brain / "_indexes").mkdir(parents=True)
    (course / "lesson1.pdf").write_bytes(b"%PDF")
    (course / "lesson1.md").write_text(
        "\n".join(
            [
                "---",
                "title: Lesson 1",
                "citekey: shared2026",
                "---",
                "# Lesson 1",
                "This note links to [[lesson2]] and talks about electron heat capacity.",
                "See @shared2026.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (course / "lesson2.md").write_text(
        "\n".join(
            [
                "---",
                "title: Lesson 2",
                "citekey: shared2026",
                "---",
                "# Lesson 2",
                "Electron heat capacity and Fermi surface density of states appear again.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (course / "lesson3.md").write_text(
        "\n".join(
            [
                "---",
                "title: Lesson 3",
                "---",
                "# Lesson 3",
                "A nearby course handout mentions phonons and heat capacity.",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (brain / "_indexes" / "pdf-manifest.jsonl").write_text(
        json.dumps(
            {
                "brain_path": "knowledge/courses/solid-state/slides/lesson1.pdf",
                "md_path": "knowledge/courses/solid-state/slides/lesson1.md",
                "citekey": "shared2026",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return brain


def test_m10_relations_plan_and_apply_are_idempotent(tmp_path):
    brain = _brain_fixture(tmp_path)
    run_dir = tmp_path / "run"
    state_dir = tmp_path / "state"

    plan = m10_relations.build_plan(brain, run_dir, state_dir, limit=5)
    edges = plan["actions"][0]["edges"]
    rels = {edge["rel"] for edge in edges}

    assert {"wikilink", "manifest-sibling", "same-course", "bm25-topic", "citekey"} <= rels
    assert plan["summary"]["edge_count"] == len(edges)
    assert any(action["action"] == "update_related_section" for action in plan["actions"])

    plan_path = run_dir / "m10-relations-plan.json"
    run_dir.mkdir()
    plan_path.write_text(json.dumps(plan, ensure_ascii=False), encoding="utf-8")
    result1 = m10_relations.apply_plan(plan, plan_path)
    result2 = m10_relations.apply_plan(plan, plan_path)

    relations = brain / "_indexes" / "relations.jsonl"
    lesson1 = brain / "knowledge" / "courses" / "solid-state" / "slides" / "lesson1.md"
    text = lesson1.read_text(encoding="utf-8")
    assert relations.is_file()
    assert "## 相关材料" in text
    assert text.count("## 相关材料") == 1
    assert "needs_review" in text
    assert any(item["action"] == "write_relations_index" for item in result1["applied"])
    assert any(item.get("reason") == "unchanged" for item in result2["skipped"])
    assert (state_dir / "relations-audit.jsonl").is_file()


def test_related_section_replaces_existing_block():
    old = "# Note\n\nBody\n\n## 相关材料\n\n- old\n\n## Next\n\nTail\n"
    new = m10_relations.replace_related_section(
        old,
        m10_relations.related_section(
            [
                {
                    "dst": "knowledge/courses/a.md",
                    "rel": "wikilink",
                    "score": 0.9,
                    "evidence": "[[a]]",
                }
            ]
        ),
    )
    assert "- old" not in new
    assert "[[knowledge/courses/a.md|a]]" in new
    assert "## Next" in new


def _load_vault_module():
    spec = importlib.util.spec_from_file_location("rtime_vault_cli", ROOT / "scripts" / "rtime-vault.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_rtime_vault_related_reads_relation_index(tmp_path):
    mod = _load_vault_module()
    brain = tmp_path / "brain"
    (brain / "_indexes").mkdir(parents=True)
    row = {
        "src": "knowledge/courses/a.md",
        "dst": "knowledge/courses/b.md",
        "rel": "wikilink",
        "evidence": "[[b]]",
        "score": 0.9,
    }
    (brain / "_indexes" / "relations.jsonl").write_text(json.dumps(row) + "\n", encoding="utf-8")

    payload = mod.related_materials("knowledge/courses/a.md", brain, limit=3)

    assert payload["match_count"] == 1
    assert payload["matches"][0]["dst"] == "knowledge/courses/b.md"
