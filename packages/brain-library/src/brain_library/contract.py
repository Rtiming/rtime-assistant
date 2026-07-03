# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""H M0 库内容合同校验 + 夜巡报告(validity/consistency 两维,只读)。

设计: docs/design/library-lifecycle-maintenance-2026-07.zh-CN.md §四(内容合同)/§五(doctor 指标)。

合同字段(frontmatter,与 SGM/P4 同一体系):
  status: draft|active|needs-review|superseded|archived
  review_after: YYYY-MM-DD
  superseded_by: <brain 相对路径>   (status=superseded 时必填)
  version: 正整数
  source: 来源 URL(硬规则;缺失=warning)

存量宽容原则:字段**缺失**只 warning(3 万+存量渐进补,绝不拒读);字段**存在但非法**
才是 error。报告绝不输出正文——只有路径、字段名、issue code 与截断的元数据值。

与索引器共用同一 frontmatter 解析(_parse_frontmatter)与文件遍历(_walk_index_files),
保证"合同看到的库"和"索引看到的库"口径一致;drift 检查即两者的集合差。
"""

from __future__ import annotations

import os
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .indexer import (
    _parse_frontmatter,
    _read_index_text,
    _relative,
    _strip_frontmatter,
    _walk_index_files,
)

# 与 cli.MAX_TEXT_BYTES 同值;不从 cli 导入(cli 对 contract 是懒导入,保持单向依赖 contract→indexer)
MAX_TEXT_BYTES = 2_000_000

JsonObject = dict[str, Any]

STATUS_VALUES = ("draft", "active", "needs-review", "superseded", "archived")
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
# 正文(去 frontmatter)短于此字符数视为 stub(维基园艺:stub detection)
STUB_BODY_CHARS = 40
DEFAULT_SAMPLE_LIMIT = 20
DEFAULT_MAX_FILES = 200_000


@dataclass(frozen=True)
class ContractIssue:
    """一条合同违规。detail 只放截断的元数据值,永不放正文。"""

    path: str
    code: str
    severity: str  # "error" | "warning"
    field: str = ""
    detail: str = ""


def validate_front(front: dict[str, str], *, body_chars: int) -> list[tuple[str, str, str, str]]:
    """校验单份 frontmatter,返回 (severity, code, field, detail) 列表。

    纯函数:入库(finalize/intake)与编辑(M1+ 写动词)共用,不做任何 IO。
    """
    issues: list[tuple[str, str, str, str]] = []
    status = front.get("status", "")
    if not status:
        issues.append(("warning", "missing_status", "status", ""))
    elif status not in STATUS_VALUES:
        # 2026-07-03 真库基线:库里已有自然长成的status词汇(experimental-draft/filed/
        # verified/raw-extracted-untrusted等471处,多为intake流水线产物)。status是
        # 自由元数据,破坏不了下游 => 非枚举值=warning供渐进迁移,不是error;
        # error只留给结构性违规(悬挂/坏日期/坏版本)。
        issues.append(("warning", "nonstandard_status", "status", status[:80]))
    if status == "superseded" and not front.get("superseded_by"):
        issues.append(("error", "superseded_without_target", "superseded_by", ""))
    review_after = front.get("review_after", "")
    if review_after and not _DATE_RE.match(review_after):
        issues.append(("error", "invalid_review_after", "review_after", review_after[:80]))
    version = front.get("version", "")
    if version:
        try:
            if int(version) <= 0:
                raise ValueError
        except ValueError:
            issues.append(("error", "invalid_version", "version", version[:80]))
    if not front.get("source"):
        issues.append(("warning", "missing_source", "source", ""))
    if body_chars < STUB_BODY_CHARS:
        issues.append(("warning", "stub_body", "", f"body_chars={body_chars}"))
    return issues


def _scan_files(
    root: Path, *, path_prefix: str, max_files: int
) -> tuple[list[Path], bool]:
    files, truncated = _walk_index_files(root, max_files=max_files)
    if path_prefix:
        prefix = path_prefix.strip("/")
        files = [f for f in files if _relative(f, root).startswith(prefix)]
    return files, truncated


def scan_contract(
    root: Path,
    *,
    path_prefix: str = "",
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    """validity + superseded_by 悬挂(consistency 的文件侧):全库只读巡检报告。"""
    root = root.resolve()
    files, truncated = _scan_files(root, path_prefix=path_prefix, max_files=max_files)
    md_files = [f for f in files if f.suffix.lower() == ".md"]
    by_code: dict[str, dict[str, Any]] = {}
    totals = {"error": 0, "warning": 0}
    with_frontmatter = 0
    unreadable = 0

    def _record(issue: ContractIssue) -> None:
        totals[issue.severity] += 1
        bucket = by_code.setdefault(
            issue.code, {"count": 0, "severity": issue.severity, "sample": []}
        )
        bucket["count"] += 1
        if len(bucket["sample"]) < sample_limit:
            entry = issue.path if not issue.detail else f"{issue.path} ({issue.detail})"
            bucket["sample"].append(entry)

    for path in md_files:
        rel = _relative(path, root)
        text, err = _read_index_text(path, max_bytes=MAX_TEXT_BYTES)
        if err:
            unreadable += 1
            _record(ContractIssue(rel, "unreadable", "warning", detail=err))
            continue
        front = _parse_frontmatter(text)
        if front:
            with_frontmatter += 1
        body_chars = len(_strip_frontmatter(text).strip())
        for severity, code, field, detail in validate_front(front, body_chars=body_chars):
            _record(ContractIssue(rel, code, severity, field, detail))
        target = front.get("superseded_by", "")
        if target:
            resolved = (root / target.strip("/")).resolve()
            # 越界指向(../)与不存在的目标同为悬挂
            if not str(resolved).startswith(str(root)) or not resolved.exists():
                _record(
                    ContractIssue(
                        rel, "dangling_superseded_by", "error",
                        "superseded_by", target[:120],
                    )
                )

    return {
        "ok": totals["error"] == 0,
        "root": str(root),
        "path_prefix": path_prefix or None,
        "files_scanned": len(md_files),
        "files_with_frontmatter": with_frontmatter,
        "unreadable": unreadable,
        "truncated": truncated,
        "issues": totals,
        "by_code": dict(sorted(by_code.items(), key=lambda kv: -kv[1]["count"])),
    }


def check_index_drift(
    root: Path,
    index: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    """consistency 的索引侧:磁盘文件集 vs 索引 documents 表的双向差。

    只读;两侧用同一 walker 口径,差集即真漂移(索引器跳过的目录两侧都不算)。
    """
    root = root.resolve()
    files, truncated = _walk_index_files(root, max_files=max_files)
    on_disk = {_relative(f, root) for f in files}
    try:
        connection = sqlite3.connect(f"file:{index}?mode=ro", uri=True)
        try:
            rows = connection.execute("SELECT path FROM documents").fetchall()
        finally:
            connection.close()
    except sqlite3.Error as exc:
        return {"ok": False, "error": f"index_unreadable: {exc}", "index": str(index)}
    in_index = {row[0] for row in rows}
    missing_in_index = sorted(on_disk - in_index)
    missing_on_disk = sorted(in_index - on_disk)
    return {
        "ok": not missing_in_index and not missing_on_disk,
        "index": str(index),
        "on_disk": len(on_disk),
        "in_index": len(in_index),
        "truncated": truncated,
        "missing_in_index": {
            "count": len(missing_in_index),
            "sample": missing_in_index[:sample_limit],
        },
        "missing_on_disk": {
            "count": len(missing_on_disk),
            "sample": missing_on_disk[:sample_limit],
        },
    }


def contract_report(
    root: Path,
    *,
    index: Path | None = None,
    path_prefix: str = "",
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    """夜巡入口:validity 扫描 + (可选)索引 drift。纯只读,报告无正文。"""
    report = scan_contract(
        root, path_prefix=path_prefix, max_files=max_files, sample_limit=sample_limit
    )
    if index is not None:
        drift = check_index_drift(
            root, index, max_files=max_files, sample_limit=sample_limit
        )
        report["index_drift"] = drift
        report["ok"] = bool(report["ok"] and drift.get("ok"))
    else:
        report["index_drift"] = None
    return report
