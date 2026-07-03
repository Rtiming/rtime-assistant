# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M1 lib.annotate:frontmatter-only 写动词(plan/apply 两段式)。

覆盖:plan diff/合同 error 拒/非法字段拒/version 显式改拒/token 乐观并发/
正文字节不变/修订链+chain 可回放/version 自增/空串删字段/多行结构拒/无 frontmatter。
设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §六/§七;spec-h-m1。
"""

from __future__ import annotations

import importlib
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-library" / "src"


def _mod(name: str):
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module(name)


def _brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "doc.md").write_text(
        "---\nstatus: draft\nsource: https://example.edu/x\nversion: 2\n---\n"
        "# 标题\n这是正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    return brain


def _apply(a, brain, rel, changes, actor="tester"):
    plan = a.plan_annotate(brain, rel, changes)
    assert plan["ok"], plan
    return a.apply_annotate(brain, rel, changes, plan["confirm_token"], actor=actor)


def test_plan_diff_and_token(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    plan = a.plan_annotate(brain, "knowledge/doc.md", {"status": "active"})
    assert plan["ok"]
    assert plan["diff"]["status"] == {"old": "draft", "new": "active"}
    assert plan["diff"]["version"] == {"old": "2", "new": "3"}
    assert plan["version"] == 3
    assert len(plan["confirm_token"]) == 32


def test_apply_body_bytes_unchanged_and_frontmatter_updated(tmp_path):
    a = _mod("brain_library.annotate")
    indexer = _mod("brain_library.indexer")
    brain = _brain(tmp_path)
    doc = brain / "knowledge" / "doc.md"
    body_before = indexer._strip_frontmatter(doc.read_text(encoding="utf-8"))
    res = _apply(a, brain, "knowledge/doc.md", {"status": "active", "review_after": "2026-12-01"})
    assert res["ok"] and res["version"] == 3
    text = doc.read_text(encoding="utf-8")
    front = indexer._parse_frontmatter(text)
    assert front["status"] == "active"
    assert front["review_after"] == "2026-12-01"
    assert front["version"] == "3"
    assert indexer._strip_frontmatter(text) == body_before  # 正文逐字节不变


def test_revision_chain_and_replay(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    original = (brain / "knowledge" / "doc.md").read_text(encoding="utf-8")
    _apply(a, brain, "knowledge/doc.md", {"status": "active"})
    _apply(a, brain, "knowledge/doc.md", {"status": "needs-review"})
    digest = a._sha("knowledge/doc.md")
    rev_dir = brain / "_revisions" / digest[:2] / digest
    snaps = sorted(rev_dir.glob("*.md"))
    assert len(snaps) == 2
    # 第一个快照 = 最初的原文(可回放到任意版本)
    assert snaps[0].read_text(encoding="utf-8") == original
    chain = [json.loads(x) for x in (rev_dir / "chain.jsonl").read_text().splitlines()]
    assert [c["version"] for c in chain] == [3, 4]
    assert chain[0]["verb"] == "annotate" and chain[0]["actor"] == "tester"


def test_empty_string_deletes_field(tmp_path):
    a = _mod("brain_library.annotate")
    indexer = _mod("brain_library.indexer")
    brain = _brain(tmp_path)
    res = _apply(a, brain, "knowledge/doc.md", {"source": ""})
    assert res["ok"]
    front = indexer._parse_frontmatter((brain / "knowledge" / "doc.md").read_text())
    assert "source" not in front


def test_contract_error_rejected_in_plan(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    # status=superseded 而无 superseded_by => 合同 error
    plan = a.plan_annotate(brain, "knowledge/doc.md", {"status": "superseded"})
    assert not plan["ok"]
    assert any("superseded_without_target" in e for e in plan["errors"])


def test_illegal_field_and_version_rejected(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    assert not a.plan_annotate(brain, "knowledge/doc.md", {"title": "x"})["ok"]
    assert not a.plan_annotate(brain, "knowledge/doc.md", {"version": "9"})["ok"]
    assert not a.plan_annotate(brain, "knowledge/doc.md", {})["ok"]


def test_stale_token_rejected(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    doc = brain / "knowledge" / "doc.md"
    plan = a.plan_annotate(brain, "knowledge/doc.md", {"status": "active"})
    # plan 之后文件被改动 => token 失效
    doc.write_text(doc.read_text(encoding="utf-8") + "\n补一行\n", encoding="utf-8")
    res = a.apply_annotate(brain, "knowledge/doc.md", {"status": "active"}, plan["confirm_token"])
    assert not res["ok"] and any("stale_token" in e for e in res["errors"])


def test_multiline_field_rejected(tmp_path):
    a = _mod("brain_library.annotate")
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    # tags 是多行 YAML 列表 => 复杂字段,annotate 拒改(不产生孤儿续行)
    (brain / "knowledge" / "m.md").write_text(
        "---\nstatus: draft\ntags:\n  - a\n  - b\n---\n正文正文正文正文正文正文正文正文正文正文。\n",
        encoding="utf-8",
    )
    plan = a.plan_annotate(brain, "knowledge/m.md", {"tags": "x,y"})
    assert not plan["ok"]
    assert any("多行结构" in e for e in plan["errors"])


def test_no_frontmatter_file_gets_block(tmp_path):
    a = _mod("brain_library.annotate")
    indexer = _mod("brain_library.indexer")
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    doc = brain / "knowledge" / "bare.md"
    doc.write_text("# 无frontmatter\n正文正文正文正文正文正文正文正文正文正文正文正文。\n", encoding="utf-8")
    res = _apply(a, brain, "knowledge/bare.md", {"status": "active", "source": "https://x"})
    assert res["ok"] and res["version"] == 1
    text = doc.read_text(encoding="utf-8")
    front = indexer._parse_frontmatter(text)
    assert front["status"] == "active" and front["version"] == "1"
    assert text.startswith("---\n")


def test_out_of_scope_path_rejected(tmp_path):
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    assert not a.plan_annotate(brain, "../escape.md", {"status": "active"})["ok"]
    assert not a.plan_annotate(brain, "knowledge/nope.md", {"status": "active"})["ok"]
