# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M1 lib.annotate:只改 frontmatter 的最小库写动词(写四件套地基)。

设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §六/§七;
规格: docs/specs/spec-h-m1-lib-annotate.zh-CN.md。

四件套:合同校验(contract.validate_front,error 即拒)→ 修订快照(_revisions/ 全量
原文 + chain.jsonl)→ 落盘(原子替换)→ 索引一致(由网关层调 indexer.update_meta_columns)。

两个执行期决策(比规格初稿更保守,已回写规格注):
- **行级编辑而非整块重排**:_parse_frontmatter 只认扁平 `key: value`,嵌套/列表/注释
  它看不见——若按解析结果重排序列化,会静默丢掉多行结构。这里只替换/删除/追加目标
  字段那一行,其余 frontmatter 行(含嵌套块、注释)逐字节不动;目标字段带续行
  (多行列表等)则拒绝(complex_field),绝不产生孤儿续行。
- **version 系统管理**:apply 自动 +1(无则置 1),changes 里显式给 version 一律拒
  ——版本号是修订链的序号,不是可编辑元数据。

正文保证:改动前后 `_strip_frontmatter` 的正文逐字节相等(apply 内自检,违反即
恢复原文并报错)。乐观并发:confirm_token = sha256(path|canonical(changes)|内容sha256),
plan 之后文件内容被谁动过,apply 即 stale_token(绑内容不绑 mtime:粗时间戳内核上
mtime 指纹会漏检同一时间粒内的改动)。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
from pathlib import Path
from typing import Any

from .contract import validate_front
from .indexer import FRONTMATTER_RE, _parse_frontmatter, _strip_frontmatter

JsonObject = dict[str, Any]

# version 不在此列(系统管理);tags 是扁平字符串字段(逗号分隔),复杂列表拒。
ALLOWED_FIELDS = ("status", "review_after", "superseded_by", "source", "tags")


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_changes(changes: dict[str, str]) -> str:
    return json.dumps(changes, ensure_ascii=False, sort_keys=True)


def _token(rel_path: str, changes: dict[str, str], content_sha: str) -> str:
    # 指纹绑文件内容哈希,不绑 mtime_ns:粗时间戳粒度的内核上(如 orangepi CI),
    # plan 后同一时间粒内的改动 mtime 不变,mtime 指纹会漏检(真机 CI 抓到的缺陷)。
    return _sha(f"{rel_path}|{_canonical_changes(changes)}|{content_sha}")[:32]


def _resolve(root: Path, rel_path: str) -> Path | None:
    """root 内解析,越界(../ 等)返回 None。"""
    candidate = (root / rel_path.strip("/")).resolve()
    return candidate if str(candidate).startswith(str(root.resolve()) + os.sep) else None


def _validate_changes(changes: Any) -> tuple[dict[str, str], list[str]]:
    errors: list[str] = []
    clean: dict[str, str] = {}
    if not isinstance(changes, dict) or not changes:
        return {}, ["changes 必须是非空对象 {field: value},value 空串=删除该字段"]
    for key, value in changes.items():
        key = str(key)
        if key == "version":
            errors.append("version 由系统管理(apply 自动 +1),不接受显式修改")
            continue
        if key not in ALLOWED_FIELDS:
            errors.append(f"字段不在可注记集合 {ALLOWED_FIELDS}: {key}")
            continue
        value = str(value)
        if "\n" in value or "\r" in value:
            errors.append(f"字段值不允许换行: {key}")
            continue
        clean[key] = value.strip()
    return clean, errors


def _field_line_index(lines: list[str], key: str) -> int | None:
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if pattern.match(line):
            return i
    return None


def _has_continuation(lines: list[str], idx: int) -> bool:
    """目标字段行后紧跟缩进/列表续行 => 多行结构,拒改(绝不产生孤儿续行)。"""
    for line in lines[idx + 1 :]:
        if not line.strip():
            continue
        return line[0] in " \t" or line.lstrip().startswith("- ")
    return False


def _split(text: str) -> tuple[str | None, str]:
    """(frontmatter 整块含定界符, 正文)。无 frontmatter => (None, 原文)。"""
    match = FRONTMATTER_RE.match(text)
    if not match:
        return None, text
    return text[: match.end()], text[match.end() :]


