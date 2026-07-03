# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M3 lib.move / lib.retire / lib.restore:库维护写动词(plan/apply 两段式)。

覆盖:move(引用报告/墓碑重定向/越界拒/to 已存在拒/stale_token/新路径落地)+
retire(归档保留/墓碑/可 restore)+ restore(放回原路径)+ 修订链累积(verb=move/
retire/restore)。设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §六/§九(M3)。
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-library" / "src"


def _mod(name: str):
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    return importlib.import_module(name)


DOC = (
    "---\nstatus: active\nsource: https://example.edu/x\nversion: 3\n---\n"
    "# 原标题\n原正文,足够长足够长足够长足够长足够长足够长足够长足够长。\n"
)
REL = "knowledge/dir/doc.md"
DEST = "knowledge/dir/renamed.md"


def _brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge" / "dir").mkdir(parents=True)
    (brain / "knowledge" / "dir" / "doc.md").write_text(DOC, encoding="utf-8")
    # 一个用 wikilink 指向 doc、一个用 superseded_by 指向 doc 的引用文件
    (brain / "knowledge" / "ref-wiki.md").write_text(
        "---\nstatus: active\n---\n见 [[doc]] 与 [[doc#章节|别名]]。\n", encoding="utf-8"
    )
    (brain / "knowledge" / "ref-superseded.md").write_text(
        "---\nstatus: superseded\nsuperseded_by: knowledge/dir/doc.md\n---\n旧文。\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "unrelated.md").write_text(
        "---\nstatus: active\n---\n无关文件,不指向 doc。\n", encoding="utf-8"
    )
    return brain


# --------------------------------------------------------------------------- move
def test_move_plan_reports_references(tmp_path):
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    plan = m.plan_move(brain, REL, DEST)
    assert plan["ok"] and plan["verb"] == "move"
    paths = {r["path"] for r in plan["affected_refs"]}
    assert paths == {"knowledge/ref-wiki.md", "knowledge/ref-superseded.md"}
    kinds = {r["path"]: r["kinds"] for r in plan["affected_refs"]}
    assert "wikilink" in kinds["knowledge/ref-wiki.md"]
    assert "superseded_by" in kinds["knowledge/ref-superseded.md"]
    assert plan["confirm_token"]


def test_move_applies_and_leaves_tombstone(tmp_path):
    m = _mod("brain_library.maintain")
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    plan = m.plan_move(brain, REL, DEST)
    res = m.apply_move(brain, REL, DEST, plan["confirm_token"], actor="tester")
    assert res["ok"] and res["to"] == "knowledge/dir/renamed.md"
    # 新路径拿到原文全量(frontmatter+正文原样)
    moved = (brain / "knowledge" / "dir" / "renamed.md").read_text(encoding="utf-8")
    assert moved == DOC
    # 旧路径变成墓碑重定向(status: moved / moved_to)
    tomb = (brain / "knowledge" / "dir" / "doc.md").read_text(encoding="utf-8")
    front = a._parse_frontmatter(tomb)
    assert front["status"] == "moved" and front["moved_to"] == "knowledge/dir/renamed.md"
    # 修订链有一条移动前快照(verb=move)
    revs = _mod("brain_library.edit").list_revisions(brain, REL)["revisions"]
    assert revs[-1]["verb"] == "move" and revs[-1]["moved_to"] == "knowledge/dir/renamed.md"


def test_move_rejects_existing_dest_and_out_of_bounds(tmp_path):
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    (brain / "knowledge" / "dir" / "renamed.md").write_text("已占用\n", encoding="utf-8")
    assert not m.plan_move(brain, REL, DEST)["ok"]  # to 已存在
    assert not m.plan_move(brain, REL, "../outside.md")["ok"]  # 越界
    assert not m.plan_move(brain, "../etc/passwd", DEST)["ok"]  # from 越界
    assert not m.plan_move(brain, REL, REL)["ok"]  # from==to


