# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M3 lib.move / lib.retire / lib.restore:库维护写动词(移动/重命名 + 软删归档)。

设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §六/§九(M3)。

复用 M1/M2 地基(annotate/edit 的纹理):两段式 plan→confirm_token→apply(token 绑
源文件内容 sha 做乐观并发,plan 后文件变即 stale_token)、``_resolve`` 越界防护、
``_write_revision`` 统一修订链(move/retire/restore 也进链,verb 对应),合同 warning
只 surface 不拦。

- lib.move   改路径/重命名:先跑引用完整性扫描(谁用 [[slug]] / superseded_by 指向它),
             plan 报告受影响引用列表;apply 写修订快照(verb=move)→移动文件→原路径
             留**墓碑**(frontmatter status=moved / moved_to,一句正文说明),旧路径
             lib.read 即读到重定向信息。to_path 已存在=拒(不覆盖)。
- lib.retire 软删:移到 ``_archive/<原相对路径>``(保留目录结构),原路径留墓碑
             (status=retired);修订快照 verb=retire;归档文件完整保留=可恢复。
- lib.restore 把 _archive 里的退役文件恢复回原路径(墓碑须存在且 status=retired)。

索引一致:move/retire 让原路径不再是知识内容(墓碑/归档),索引层由网关 handler 调
``indexer.remove_from_index`` 删旧 path 行;move 的新路径由后续增量重建收录(handler
标 index_rebuild_needed 提示)。此模块只管文件与修订链,不碰索引(与 annotate 分层一致:
索引同步是网关 handler 的第四件套)。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .annotate import (
    _parse_frontmatter,
    _resolve,
    _sha,
    _write_revision,
)
from .indexer import _walk_index_files

JsonObject = dict[str, Any]

# 引用扫描范围:knowledge/ 下的 .md(受影响引用的现实来源;全库扫描太重且 _archive/
# _revisions/_inbox 等非知识区不算引用)。上限防呆(与 contract 同量级)。
_REF_SCAN_MAX_FILES = 200_000
_REF_SCAN_DIR = "knowledge"


def _rel_norm(rel_path: str) -> str:
    return rel_path.strip().replace("\\", "/").strip("/")


def _slug_candidates(rel_path: str) -> list[str]:
    """一个 brain 相对路径可能被 [[...]] 引用的形态:全相对路径、去 .md 的路径、纯
    basename、去 .md 的 basename。Obsidian wikilink 常用最短唯一名(basename),也允许
    全路径,这里都算命中。"""
    rel = _rel_norm(rel_path)
    stem = rel[:-3] if rel.lower().endswith(".md") else rel
    base = rel.rsplit("/", 1)[-1]
    base_stem = stem.rsplit("/", 1)[-1]
    # 去重保序
    seen: dict[str, None] = {}
    for cand in (rel, stem, base, base_stem):
        if cand:
            seen.setdefault(cand, None)
    return list(seen)


def _scan_references(root: Path, rel_path: str, *, exclude_self: bool = True) -> list[JsonObject]:
    """扫 knowledge/ 下 .md,报告哪些文件在 frontmatter/正文里指向 rel_path。

    命中两类:
      - ``superseded_by: <rel_path>``(frontmatter 精确路径指向,归一化比较);
      - ``[[slug]]`` wikilink,slug ∈ _slug_candidates(rel_path)(含 ``[[slug|别名]]``、
        ``[[slug#锚点]]``)。
    最简可靠:逐文件文本扫描 + 轻量解析,不建反向链接索引(维护动词是低频操作)。
    返回 [{path, kinds:[...]}],path 为 brain 相对。"""
    root = root.resolve()
    target = _rel_norm(rel_path)
    slugs = {c.lower() for c in _slug_candidates(rel_path)}
    scan_root = root / _REF_SCAN_DIR
    if not scan_root.is_dir():
        return []
    files, _truncated = _walk_index_files(scan_root, max_files=_REF_SCAN_MAX_FILES)
    hits: list[JsonObject] = []
    for path in files:
        if path.suffix.lower() != ".md":
            continue
        try:
            rel = path.resolve().relative_to(root).as_posix()
        except ValueError:
            continue
        if exclude_self and _rel_norm(rel) == target:
            continue
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        kinds: list[str] = []
        front = _parse_frontmatter(text)
        sb = front.get("superseded_by", "")
        if sb and _rel_norm(sb) == target:
            kinds.append("superseded_by")
        if _has_wikilink(text, slugs):
            kinds.append("wikilink")
        if kinds:
            hits.append({"path": rel, "kinds": kinds})
    hits.sort(key=lambda h: h["path"])
    return hits


