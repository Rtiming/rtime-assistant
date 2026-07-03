# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""组装可检索产物：总索引 MOC + 全部门联系总表。读笔记 frontmatter + 站点清单。"""

import glob
import os
import re

from . import config, files, registry, worklog


def _fm(text, key):
    m = re.search(r"^%s:\s*(.*)$" % key, text, re.M)
    return m.group(1).strip() if m else ""


def _title(text, fp):
    m = re.search(r"^#\s+(.*)$", text, re.M)
    return m.group(1).strip() if m else os.path.basename(fp)[:-3]


def _scan_notes():
    out = []
    for fp in glob.glob(os.path.join(config.NOTES_DIR, "**", "*.md"), recursive=True):
        with open(fp, encoding="utf-8", errors="replace") as f:
            t = f.read()
        out.append(
            {
                "path": fp,
                "dept_id": _fm(t, "dept_id"),
                "dept": _fm(t, "dept"),
                "topic": _fm(t, "topic"),
                "title": _title(t, fp),
                "source": _fm(t, "source"),
                "published": _fm(t, "published"),
                "has_files": "\nfiles:" in t,
            }
        )
    return out


def master_index():
    notes = _scan_notes()
    deps = registry.departments()
    by_dept = {}
    for n in notes:
        by_dept.setdefault(n["dept_id"], []).append(n)
    lines = [
        "# 中国科学技术大学校内事务知识库 · 总索引",
        "",
        "> 自动生成。每条链接到笔记，附原始来源与发布日期；原始文件见各笔记『原始文件』节与 files_index。",
        "",
    ]
    for did in registry.dept_ids():
        ns = sorted(by_dept.get(did, []), key=lambda x: (x["topic"], x["title"]))
        if not ns:
            continue
        d = deps[did]
        lines.append("## %s（%s）" % (d["name"], did))
        oc = d.get("contact", "")
        if oc:
            lines.append("联系方式：%s" % oc)
        lines.append("")
        for n in ns:
            fl = "  📎" if n["has_files"] else ""
            dt = ("  · " + n["published"]) if n["published"] else ""
            rel = os.path.relpath(n["path"], config.DATA_ROOT)
            lines.append("- [%s](%s)%s%s" % (n["title"], rel, fl, dt))
        lines.append("")
    path = os.path.join(config.INDEX_DIR, "master-index.md")
    config.ensure_dirs()
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path, len(notes)


def contacts_table():
    deps = registry.departments()
    lines = [
        "# 部处公开联系方式总表",
        "",
        "| 部门 | 电话 | 邮箱/地址等 |",
        "|---|---|---|",
    ]
    for did in registry.dept_ids():
        d = deps[did]
        c = d.get("contact", "").replace("\n", " ")
        ph = ""
        import re as _re

        m = _re.search(r"(0551[-\d、/()]+|6\d{7})", c)
        ph = m.group(1) if m else ""
        lines.append("| %s | %s | %s |" % (d["name"], ph, c[:120]))
    path = os.path.join(config.INDEX_DIR, "contacts.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return path


def run():
    idx, n = master_index()
    con = contacts_table()
    fs = files.stats()
    worklog.log("assemble", {"notes": n, "files_archived": fs["total"]})
    return {"master_index": idx, "contacts": con, "notes": n, "files": fs}
