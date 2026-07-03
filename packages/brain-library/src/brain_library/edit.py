# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M2 lib.edit / lib.revert:改正文的库写动词(在 annotate 的修订地基之上)。

设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §四(M2)。

三个动词,全部两段式(plan→confirm_token→apply),共用 annotate 的 per-path 修订链
(_write_revision / read_revisions)与越界防护(_resolve):

- lib.edit   改正文:plan 传新正文→返回 unified diff + token→apply 落盘。frontmatter
             除 version 外**逐字节保留**(只有正文换、version 系统 +1);正文由调用方
             全量给出(整篇替换,不做行级 merge)。
- lib.revisions  列该路径修订链(纯读,无 token)。
- lib.revert 回滚到某个修订快照:plan 显示 当前→快照 的 diff→apply。快照的
             frontmatter+正文整体恢复,仅 version 前向 +1(前向历史,回滚本身也进链)。

乐观并发:token 绑 (verb|path|目标内容sha|当前文件内容sha),plan 之后文件内容被谁
动过,apply 即 stale_token(与 annotate 同纹理,绑内容不绑 mtime)。

正文合同:apply 前跑 contract.validate_front(frontmatter, body_chars=新正文字数),
error 即拒——空正文/过短等由合同判(与 annotate 共用同一套合同)。
"""

from __future__ import annotations

import difflib
from pathlib import Path
from typing import Any

from .annotate import (
    _edit_block,
    _parse_frontmatter,
    _resolve,
    _sha,
    _split,
    _write_revision,
    read_revisions,
)
from .contract import validate_front

JsonObject = dict[str, Any]


def _current_version(front: dict[str, str]) -> int:
    try:
        return max(int(front.get("version", "0")), 0)
    except ValueError:
        return 0


def _bump_version_keep_frontmatter(text: str, new_body: str, new_version: int) -> tuple[str | None, list[str]]:
    """保留 frontmatter(version 改为 new_version)+ 换成 new_body。无 frontmatter 则
    造一个最小块(仅 version)。返回 (新全文, errors)。"""
    block, _body = _split(text)
    if block is None:
        bom = "﻿" if text.startswith("﻿") else ""
        return bom + "---\nversion: " + str(new_version) + "\n---\n" + new_body, []
    # _edit_block(block, {}, v):只把 version 行改成 v,其余 frontmatter 行逐字节不动。
    new_block, errors = _edit_block(block, {}, new_version)
    if errors:
        return None, errors
    return new_block + new_body, []


def _edit_token(rel_path: str, new_body: str, content_sha: str) -> str:
    return _sha(f"edit|{rel_path}|{_sha(new_body)}|{content_sha}")[:32]


def _unified(old_body: str, new_body: str, rel_path: str) -> str:
    return "".join(
        difflib.unified_diff(
            old_body.splitlines(keepends=True),
            new_body.splitlines(keepends=True),
            fromfile=f"a/{rel_path}",
            tofile=f"b/{rel_path}",
        )
    )


def _load_target(root: Path, rel_path: str, op: str) -> tuple[Path | None, str, JsonObject | None]:
    root = root.resolve()
    target = _resolve(root, rel_path)
    if target is None or not target.is_file():
        return None, "", {"ok": False, "op": op, "path": rel_path, "errors": ["文件不存在或越界"]}
    return target, target.read_text(encoding="utf-8"), None


# --------------------------------------------------------------------------- edit
def plan_edit(root: Path, rel_path: str, new_body: Any) -> JsonObject:
    """预览正文编辑:合同校验 + unified diff + confirm_token。不写。"""
    if not isinstance(new_body, str):
        return {"ok": False, "op": "plan", "path": rel_path, "errors": ["new_body 必须是字符串"]}
    target, text, err = _load_target(root, rel_path, "plan")
    if err:
        return err
    _block, old_body = _split(text)
    front = _parse_frontmatter(text)
    new_version = _current_version(front) + 1
    issues = validate_front(front, body_chars=len(new_body.strip()))
    contract_errors = [f"{code}({field})" if field else code
                       for sev, code, field, _d in issues if sev == "error"]
    if contract_errors:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": contract_errors}
    if new_body == old_body:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": ["no_change: 新正文与现正文相同"]}
    warnings = [f"{code}({field})" if field else code
                for sev, code, field, _d in issues if sev == "warning"]
    return {
        "ok": True,
        "op": "plan",
        "path": rel_path,
        "verb": "edit",
        "version": new_version,
        "diff": _unified(old_body, new_body, rel_path),
        "warnings": warnings,  # 合同 warning(如 stub_body 空/过短正文):不拦,但操作者应看见
        "confirm_token": _edit_token(rel_path, new_body, _sha(text)),
    }


def apply_edit(root: Path, rel_path: str, new_body: Any, token: str, *, actor: str = "unknown") -> JsonObject:
    """落盘正文编辑:token 校验 → 修订快照 → 原子写 → 自检(frontmatter 除 version 不变+正文=new_body)。"""
    if not isinstance(new_body, str):
        return {"ok": False, "op": "apply", "path": rel_path, "errors": ["new_body 必须是字符串"]}
    target, text, err = _load_target(root, rel_path, "apply")
    if err:
        return err
    if token != _edit_token(rel_path, new_body, _sha(text)):
        return {"ok": False, "op": "apply", "path": rel_path,
                "errors": ["stale_token: 文件在 plan 之后被改动,请重新 plan"]}
    front = _parse_frontmatter(text)
    new_version = _current_version(front) + 1
    contract_errors = [
        code for severity, code, _f, _d in validate_front(front, body_chars=len(new_body.strip()))
        if severity == "error"
    ]
    if contract_errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": contract_errors}
    new_text, edit_errors = _bump_version_keep_frontmatter(text, new_body, new_version)
    if edit_errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": edit_errors}
    assert new_text is not None
    # 自检:frontmatter 除 version 逐字段不变;正文 == new_body。
    reparsed = _parse_frontmatter(new_text)
    if str(reparsed.get("version")) != str(new_version):
        return {"ok": False, "op": "apply", "path": rel_path, "errors": ["version_bump_failed"]}
    for key, value in front.items():
        if key == "version":
            continue
        if reparsed.get(key) != value:
            return {"ok": False, "op": "apply", "path": rel_path, "errors": [f"frontmatter_changed: {key}"]}
    _b, body_after = _split(new_text)
    if body_after != new_body:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": ["body_mismatch"]}

    snapshot_name = _write_revision(
        root.resolve(), rel_path, text, new_text,
        version=new_version, actor=actor, verb="edit",
    )
    tmp = target.with_name(f".{target.name}.edit.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(target)
    return {"ok": True, "op": "apply", "path": rel_path, "verb": "edit",
            "version": new_version, "revision": snapshot_name}


# --------------------------------------------------------------------------- revisions
def list_revisions(root: Path, rel_path: str) -> JsonObject:
    """纯读:该路径的修订链 + 当前版本。lib.revisions 的返回。"""
    root = root.resolve()
    target = _resolve(root, rel_path)
    revisions = read_revisions(root, rel_path)
    current_version = None
    if target is not None and target.is_file():
        current_version = _current_version(_parse_frontmatter(target.read_text(encoding="utf-8")))
    return {"ok": True, "op": "revisions", "path": rel_path,
            "current_version": current_version, "revisions": revisions}


# --------------------------------------------------------------------------- revert
def _revert_token(rel_path: str, snapshot: str, content_sha: str) -> str:
    return _sha(f"revert|{rel_path}|{snapshot}|{content_sha}")[:32]


def _snapshot_text(root: Path, rel_path: str, snapshot: str) -> str | None:
    from .annotate import _revision_dir  # 局部:避免扩大顶层依赖面

    snap = _revision_dir(root.resolve(), rel_path) / snapshot
    # snapshot 名来自 chain(NNNNNN.md);仍防越界(不接受路径分隔符)
    if "/" in snapshot or "\\" in snapshot or not snap.is_file():
        return None
    return snap.read_text(encoding="utf-8")


def _revert_new_text(snapshot_text: str, new_version: int) -> tuple[str | None, list[str]]:
    """把快照整体恢复(frontmatter+正文),仅 version 前向 +1。"""
    _block, body = _split(snapshot_text)
    return _bump_version_keep_frontmatter(snapshot_text, body, new_version)


def plan_revert(root: Path, rel_path: str, snapshot: str) -> JsonObject:
    """预览回滚到 snapshot:当前→快照(version 前向)的 diff + token。不写。"""
    target, text, err = _load_target(root, rel_path, "plan")
    if err:
        return err
    snapshot_text = _snapshot_text(root, rel_path, snapshot)
    if snapshot_text is None:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": [f"unknown_snapshot: {snapshot}"]}
    new_version = _current_version(_parse_frontmatter(text)) + 1
    new_text, errors = _revert_new_text(snapshot_text, new_version)
    if errors:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": errors}
    assert new_text is not None
    if new_text == text:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": ["no_change: 目标快照与当前内容一致"]}
    return {
        "ok": True, "op": "plan", "path": rel_path, "verb": "revert",
        "snapshot": snapshot, "version": new_version,
        "diff": _unified(text, new_text, rel_path),
        "confirm_token": _revert_token(rel_path, snapshot, _sha(text)),
    }


def apply_revert(root: Path, rel_path: str, snapshot: str, token: str, *, actor: str = "unknown") -> JsonObject:
    """落盘回滚:token 校验 → 快照读取 → 修订快照(当前) → 原子写。回滚本身进修订链。"""
    target, text, err = _load_target(root, rel_path, "apply")
    if err:
        return err
    if token != _revert_token(rel_path, snapshot, _sha(text)):
        return {"ok": False, "op": "apply", "path": rel_path,
                "errors": ["stale_token: 文件在 plan 之后被改动,请重新 plan"]}
    snapshot_text = _snapshot_text(root, rel_path, snapshot)
    if snapshot_text is None:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": [f"unknown_snapshot: {snapshot}"]}
    new_version = _current_version(_parse_frontmatter(text)) + 1
    new_text, errors = _revert_new_text(snapshot_text, new_version)
    if errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": errors}
    assert new_text is not None
    snapshot_name = _write_revision(
        root.resolve(), rel_path, text, new_text,
        version=new_version, actor=actor, verb="revert",
        extra={"reverted_to": snapshot},
    )
    tmp = target.with_name(f".{target.name}.revert.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(target)
    return {"ok": True, "op": "apply", "path": rel_path, "verb": "revert",
            "version": new_version, "revision": snapshot_name, "reverted_to": snapshot}
