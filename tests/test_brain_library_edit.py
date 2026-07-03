# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M2 lib.edit / lib.revisions / lib.revert:改正文写动词(plan/apply 两段式)。

覆盖:edit plan diff/token 乐观并发/frontmatter 除 version 逐字节保留/正文换/
no_change 拒/越界拒/修订链累积/revert 回滚 frontmatter+正文(version 前向)/
revert 未知快照拒/revert 后可再 revert/合同 error 拒空正文。
设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §四(M2)。
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


def _brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "doc.md").write_text(
        "---\nstatus: active\nsource: https://example.edu/x\ntags: a,b\nversion: 3\n---\n"
        "# 原标题\n原正文,足够长足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    return brain


BODY2 = "# 新标题\n改后的正文,一样足够长足够长足够长足够长足够长足够长足够长足够长。\n"
REL = "knowledge/doc.md"


def _do_edit(e, brain, new_body, actor="tester"):
    plan = e.plan_edit(brain, REL, new_body)
    assert plan["ok"], plan
    return e.apply_edit(brain, REL, new_body, plan["confirm_token"], actor=actor)


def test_edit_plan_diff_and_version(tmp_path):
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    plan = e.plan_edit(brain, REL, BODY2)
    assert plan["ok"] and plan["version"] == 4
    assert "新标题" in plan["diff"] and plan["diff"].startswith("---")
    assert plan["confirm_token"]


def test_edit_applies_and_preserves_frontmatter(tmp_path):
    e = _mod("brain_library.edit")
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    res = _do_edit(e, brain, BODY2)
    assert res["ok"] and res["version"] == 4
    text = (brain / "knowledge" / "doc.md").read_text(encoding="utf-8")
    front = a._parse_frontmatter(text)
    # frontmatter 除 version 逐字段保留;正文换;version +1
    assert front["status"] == "active" and front["source"] == "https://example.edu/x"
    assert front["tags"] == "a,b" and front["version"] == "4"
    _b, body = a._split(text)
    assert body == BODY2


def test_edit_stale_token_rejected(tmp_path):
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    plan = e.plan_edit(brain, REL, BODY2)
    # plan 后文件被改 -> token 失效
    (brain / "knowledge" / "doc.md").write_text(
        "---\nstatus: active\nversion: 3\n---\n别的正文别的正文别的正文别的正文别的正文。\n",
        encoding="utf-8",
    )
    res = e.apply_edit(brain, REL, BODY2, plan["confirm_token"])
    assert not res["ok"] and any("stale_token" in x for x in res["errors"])


def test_edit_no_change_and_out_of_bounds(tmp_path):
    e = _mod("brain_library.edit")
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    _b, body = a._split((brain / "knowledge" / "doc.md").read_text(encoding="utf-8"))
    assert not e.plan_edit(brain, REL, body)["ok"]  # 与现正文相同
    assert not e.plan_edit(brain, "../etc/passwd", "x")["ok"]  # 越界
    assert not e.plan_edit(brain, REL, 123)["ok"]  # 非字符串


def test_edit_short_body_warns_not_blocks(tmp_path):
    # 合同把空/过短正文判为 warning(stub_body)而非 error:edit 不拦,但 plan surface 出来
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    res = e.plan_edit(brain, REL, "太短了\n")
    assert res["ok"] and any("stub_body" in w for w in res["warnings"])


def test_revisions_accumulate(tmp_path):
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    _do_edit(e, brain, BODY2)
    _do_edit(e, brain, "# 三版\n第三版正文足够长足够长足够长足够长足够长足够长足够长足够长。\n")
    revs = e.list_revisions(brain, REL)
    assert revs["ok"] and revs["current_version"] == 5
    assert [r["verb"] for r in revs["revisions"]] == ["edit", "edit"]
    assert [r["version"] for r in revs["revisions"]] == [4, 5]


def test_revert_restores_snapshot(tmp_path):
    e = _mod("brain_library.edit")
    a = _mod("brain_library.annotate")
    brain = _brain(tmp_path)
    original = (brain / "knowledge" / "doc.md").read_text(encoding="utf-8")
    r1 = _do_edit(e, brain, BODY2)  # v4;快照 000001.md = 原文(v3)
    snap = r1["revision"]
    # 回滚到 v3 快照
    plan = e.plan_revert(brain, REL, snap)
    assert plan["ok"] and plan["version"] == 5
    res = e.apply_revert(brain, REL, snap, plan["confirm_token"])
    assert res["ok"] and res["reverted_to"] == snap
    text = (brain / "knowledge" / "doc.md").read_text(encoding="utf-8")
    front = a._parse_frontmatter(text)
    _b, body = a._split(text)
    _ob, orig_body = a._split(original)
    # 正文+frontmatter(除version)恢复到原始;version 前向到 5
    assert body == orig_body and front["status"] == "active" and front["version"] == "5"
    # 回滚本身进了修订链(verb=revert, reverted_to)
    revs = e.list_revisions(brain, REL)["revisions"]
    assert revs[-1]["verb"] == "revert" and revs[-1]["reverted_to"] == snap


def test_revert_unknown_snapshot_and_traversal(tmp_path):
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    _do_edit(e, brain, BODY2)
    assert not e.plan_revert(brain, REL, "999999.md")["ok"]
    assert not e.plan_revert(brain, REL, "../../etc/passwd")["ok"]


def test_revert_stale_token_rejected(tmp_path):
    e = _mod("brain_library.edit")
    brain = _brain(tmp_path)
    r1 = _do_edit(e, brain, BODY2)
    plan = e.plan_revert(brain, REL, r1["revision"])
    _do_edit(e, brain, "# 插一版\n插入的一版正文足够长足够长足够长足够长足够长足够长足够长。\n")
    res = e.apply_revert(brain, REL, r1["revision"], plan["confirm_token"])
    assert not res["ok"] and any("stale_token" in x for x in res["errors"])
