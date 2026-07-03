# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""路径与常量。数据产物默认放在仓库外（避免 git 膨胀），可用环境变量 USTC_KB_DATA 覆盖。"""

import os

# 代码在仓库内；抓取产物（原始HTML/原始文件/笔记/索引/台账）默认在仓库外。
DATA_ROOT = os.path.abspath(
    os.environ.get("USTC_KB_DATA", os.path.expanduser("~/Desktop/ustc-kb-data"))
)

SOURCES_DIR = os.path.join(DATA_ROOT, "sources")  # 原始HTML：sources/<dept>/<slug>.html
FILES_DIR = os.path.join(DATA_ROOT, "files")  # 原始附件：files/<dept>/<name>
NOTES_DIR = os.path.join(
    DATA_ROOT, "notes", "knowledge", "institutions", "ustc"
)  # 笔记，镜像 brain 结构
DATA_DIR = os.path.join(DATA_ROOT, "data")  # 每部门抓取台账：data/<dept>.jsonl
INDEX_DIR = os.path.join(
    DATA_ROOT, "index"
)  # files_index.jsonl / master-index.md / contacts.md
WORKLOG_MD = os.path.join(DATA_ROOT, "WORKLOG.md")
WORKLOG_JSONL = os.path.join(DATA_ROOT, "worklog.jsonl")

TODAY = os.environ.get(
    "USTC_KB_TODAY", "2026-06-20"
)  # 入库日期（脚本环境无 Date.now，显式给）

# brain 规范的 10 个 topic 子目录
TOPICS = [
    "organization",
    "student-affairs",
    "academics",
    "admissions",
    "youth-league",
    "second-classroom",
    "procedures",
    "templates",
    "activities",
    "sources",
]

# 可下载归档的原始文件类型
FILE_EXTS = (".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx", ".zip", ".rar")


def ensure_dirs():
    for d in (SOURCES_DIR, FILES_DIR, NOTES_DIR, DATA_DIR, INDEX_DIR):
        os.makedirs(d, exist_ok=True)
    for t in TOPICS:
        os.makedirs(os.path.join(NOTES_DIR, t), exist_ok=True)
