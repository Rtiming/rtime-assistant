# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""把文章记录渲染成忠实正文的规范 .md（不硬套九小节，保全部信息）。"""

import os

from . import config


def render(dept, dept_id, art, url, archived):
    """archived: files.archive 的返回（含本地路径），写入"原始文件"小节供助手取用。"""
    fm = [
        "---",
        "type: ustc-procedure",
        "institution: ustc",
        "dept: %s" % dept["name"],
        "dept_id: %s" % dept_id,
        "topic: %s" % dept.get("topic", "procedures"),
        "status: draft",
        "source: %s" % url,
        "published: %s" % (art.get("date") or ""),
        "updated: %s" % config.TODAY,
        "privacy: public",
    ]
    files_ok = [a for a in (archived or []) if a.get("status") == "ok"]
    if files_ok:
        fm.append("files:")
        for a in files_ok:
            fm.append("  - %s" % a["local_path"])
    fm.append("---")

    body = (
        art.get("body_md") or "(正文为空或以附件/图片形式发布，见下方原始文件 / 来源)"
    )
    parts = ["\n".join(fm), "# %s" % (art.get("title") or "（无标题）"), body]

    if files_ok:
        lines = ["## 原始文件（已归档，可直接取用）"]
        for a in files_ok:
            lines.append("- %s — 本地：%s" % (a["filename"], a["local_path"]))
        parts.append("\n".join(lines))

    parts.append("## 部门公开联系方式\n%s" % dept.get("contact", ""))
    src = url + (("  · 发布 " + art["date"]) if art.get("date") else "")
    parts.append("## 来源\n%s" % src)
    return "\n\n".join(parts) + "\n"


def write(dept, dept_id, art, url, archived):
    topic = dept.get("topic", "procedures")
    from .cms import slugify

    d = os.path.join(config.NOTES_DIR, topic)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(
        d, "%s_%s.md" % (dept_id, slugify(art.get("title") or "untitled"))
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(render(dept, dept_id, art, url, archived))
    return path
