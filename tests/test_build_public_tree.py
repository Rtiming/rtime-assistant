# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""S4 发布白名单器:选文件/扫描/豁免的单测 + 对真实 manifest 的产树-零命中门。

这是安全门,必须自证有效:构造带敏感物的假树验证扫描确实抓、验证 exclude/allowlist
确实生效;再对真实仓库跑一遍产树+扫描,断言 builtin 命中为 0(回归:任何新提交引入
owner id/群号/密钥形状到白名单内路径,这条会红)。
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]
_spec = importlib.util.spec_from_file_location("build_public_tree", REPO / "scripts" / "build-public-tree.py")
bpt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(bpt)


def _has_git_tree() -> bool:
    try:
        bpt.tracked_files()
        return True
    except bpt.NotAGitRepo:
        return False


needs_git = pytest.mark.skipif(
    not _has_git_tree(),
    reason="需要 git 工作树(裸仓库 checkout / CI 无 .git 时跳过)",
)


def test_scan_catches_known_shapes(tmp_path):
    (tmp_path / "a.py").write_text("uid = '2229098829'\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("key: sk-" + "A" * 25 + "\n", encoding="utf-8")
    (tmp_path / "c.txt").write_text("nothing sensitive here\n", encoding="utf-8")
    hits = bpt.scan_tree(tmp_path)
    files = {h["file"] for h in hits}
    assert "a.py" in files and "b.md" in files and "c.txt" not in files


def test_scan_allowlist_exempts_vendored(tmp_path):
    # allowlist 是 (前缀, 模式);vendored 树内的 admin@123 被豁免,树外不豁免
    vend = tmp_path / "tools" / "chat-intake" / "wechat-mp-rss"
    vend.mkdir(parents=True)
    (vend / "x.md").write_text("PASSWORD=admin@123\n", encoding="utf-8")
    (tmp_path / "y.md").write_text("PASSWORD=admin@123\n", encoding="utf-8")
    hits = bpt.scan_tree(tmp_path)
    files = {h["file"] for h in hits}
    assert "y.md" in files  # 树外不豁免
    assert "tools/chat-intake/wechat-mp-rss/x.md" not in files  # vendored 豁免


@needs_git
def test_selected_files_respects_exclude_and_block():
    manifest = {
        "include": ["packages/", "profiles/"],
        "exclude_always": ["profiles/owner/"],
        "include_blocked": [{"path": "deploy/systemd/"}],
    }
    picked, dropped = bpt.selected_files(manifest)
    assert all(not p.startswith("profiles/owner/") for p in picked)
    assert all(not p.startswith("deploy/systemd/") for p in picked)
    # profiles/owner 若被 git 跟踪,应出现在 dropped(命中 include 但被 exclude)
    assert all(p.startswith(("packages/", "profiles/")) for p in picked)


@needs_git
def test_real_manifest_produces_clean_tree(tmp_path):
    """真实 manifest 产树 + 内置扫描零命中(发布门回归)。"""
    manifest = bpt.load_manifest()
    picked, _ = bpt.selected_files(manifest)
    assert len(picked) > 500  # 白名单不该意外坍缩
    out = tmp_path / "tree"
    bpt.build_tree(picked, out)
    hits = bpt.scan_tree(out)
    assert hits == [], [h["file"] + ":" + h["pattern"] for h in hits[:20]]
    # excluded 真实不在树里
    for leaked in ("profiles/owner", "tools/chat-intake/chat-mcp"):
        assert not (out / leaked).exists()
