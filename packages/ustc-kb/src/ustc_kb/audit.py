# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""审计（确定性）：按来源去重 + 校准过的隐私扫描 + 结构校验 + 覆盖统计。比 LLM 自报可靠。"""

import glob
import json
import os
import re

from . import config

_MOBILE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_URLISH = (
    "http",
    "uploadfiles",
    "/static/",
    "/_upload",
    "/upload/",
    ".jpg",
    ".pdf",
    ".doc",
)
_FM_KEYS = ("type:", "institution:", "source:", "topic:")


def _notes():
    return glob.glob(os.path.join(config.NOTES_DIR, "**", "*.md"), recursive=True)


def _read(fp):
    with open(fp, encoding="utf-8", errors="replace") as f:
        return f.read()


def _source_of(text):
    m = re.search(r"^source:\s*(\S+)", text, re.M)
    return m.group(1).strip() if m else ""


def dedup():
    """同一 source URL 只留一篇（留路径最短者）。返回删除数。"""
    bysrc = {}
    for fp in _notes():
        s = _source_of(_read(fp))
        if s:
            bysrc.setdefault(s, []).append(fp)
    removed = 0
    for fps in bysrc.values():
        if len(fps) > 1:
            keep = sorted(fps, key=lambda f: (len(os.path.basename(f)), f))[0]
            for f in fps:
                if f != keep:
                    os.remove(f)
                    removed += 1
    return removed


def privacy_scan():
    """只报真·手机号（排除 URL/上传路径/时间戳里的数字串）。返回命中列表。"""
    hits = []
    for fp in _notes():
        for i, line in enumerate(_read(fp).splitlines(), 1):
            if any(u in line.lower() for u in _URLISH):
                continue
            for m in _MOBILE.finditer(line):
                hits.append(
                    {
                        "file": os.path.relpath(fp, config.NOTES_DIR),
                        "line": i,
                        "value": m.group(),
                    }
                )
    return hits


def structure_scan():
    bad = []
    for fp in _notes():
        if not all(k in _read(fp) for k in _FM_KEYS):
            bad.append(os.path.relpath(fp, config.NOTES_DIR))
    return bad


def coverage():
    rows = []
    for lp in sorted(glob.glob(os.path.join(config.DATA_DIR, "*.jsonl"))):
        with open(lp, encoding="utf-8") as f:
            recs = [json.loads(x) for x in f if x.strip()]
        c = {"dept": os.path.basename(lp)[:-6], "items": len(recs)}
        for k in ("ok", "empty_or_attachment_only", "login_required", "fetch_fail"):
            c[k] = sum(1 for r in recs if r.get("status") == k)
        rows.append(c)
    return rows


def run():
    return {
        "dedup_removed": dedup(),
        "privacy_hits": privacy_scan(),
        "structure_issues": structure_scan(),
        "coverage": coverage(),
    }