def _has_wikilink(text: str, slugs: set[str]) -> bool:
    """text 里是否含 [[slug]]（slug 命中候选集，忽略 |别名 和 #锚点，大小写不敏感）。"""
    idx = 0
    while True:
        start = text.find("[[", idx)
        if start == -1:
            return False
        end = text.find("]]", start + 2)
        if end == -1:
            return False
        inner = text[start + 2 : end]
        idx = end + 2
        # 去掉别名(|)与锚点(#),取链接目标本体
        link = inner.split("|", 1)[0].split("#", 1)[0].strip()
        if link and link.lower() in slugs:
            return True


def _tombstone_text(*, verb: str, target_rel: str | None, note: str) -> str:
    """墓碑文件全文:最小 frontmatter(status + 指向) + 一句正文说明。

    move -> status: moved / moved_to: <to>;retire -> status: retired。version:1。
    正文一句人读的重定向说明。墓碑不进检索(status 非知识,且 retire 墓碑无正文价值),
    但旧路径 lib.read 能读到它拿到重定向。"""
    lines = ["---", f"status: {'moved' if verb == 'move' else 'retired'}"]
    if verb == "move" and target_rel:
        lines.append(f"moved_to: {target_rel}")
    lines.append("version: 1")
    lines.append("---")
    body = note if note.endswith("\n") else note + "\n"
    return "\n".join(lines) + "\n" + body


# --------------------------------------------------------------------------- move
def _load_source(root: Path, rel_path: str, op: str) -> tuple[Path | None, str, JsonObject | None]:
    root = root.resolve()
    src = _resolve(root, rel_path)
    if src is None or not src.is_file():
        return None, "", {"ok": False, "op": op, "path": rel_path, "errors": ["源文件不存在或越界"]}
    return src, src.read_text(encoding="utf-8"), None


def _move_token(from_path: str, to_path: str, content_sha: str) -> str:
    return _sha(f"move|{_rel_norm(from_path)}|{_rel_norm(to_path)}|{content_sha}")[:32]


def _check_dest(root: Path, to_path: str, op: str) -> tuple[Path | None, JsonObject | None]:
    """目标路径:过越界检查 + 不覆盖(已存在即拒)。父目录不存在=允许(apply 时建)。"""
    dest = _resolve(root.resolve(), to_path)
    if dest is None:
        return None, {"ok": False, "op": op, "to": to_path, "errors": ["目标路径越界"]}
    if dest.exists():
        return None, {"ok": False, "op": op, "to": to_path, "errors": ["目标路径已存在,不覆盖"]}
    return dest, None


def plan_move(root: Path, from_path: str, to_path: str) -> JsonObject:
    """预览移动/重命名:引用完整性扫描 + confirm_token。不写。"""
    if _rel_norm(from_path) == _rel_norm(to_path):
        return {"ok": False, "op": "plan", "path": from_path, "errors": ["from 与 to 相同"]}
    src, text, err = _load_source(root, from_path, "plan")
    if err:
        return err
    dest, derr = _check_dest(root, to_path, "plan")
    if derr:
        return derr
    affected = _scan_references(root, from_path)
    return {
        "ok": True,
        "op": "plan",
        "verb": "move",
        "from": _rel_norm(from_path),
        "to": _rel_norm(to_path),
        "affected_refs": affected,
        "affected_ref_count": len(affected),
        "tombstone": _rel_norm(from_path),
        "confirm_token": _move_token(from_path, to_path, _sha(text)),
    }


