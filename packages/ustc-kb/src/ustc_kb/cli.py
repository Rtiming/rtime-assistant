# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""命令行入口：ustc-kb <crawl|audit|assemble|find-file|sites|files-stats>。"""

import argparse
import json
import sys

from . import assemble, audit, colleges, crawl, files, job, registry


def main(argv=None):
    ap = argparse.ArgumentParser(
        prog="ustc-kb", description="USTC 校内事务资料抓取/归档/索引"
    )
    sub = ap.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("crawl", help="抓取部门（id 列表，或 all）")
    c.add_argument("depts", nargs="+")
    c.add_argument("--no-files", action="store_true", help="不下载原始附件")
    c.add_argument("--since", help="只抓发布日期 >= 此日期(YYYY-MM-DD)的条目")
    c.add_argument("--limit", type=int, help="每部门最多抓 N 条")
    c.add_argument(
        "--incremental", action="store_true", help="跳过台账里已爬过的URL(增量)"
    )
    c.add_argument(
        "--no-extras",
        action="store_true",
        help="不抓 extra_columns(通知/规章/下载/概况/联系);默认抓全",
    )
    c.add_argument(
        "--workers", type=int, help="单部门内并发抓条目线程数(默认8,I/O密集提速)"
    )

    sub.add_parser("audit", help="去重/隐私/结构/覆盖审计")
    sub.add_parser("assemble", help="生成总索引 + 联系总表")
    sub.add_parser("sites", help="列出站点清单")
    sub.add_parser("files-stats", help="已归档原始文件统计")
    sub.add_parser("clean-files", help="清理装饰图/无意义文件 + 重写文件索引")
    f = sub.add_parser("find-file", help="按名检索已归档原始文件")
    f.add_argument("query")
    sub.add_parser(
        "colleges-discover", help="联网发现各学院站点+栏目，写 colleges.json"
    )
    cj = sub.add_parser("crawl-job", help="抓就业处(ahbys JSON-API 站，独立流程)")
    cj.add_argument("--no-files", action="store_true")
    cj.add_argument("--limit", type=int)

    a = ap.parse_args(argv)

    if a.cmd == "crawl-job":
        st = job.crawl(download_files=not a.no_files, limit=a.limit)
        print(json.dumps(st, ensure_ascii=False))
        return

    if a.cmd == "colleges-discover":
        reg = colleges.build()
        ncol = sum(len(v["columns"]) for v in reg.values())
        ncollege = sum(1 for v in reg.values() if v.get("kind") == "college")
        nadmin = sum(1 for v in reg.values() if v.get("kind") == "admin")
        print(
            "发现单位 %d 个(学院%d + 管理机构%d)，内容栏目共 %d 个 -> colleges.json"
            % (len(reg), ncollege, nadmin, ncol)
        )
        for cid, v in reg.items():
            print(
                "  %-14s %-7s %-18s 栏目%d"
                % (cid, v.get("kind", "?"), v["name"], len(v["columns"]))
            )
        return

    if a.cmd == "crawl":
        ids = []
        for d in a.depts:
            if d == "all":
                ids += registry.dept_ids()
            elif d == "colleges":
                ids += registry.college_ids()
            elif d == "orgs":
                ids += registry.org_ids()
            elif d == "research":
                ids += registry.research_ids()
            elif d == "units":
                ids += registry.unit_ids()
            else:
                ids.append(d)
        for did in ids:
            st = crawl.crawl_dept(
                did,
                download_files=not a.no_files,
                since=a.since,
                limit=a.limit,
                incremental=a.incremental,
                include_extras=not a.no_extras,
                workers=a.workers or crawl.DEFAULT_WORKERS,
            )
            print(json.dumps(st, ensure_ascii=False))
    elif a.cmd == "audit":
        r = audit.run()
        print("去重删除:", r["dedup_removed"])
        print("隐私命中(真手机号):", len(r["privacy_hits"]))
        for h in r["privacy_hits"][:20]:
            print("  ", h)
        print("结构缺失:", len(r["structure_issues"]))
        for c0 in r["coverage"]:
            print("  覆盖", json.dumps(c0, ensure_ascii=False))
    elif a.cmd == "assemble":
        r = assemble.run()
        print(
            json.dumps(
                {k: r[k] for k in ("master_index", "contacts", "notes")},
                ensure_ascii=False,
            )
        )
        print("已归档文件:", r["files"])
    elif a.cmd == "sites":
        for did, d in registry.departments().items():
            print(
                "%-12s %s  %s  栏目%d"
                % (did, d["name"], d["base"], len(d.get("columns", [])))
            )
    elif a.cmd == "files-stats":
        print(json.dumps(files.stats(), ensure_ascii=False))
    elif a.cmd == "clean-files":
        dropped, kept = files.clean()
        print("清理装饰/无意义文件 %d，保留 %d" % (dropped, kept))
    elif a.cmd == "find-file":
        hits = files.find(a.query)
        if not hits:
            print("未找到:", a.query)
            return 0
        for h in hits:
            print("• %s  [%s]  -> %s" % (h["title"], h["dept"], h["local_path"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