def _edit_block(
    block: str, changes: dict[str, str], new_version: int
) -> tuple[str | None, list[str]]:
    """对 frontmatter 块做行级编辑;返回 (新块, errors)。"""
    bom = "﻿" if block.startswith("﻿") else ""
    inner = FRONTMATTER_RE.match(block)
    assert inner is not None  # 调用方保证 block 来自 FRONTMATTER_RE
    lines = inner.group(1).splitlines()
    errors: list[str] = []
    pending = dict(changes)
    pending["version"] = str(new_version)
    for key in list(pending):
        idx = _field_line_index(lines, key)
        value = pending[key]
        if idx is None:
            if value:  # 新增;删除不存在的字段=无操作
                lines.append(f"{key}: {value}")
            pending.pop(key)
            continue
        if _has_continuation(lines, idx):
            errors.append(f"字段 {key} 是多行结构(列表/嵌套),annotate 不改复杂字段")
            continue
        if value:
            lines[idx] = f"{key}: {value}"
        else:
            del lines[idx]
        pending.pop(key)
    if errors:
        return None, errors
    return bom + "---\n" + "\n".join(lines) + "\n---\n", []


def _build_new_text(text: str, changes: dict[str, str], new_version: int) -> tuple[str | None, list[str]]:
    block, body = _split(text)
    if block is None:
        bom = "﻿" if text.startswith("﻿") else ""
        body_wo_bom = text[len(bom) :]
        lines = [f"{k}: {v}" for k, v in changes.items() if v]
        lines.append(f"version: {new_version}")
        return bom + "---\n" + "\n".join(lines) + "\n---\n" + body_wo_bom, []
    new_block, errors = _edit_block(block, changes, new_version)
    if errors:
        return None, errors
    return new_block + body, []


def _proposed_front(front: dict[str, str], changes: dict[str, str], new_version: int) -> dict[str, str]:
    out = dict(front)
    for key, value in changes.items():
        if value:
            out[key] = value
        else:
            out.pop(key, None)
    out["version"] = str(new_version)
    return out


def plan_annotate(root: Path, rel_path: str, changes: Any) -> JsonObject:
    """预览:合同校验 + 逐字段 diff + confirm_token。不写任何东西。"""
    root = root.resolve()
    clean, errors = _validate_changes(changes)
    if errors:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": errors}
    target = _resolve(root, rel_path)
    if target is None or not target.is_file():
        return {"ok": False, "op": "plan", "path": rel_path, "errors": ["文件不存在或越界"]}
    text = target.read_text(encoding="utf-8")
    front = _parse_frontmatter(text)
    try:
        current_version = int(front.get("version", "0"))
    except ValueError:
        current_version = 0
    new_version = max(current_version, 0) + 1
    proposed = _proposed_front(front, clean, new_version)
    body_chars = len(_strip_frontmatter(text).strip())
    contract_errors = [
        f"{code}({field}): {detail}" if detail else f"{code}({field})"
        for severity, code, field, detail in validate_front(proposed, body_chars=body_chars)
        if severity == "error"
    ]
    if contract_errors:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": contract_errors}
    # 行级编辑可行性预检(复杂字段在 plan 就拒,不留到 apply)
    new_text, edit_errors = _build_new_text(text, clean, new_version)
    if edit_errors:
        return {"ok": False, "op": "plan", "path": rel_path, "errors": edit_errors}
    assert new_text is not None
    diff = {
        key: {"old": front.get(key), "new": (clean[key] or None)} for key in clean
    }
    diff["version"] = {"old": front.get("version"), "new": str(new_version)}
    return {
        "ok": True,
        "op": "plan",
        "path": rel_path,
        "diff": diff,
        "version": new_version,
        "confirm_token": _token(rel_path, clean, _sha(text)),
    }


def _revision_dir(root: Path, rel_path: str) -> Path:
    digest = _sha(rel_path)
    return root / "_revisions" / digest[:2] / digest