def test_move_stale_token_rejected(tmp_path):
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    plan = m.plan_move(brain, REL, DEST)
    (brain / "knowledge" / "dir" / "doc.md").write_text(
        DOC + "\n后来又改了一行。\n", encoding="utf-8"
    )
    res = m.apply_move(brain, REL, DEST, plan["confirm_token"])
    assert not res["ok"] and any("stale_token" in e for e in res["errors"])
    # 目标未被创建(apply 失败即无副作用)
    assert not (brain / "knowledge" / "dir" / "renamed.md").exists()


# --------------------------------------------------------------------------- retire
def test_retire_archives_and_tombstones(tmp_path):
    m = _mod("brain_library.maintain")
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    plan = m.plan_retire(brain, REL)
    assert plan["ok"] and plan["archived_to"] == "_archive/knowledge/dir/doc.md"
    res = m.apply_retire(brain, REL, plan["confirm_token"], actor="tester")
    assert res["ok"]
    # 归档文件完整保留原文
    archived = (brain / "_archive" / "knowledge" / "dir" / "doc.md").read_text(encoding="utf-8")
    assert archived == DOC
    # 原路径是 retired 墓碑
    tomb = (brain / "knowledge" / "dir" / "doc.md").read_text(encoding="utf-8")
    assert a._parse_frontmatter(tomb)["status"] == "retired"
    # 修订链 verb=retire
    revs = _mod("brain_library.edit").list_revisions(brain, REL)["revisions"]
    assert revs[-1]["verb"] == "retire"


def test_retire_then_restore_recovers(tmp_path):
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    rplan = m.plan_retire(brain, REL)
    m.apply_retire(brain, REL, rplan["confirm_token"])
    # 恢复
    plan = m.plan_restore(brain, REL)
    assert plan["ok"] and plan["restored_from"] == "_archive/knowledge/dir/doc.md"
    res = m.apply_restore(brain, REL, plan["confirm_token"], actor="tester")
    assert res["ok"]
    # 原路径恢复原文;归档副本清除
    restored = (brain / "knowledge" / "dir" / "doc.md").read_text(encoding="utf-8")
    assert restored == DOC
    assert not (brain / "_archive" / "knowledge" / "dir" / "doc.md").exists()
    # 修订链累积:retire 后 restore
    verbs = [r["verb"] for r in _mod("brain_library.edit").list_revisions(brain, REL)["revisions"]]
    assert verbs == ["retire", "restore"]


def test_retire_rejects_out_of_bounds_and_stale_token(tmp_path):
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    assert not m.plan_retire(brain, "../etc/passwd")["ok"]
    plan = m.plan_retire(brain, REL)
    (brain / "knowledge" / "dir" / "doc.md").write_text(DOC + "改了\n", encoding="utf-8")
    res = m.apply_retire(brain, REL, plan["confirm_token"])
    assert not res["ok"] and any("stale_token" in e for e in res["errors"])
    assert not (brain / "_archive" / "knowledge" / "dir" / "doc.md").exists()


def test_restore_refuses_when_original_not_a_tombstone(tmp_path):
    """恢复前原路径若是活文件(非 retired 墓碑)则拒,绝不覆盖现有内容。"""
    m = _mod("brain_library.maintain")
    brain = _brain(tmp_path)
    rplan = m.plan_retire(brain, REL)
    m.apply_retire(brain, REL, rplan["confirm_token"])
    # 原路径又被写成活文件(status!=retired)
    (brain / "knowledge" / "dir" / "doc.md").write_text(
        "---\nstatus: active\n---\n新内容占位。\n", encoding="utf-8"
    )
    assert not m.plan_restore(brain, REL)["ok"]


def test_revisions_chain_accumulates_across_verbs(tmp_path):
    """move→(在新路径上)不追;单路径 retire→restore 链在同一 per-path 链上累积。"""
    m = _mod("brain_library.maintain")
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    p1 = m.plan_retire(brain, REL)
    m.apply_retire(brain, REL, p1["confirm_token"])
    p2 = m.plan_restore(brain, REL)
    m.apply_restore(brain, REL, p2["confirm_token"])
    revs = e.list_revisions(brain, REL)["revisions"]
    assert [r["verb"] for r in revs] == ["retire", "restore"]
    assert [r["version"] for r in revs] == [1, 1]
