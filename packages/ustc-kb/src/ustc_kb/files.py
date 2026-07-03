# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""原始文件归档 + 可检索索引。
把页面里的原始附件（审批表/通知PDF/表格等）下载到 DATA_ROOT/files，并登记到 files_index.jsonl，
让 rtime 助手能按名（如"无犯罪记录证明审批表"）检索到本地文件路径并发送给用户。"""

import json
import os
import re
import threading

from . import config, http

# 守护 files_index.jsonl 追加：crawl 用线程池并发归档时，多个条目可能同时写索引，
# 不加锁会交错/损坏行。每次写一条短记录，锁竞争可忽略。
_INDEX_LOCK = threading.Lock()

# 内容文件白名单（表格/文档/PDF/有意义的内容图）
_CONTENT_EXT = (
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".ppt",
    ".pptx",
    ".zip",
    ".rar",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".bmp",
    ".svg",
)
_IMG_EXT = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".svg")
# 装饰图常见命名
_DECOR = re.compile(
    r"(logo|banner|icon|head|foot|bottom|top|nav|btn|button|qrcode|ewm|weixin|"
    r"wechat|share|blank|spacer|line\b|dot|arrow|more|search|bg\b|back\b)",
    re.I,
)
_IMG_MIN = 12000  # 小于此字节的图多为图标/装饰


def is_decorative(local_path, size):
    """装饰/无意义文件判定：无扩展名/非内容类型、装饰命名的图、过小的图。表格/PDF一律保留。"""
    name = os.path.basename(local_path)
    ext = os.path.splitext(name)[1].lower()
    if ext not in _CONTENT_EXT:
        return True
    if ext in _IMG_EXT and (_DECOR.search(name) or (size or 0) < _IMG_MIN):
        return True
    return False


def _index_path():
    return os.path.join(config.INDEX_DIR, "files_index.jsonl")


def archive(dept_id, dept_name, title, source_url, file_urls):
    """下载 file_urls 到 files/<dept>/，登记索引。返回已归档记录列表（含本地路径）。"""
    if not file_urls:
        return []
    config.ensure_dirs()
    dest = os.path.join(config.FILES_DIR, dept_id)
    out = []
    for u in file_urls:
        res = http.download(u, dest)
        if not res:
            out.append({"url": u, "status": "download_fail"})
            continue
        path, size, sha = res
        if is_decorative(path, size):
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        ext = os.path.splitext(path)[1].lower().lstrip(".")
        rec = {
            "title": title,
            "filename": os.path.basename(path),
            "dept_id": dept_id,
            "dept": dept_name,
            "source_page": source_url,
            "file_url": u,
            "local_path": path,
            "ext": ext,
            "size": size,
            "sha256": sha,
            "status": "ok",
        }
        out.append(rec)
        with _INDEX_LOCK, open(_index_path(), "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    return out


def _load_index():
    p = _index_path()
    if not os.path.exists(p):
        return []
    recs, seen = [], set()
    with open(p, encoding="utf-8") as f:
        lines = f.readlines()
    for line in lines:
        line = line.strip()
        if not line:
            continue
        r = json.loads(line)
        key = (r.get("sha256"), r.get("local_path"))
        if key in seen:
            continue
        seen.add(key)
        recs.append(r)
    return recs


def find(query, limit=10):
    """按 标题/文件名/部门 模糊检索已归档文件。供助手取用。"""
    q = str(query).lower()
    hits = []
    for r in _load_index():
        hay = " ".join(
            str(r.get(k, "")) for k in ("title", "filename", "dept", "dept_id")
        ).lower()
        if all(tok in hay for tok in q.split()):
            hits.append(r)
    return hits[:limit]


def clean():
    """清理已归档的装饰/无意义文件：删文件 + 重写 files_index。返回 (删除数, 保留数)。"""
    recs = _load_index()
    keep, dropped = [], 0
    for r in recs:
        lp = r.get("local_path", "")
        if r.get("status") != "ok" or is_decorative(lp, r.get("size", 0)):
            if os.path.exists(lp):
                try:
                    os.remove(lp)
                except OSError:
                    pass
            dropped += 1
            continue
        keep.append(r)
    with open(_index_path(), "w", encoding="utf-8") as f:
        for r in keep:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
    return dropped, len(keep)


def stats():
    recs = _load_index()
    by_dept = {}
    for r in recs:
        by_dept[r.get("dept_id", "?")] = by_dept.get(r.get("dept_id", "?"), 0) + 1
    return {"total": len(recs), "by_dept": by_dept}
