#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""把 ustc-kb 暂存区(notes/files/index)一键发布进 brain。全程 add-only + 备份。

在 runtime 主机跑（暂存区与 brain 在同机磁盘时，rsync 是本地移动，无跨机传输）。
手工流程固化:笔记/文件 add-only rsync 进 brain，再合并文件索引(改写本地路径前缀、
按 dept_id 替换本管线条目、保留其他管线如教务处通知附件/jw 的条目),备份后写回。
默认不重建索引(派生大缓存),--reindex 才调 rebuild-brain-index.sh。

用法:
  python3 scripts/publish_ustc_to_brain.py \
    --staging /var/lib/rtime-assistant/ustc-kb-data \
    --brain   /mnt/brain [--reindex]
先 `python -m ustc_kb assemble` 生成最新暂存索引,再跑本脚本。
"""
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time

USTC_REL = "knowledge/institutions/ustc"


def rewrite_path(local_path, staging_files, brain_files):
    """暂存文件绝对路径 -> brain sources/files 绝对路径（只换前缀一次）。"""
    if local_path and local_path.startswith(staging_files):
        return brain_files + local_path[len(staging_files):]
    return local_path


def merge_index(staging_lines, brain_lines, staging_files, brain_files):
    """合并文件索引：brain 中 dept_id 不属于本次暂存的条目原样保留（教务处通知附件/jw 等
    其他管线），本次暂存的全部条目(改写 local_path)替换同 dept_id 旧条目。返回记录列表。"""
    staging = [json.loads(x) for x in staging_lines if x.strip()]
    staging_depts = {r.get("dept_id") for r in staging}
    for r in staging:
        r["local_path"] = rewrite_path(r.get("local_path", ""), staging_files, brain_files)
    kept = []
    for x in brain_lines:
        if not x.strip():
            continue
        r = json.loads(x)
        if r.get("dept_id") not in staging_depts:
            kept.append(r)
    return kept + staging


def _rsync(src, dst):
    os.makedirs(dst, exist_ok=True)
    subprocess.run(["rsync", "-rt", "--no-perms", src, dst], check=True)


def main(argv=None):
    ap = argparse.ArgumentParser(description="发布 ustc-kb 暂存区进 brain(add-only)")
    ap.add_argument("--staging", required=True)
    ap.add_argument("--brain", required=True)
    ap.add_argument("--reindex", action="store_true", help="发布后跑 rebuild-brain-index.sh")
    ap.add_argument(
        "--reindex-script",
        default=os.path.join(os.path.dirname(__file__), "rebuild-brain-index.sh"),
    )
    a = ap.parse_args(argv)

    staging_notes = os.path.join(a.staging, "notes", USTC_REL) + os.sep
    brain_ustc = os.path.join(a.brain, USTC_REL) + os.sep
    staging_files_root = os.path.join(a.staging, "files")
    staging_files = staging_files_root + os.sep
    brain_files = os.path.join(a.brain, USTC_REL, "sources", "files") + os.sep

    # 1. 笔记 add-only（含 colleges/orgs/research/procedures… 各 topic 子目录）
    _rsync(staging_notes, brain_ustc)
    # 2. 各 dept 原始文件 add-only
    if os.path.isdir(staging_files_root):
        for dept in sorted(os.listdir(staging_files_root)):
            sdir = os.path.join(staging_files_root, dept)
            if os.path.isdir(sdir):
                _rsync(sdir + os.sep, os.path.join(brain_files, dept) + os.sep)
    # 3. 合并文件索引（备份后写回）
    sidx = os.path.join(a.staging, "index", "files_index.jsonl")
    bidx = os.path.join(a.brain, USTC_REL, "_files_index.jsonl")
    if os.path.exists(sidx):
        s_lines = open(sidx, encoding="utf-8").read().splitlines()
        b_lines = (
            open(bidx, encoding="utf-8").read().splitlines()
            if os.path.exists(bidx) else []
        )
        if b_lines:
            bak = bidx + ".bak-publish-" + time.strftime("%Y%m%d-%H%M%S")
            with open(bak, "w", encoding="utf-8") as f:
                f.write("\n".join(b_lines) + "\n")
        merged = merge_index(s_lines, b_lines, staging_files, brain_files)
        with open(bidx, "w", encoding="utf-8") as f:
            for r in merged:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
        print("files_index: %d brain-kept + %d staging = %d"
              % (len(merged) - sum(1 for x in s_lines if x.strip()),
                 sum(1 for x in s_lines if x.strip()), len(merged)))
    # 4. 重建索引（可选）
    if a.reindex and os.path.exists(a.reindex_script):
        subprocess.run(["bash", a.reindex_script], check=True)
    print("published staging -> brain (add-only)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