def _write_revision(
    root: Path,
    rel_path: str,
    old_text: str,
    new_text: str,
    *,
    version: int,
    actor: str,
    verb: str,
    extra: dict[str, Any] | None = None,
) -> str:
    """写一条修订:改动前全量原文存 _revisions/<hash>/NNNNNN.md + chain.jsonl 追加一行。

    per-path 统一修订链(annotate/edit/revert 共用),返回 snapshot 文件名。这是"写四件
    套"的第二件(修订快照),从 annotate 抽出供所有内容写动词复用(H M2)。
    """
    rev_dir = _revision_dir(root, rel_path)
    rev_dir.mkdir(parents=True, exist_ok=True)
    chain = rev_dir / "chain.jsonl"
    existing = len(chain.read_text(encoding="utf-8").splitlines()) if chain.exists() else 0
    snapshot_name = f"{existing + 1:06d}.md"
    (rev_dir / snapshot_name).write_text(old_text, encoding="utf-8")
    entry: dict[str, Any] = {
        "version": version,
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "actor": actor,
        "verb": verb,
        "path": rel_path,
        "sha256_before": _sha(old_text),
        "sha256_after": _sha(new_text),
        "snapshot": snapshot_name,
    }
    if extra:
        entry.update(extra)
    with chain.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    return snapshot_name


def read_revisions(root: Path, rel_path: str) -> list[JsonObject]:
    """该路径的修订链(chain.jsonl 逐行);无则空列表。lib.revisions 的数据源。"""
    root = root.resolve()
    chain = _revision_dir(root, rel_path) / "chain.jsonl"
    if not chain.exists():
        return []
    return [json.loads(l) for l in chain.read_text(encoding="utf-8").splitlines() if l.strip()]


def apply_annotate(
    root: Path, rel_path: str, changes: Any, token: str, *, actor: str = "unknown"
) -> JsonObject:
    """落盘:token 校验 → 修订快照 → 行级改写(原子) → 自检(正文字节不变+往返一致)。"""
    root = root.resolve()
    clean, errors = _validate_changes(changes)
    if errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": errors}
    target = _resolve(root, rel_path)
    if target is None or not target.is_file():
        return {"ok": False, "op": "apply", "path": rel_path, "errors": ["文件不存在或越界"]}
    text = target.read_text(encoding="utf-8")
    if token != _token(rel_path, clean, _sha(text)):
        return {
            "ok": False, "op": "apply", "path": rel_path,
            "errors": ["stale_token: 文件在 plan 之后被改动,请重新 plan"],
        }
    front = _parse_frontmatter(text)
    try:
        current_version = int(front.get("version", "0"))
    except ValueError:
        current_version = 0
    new_version = max(current_version, 0) + 1
    proposed = _proposed_front(front, clean, new_version)
    body_before = _strip_frontmatter(text)
    contract_errors = [
        code
        for severity, code, _f, _d in validate_front(
            proposed, body_chars=len(body_before.strip())
        )
        if severity == "error"
    ]
    if contract_errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": contract_errors}
    new_text, edit_errors = _build_new_text(text, clean, new_version)
    if edit_errors:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": edit_errors}
    assert new_text is not None

    # 自检 1:正文逐字节不变;自检 2:往返一致(改动字段解析回来就是目标值)。
    reparsed = _parse_frontmatter(new_text)
    body_after = _strip_frontmatter(new_text)
    for key, value in clean.items():
        if (reparsed.get(key) or "") != value:
            return {
                "ok": False, "op": "apply", "path": rel_path,
                "errors": [f"roundtrip_failed: {key}"],
            }
    if body_after != body_before:
        return {"ok": False, "op": "apply", "path": rel_path, "errors": ["body_changed"]}

    # 修订快照(改动前全量原文)+ chain 台账,然后原子落盘。
    snapshot_name = _write_revision(
        root, rel_path, text, new_text,
        version=new_version, actor=actor, verb="annotate",
        extra={"fields": sorted(clean)},
    )
    tmp = target.with_name(f".{target.name}.annotate.tmp")
    tmp.write_text(new_text, encoding="utf-8")
    tmp.replace(target)
    return {
        "ok": True,
        "op": "apply",
        "path": rel_path,
        "version": new_version,
        "revision": snapshot_name,
        "fields": sorted(clean),
    }