def apply_move(
    root: Path, from_path: str, to_path: str, token: str, *, actor: str = "unknown"
) -> JsonObject:
    """落盘移动:token 校验 → 修订快照(verb=move) → 移文件到新路径 → 原路径写墓碑。"""
    if _rel_norm(from_path) == _rel_norm(to_path):
        return {"ok": False, "op": "apply", "path": from_path, "errors": ["from 与 to 相同"]}
    root = root.resolve()
    src, text, err = _load_source(root, from_path, "apply")
    if err:
        return err
    dest, derr = _check_dest(root, to_path, "apply")
    if derr:
        return derr
    if token != _move_token(from_path, to_path, _sha(text)):
        return {"ok": False, "op": "apply", "path": from_path,
                "errors": ["stale_token: 文件在 plan 之后被改动,请重新 plan"]}
    assert src is not None and dest is not None
    affected = _scan_references(root, from_path)
    tomb = _tombstone_text(
        verb="move", target_rel=_rel_norm(to_path),
        note=f"此文件已移动到 {_rel_norm(to_path)}(lib.move)。请更新引用。",
    )
    # 修订快照(移动前全量原文)先落——链在,即便后续步骤半途失败也可追溯/恢复。
    snapshot_name = _write_revision(
        root, from_path, text, tomb,
        version=1, actor=actor, verb="move",
        extra={"moved_to": _rel_norm(to_path)},
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    # 移动:先把原文写到新路径(原子替换到位),再把旧路径原子替换成墓碑。
    tmp_dest = dest.with_name(f".{dest.name}.move.tmp")
    tmp_dest.write_text(text, encoding="utf-8")
    tmp_dest.replace(dest)
    tmp_tomb = src.with_name(f".{src.name}.tomb.tmp")
    tmp_tomb.write_text(tomb, encoding="utf-8")
    tmp_tomb.replace(src)
    return {
        "ok": True, "op": "apply", "verb": "move",
        "from": _rel_norm(from_path), "to": _rel_norm(to_path),
        "tombstone": _rel_norm(from_path),
        "affected_refs": affected, "affected_ref_count": len(affected),
        "revision": snapshot_name,
    }


# --------------------------------------------------------------------------- retire
def _archive_rel(rel_path: str) -> str:
    return "_archive/" + _rel_norm(rel_path)


def _retire_token(rel_path: str, content_sha: str) -> str:
    return _sha(f"retire|{_rel_norm(rel_path)}|{content_sha}")[:32]


def plan_retire(root: Path, rel_path: str) -> JsonObject:
    """预览软删:目标归档路径 + 受影响引用 + confirm_token。不写。"""
    src, text, err = _load_source(root, rel_path, "plan")
    if err:
        return err
    archive_rel = _archive_rel(rel_path)
    dest, derr = _check_dest(root, archive_rel, "plan")
    if derr:
        # 归档位已存在(同名退役过):报错让操作者先处理,不覆盖历史归档。
        return {"ok": False, "op": "plan", "path": rel_path,
                "errors": [f"归档目标已存在: {archive_rel}"]}
    affected = _scan_references(root, rel_path)
    return {
        "ok": True, "op": "plan", "verb": "retire",
        "path": _rel_norm(rel_path),
        "archived_to": archive_rel,
        "affected_refs": affected, "affected_ref_count": len(affected),
        "tombstone": _rel_norm(rel_path),
        "confirm_token": _retire_token(rel_path, _sha(text)),
    }


def apply_retire(root: Path, rel_path: str, token: str, *, actor: str = "unknown") -> JsonObject:
    """落盘软删:token 校验 → 修订快照(verb=retire) → 移到 _archive/ → 原路径写墓碑。"""
    root = root.resolve()
    src, text, err = _load_source(root, rel_path, "apply")
    if err:
        return err
    archive_rel = _archive_rel(rel_path)
    dest, derr = _check_dest(root, archive_rel, "apply")
    if derr:
        return {"ok": False, "op": "apply", "path": rel_path,
                "errors": [f"归档目标已存在: {archive_rel}"]}
    if token != _retire_token(rel_path, _sha(text)):
        return {"ok": False, "op": "apply", "path": rel_path,
                "errors": ["stale_token: 文件在 plan 之后被改动,请重新 plan"]}
    assert src is not None and dest is not None
    affected = _scan_references(root, rel_path)
    tomb = _tombstone_text(
        verb="retire", target_rel=None,
        note=f"此文件已退役(软删),归档在 {archive_rel}(lib.retire)。可用 lib.restore 恢复。",
    )
    snapshot_name = _write_revision(
        root, rel_path, text, tomb,
        version=1, actor=actor, verb="retire",
        extra={"archived_to": archive_rel},
    )
    # 归档:整份原文写进 _archive(完整保留=可恢复),原路径原子替换成墓碑。
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp_arch = dest.with_name(f".{dest.name}.retire.tmp")
    tmp_arch.write_text(text, encoding="utf-8")
    tmp_arch.replace(dest)
    tmp_tomb = src.with_name(f".{src.name}.tomb.tmp")
    tmp_tomb.write_text(tomb, encoding="utf-8")
    tmp_tomb.replace(src)
    return {
        "ok": True, "op": "apply", "verb": "retire",
        "path": _rel_norm(rel_path), "archived_to": archive_rel,
        "affected_refs": affected, "affected_ref_count": len(affected),
        "tombstone": _rel_norm(rel_path), "revision": snapshot_name,
    }


# --------------------------------------------------------------------------- restore
def _restore_token(rel_path: str, content_sha: str) -> str:
    return _sha(f"restore|{_rel_norm(rel_path)}|{content_sha}")[:32]


def _load_retired(root: Path, rel_path: str, op: str) -> tuple[Path | None, Path | None, str, JsonObject | None]:
    """恢复的前置:原路径必须是 retire 墓碑(status=retired),归档文件必须在。
    返回 (原路径, 归档路径, 归档原文, err)。"""
    root = root.resolve()
    src = _resolve(root, rel_path)
    if src is None:
        return None, None, "", {"ok": False, "op": op, "path": rel_path, "errors": ["路径越界"]}
    archive = _resolve(root, _archive_rel(rel_path))
    if archive is None or not archive.is_file():
        return None, None, "", {"ok": False, "op": op, "path": rel_path, "errors": ["归档文件不存在,无法恢复"]}
    if src.exists():
        front = _parse_frontmatter(src.read_text(encoding="utf-8"))
        if front.get("status") != "retired":
            return None, None, "", {"ok": False, "op": op, "path": rel_path,
                                    "errors": ["原路径不是退役墓碑(status!=retired),拒绝覆盖"]}
    return src, archive, archive.read_text(encoding="utf-8"), None


def plan_restore(root: Path, rel_path: str) -> JsonObject:
    """预览恢复:把 _archive 里的退役文件放回原路径(原路径须是 retired 墓碑或已不存在)。"""
    src, archive, text, err = _load_retired(root, rel_path, "plan")
    if err:
        return err
    assert archive is not None
    return {
        "ok": True, "op": "plan", "verb": "restore",
        "path": _rel_norm(rel_path),
        "restored_from": _archive_rel(rel_path),
        "confirm_token": _restore_token(rel_path, _sha(text)),
    }


def apply_restore(root: Path, rel_path: str, token: str, *, actor: str = "unknown") -> JsonObject:
    """落盘恢复:token 校验(绑归档文件 sha) → 修订快照(verb=restore) → 归档文件移回原路径。"""
    root = root.resolve()
    src, archive, text, err = _load_retired(root, rel_path, "apply")
    if err:
        return err
    if token != _restore_token(rel_path, _sha(text)):
        return {"ok": False, "op": "apply", "path": rel_path,
                "errors": ["stale_token: 归档文件在 plan 之后被改动,请重新 plan"]}
    assert src is not None and archive is not None
    old_marker = src.read_text(encoding="utf-8") if src.exists() else ""
    snapshot_name = _write_revision(
        root, rel_path, old_marker, text,
        version=1, actor=actor, verb="restore",
        extra={"restored_from": _archive_rel(rel_path)},
    )
    src.parent.mkdir(parents=True, exist_ok=True)
    tmp = src.with_name(f".{src.name}.restore.tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(src)
    # 恢复后归档副本删除(文件已回原位;修订链留有轨迹)。
    try:
        archive.unlink()
    except OSError:
        pass
    return {
        "ok": True, "op": "apply", "verb": "restore",
        "path": _rel_norm(rel_path),
        "restored_from": _archive_rel(rel_path),
        "revision": snapshot_name,
    }
