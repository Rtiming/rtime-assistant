# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""抓取编排：枚举栏目条目 -> 抓正文 -> 存原始HTML -> 下载原始附件 -> 写忠实笔记 -> 台账 -> 工作记录。"""

import json
import os
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

from . import cms, config, files, http, notes, registry, worklog

# 单部门内并发抓条目的线程数（条目互相独立：各自 fetch 正文 + 下载附件 + 写笔记）。
# 抓取是 I/O 密集(网络)，并发能大幅提速;可用环境变量 USTC_KB_WORKERS 覆盖。
DEFAULT_WORKERS = int(os.environ.get("USTC_KB_WORKERS", "8"))


def _crawled_urls(dept_id):
    """从台账读已成功处理过的 URL（增量抓取据此跳过）。"""
    p = os.path.join(config.DATA_DIR, dept_id + ".jsonl")
    urls = set()
    if not os.path.exists(p):
        return urls
    with open(p, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if r.get("url") and r.get("status") in ("ok", "empty_or_attachment_only"):
                urls.add(r["url"])
    return urls


def _filter_items(items, crawled=None, since=None, limit=None):
    """纯函数：跳过已爬(crawled)、按发布日期 since 过滤(无日期者保留)、limit 截断。"""
    out = []
    for it in items:
        if crawled and it.get("url") in crawled:
            continue
        if since and it.get("date") and it["date"] < since:
            continue
        out.append(it)
    if limit:
        out = out[: int(limit)]
    return out


def _process_item(it, dept, dept_id, raw_dir, download_files):
    """抓一个条目：取正文→存原始HTML→下载附件→写忠实笔记，返回一条台账。
    各条目写各自的文件(原始HTML/笔记按标题、附件按名+sha去重)，故可多线程并发。"""
    try:
        html, final = http.fetch(it["url"])
    except Exception as e:  # noqa: BLE001
        return {
            "url": it["url"], "title": it["title"],
            "status": "fetch_fail", "reason": str(e)[:120],
        }
    if (
        "passport.ustc.edu.cn" in final
        or "id.ustc.edu.cn/cas" in final
        or "/login" in final
    ):
        return {
            "url": it["url"], "title": it["title"],
            "status": "login_required", "final": final,
        }
    with open(
        os.path.join(raw_dir, cms.slugify(it["title"]) + ".html"),
        "w", encoding="utf-8",
    ) as _f:
        _f.write(html)
    art = cms.parse_article(html, it["url"])
    art["title"] = art["title"] or it["title"]
    art["date"] = art["date"] or it["date"]
    dl = (art["attachments"] + art["images"][:10]) if download_files else []
    archived = (
        files.archive(dept_id, dept["name"], art["title"], it["url"], dl) if dl else []
    )
    notep = notes.write(dept, dept_id, art, it["url"], archived)
    empty = len(art["body_md"]) < 30 and not art["attachments"] and not art["images"]
    return {
        "url": it["url"],
        "title": art["title"],
        "date": art["date"],
        "status": "empty_or_attachment_only" if empty else "ok",
        "files": sum(1 for a in archived if a.get("status") == "ok"),
        "images": len(art["images"]),
        "note": os.path.relpath(notep, config.DATA_ROOT),
    }


def _enum_column(col, topic, sleep):
    """枚举一个栏目（翻页 + 栏目首页结构页捕获），返回条目列表（栏目内去重）。
    各栏目互不相干 → 可多线程并发枚举（枚举是整次爬取的耗时大头：多栏目×翻页×网络）。"""
    col_url, kind = col["url"], col.get("cms", "siyuan")
    # 每个栏目按其自身域名定 base（部分 extra_columns 跨域，如 job/teach）
    col_base = "https://" + urllib.parse.urlparse(col_url).netloc
    out, local_seen = [], set()
    for lp in cms.page_urls(col_url, kind):
        try:
            html, _ = http.fetch(lp)
        except Exception:  # noqa: BLE001
            break
        # 栏目首页本身常是内容页（组织架构/现任领导/概况/部门职责/中心简介），
        # 不只是文章列表——有实质正文(>=200字)或含图(架构图)就补成一条结构笔记。
        if lp == col_url:
            sart = cms.parse_article(html, col_url)
            if len(sart.get("body_md") or "") >= 200 or sart.get("images"):
                local_seen.add(col_url)
                out.append(
                    {
                        "title": col.get("label") or sart.get("title") or "概况",
                        "url": col_url,
                        "date": sart.get("date", ""),
                        "topic": topic,
                    }
                )
        page_items = cms.parse_list(html, col_base, kind)
        fresh = [it for it in page_items if it["url"] not in local_seen]
        if not fresh and lp != col_url:
            break
        for it in fresh:
            it["topic"] = topic
            local_seen.add(it["url"])
            out.append(it)
        if sleep:
            time.sleep(sleep)
    return out


def crawl_dept(
    dept_id,
    download_files=True,
    sleep=0.15,
    since=None,
    limit=None,
    incremental=False,
    include_extras=False,
    workers=DEFAULT_WORKERS,
):
    dept = registry.dept(dept_id)
    if not dept:
        raise SystemExit("未知部门: %s" % dept_id)
    config.ensure_dirs()
    raw_dir = os.path.join(config.SOURCES_DIR, dept_id)
    os.makedirs(raw_dir, exist_ok=True)

    # 1. 枚举条目（多栏目 + 翻页 + 去重）。include_extras 时把 extra_columns
    #    （通知/规章制度/文档下载/联系/概况）一并纳入，沿用本部门主栏目的 cms 类型。
    cols = list(dept.get("columns", []))
    if include_extras:
        primary_cms = (dept.get("columns") or [{}])[0].get("cms", "siyuan")
        for label, url in (dept.get("extra_columns") or {}).items():
            cols.append({"url": url, "cms": primary_cms, "label": label})
    topic = dept.get("topic", "procedures")
    n_workers = max(1, int(workers or 1))
    if n_workers == 1 or len(cols) <= 1:
        col_lists = [_enum_column(c, topic, sleep) for c in cols]
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            col_lists = list(ex.map(lambda c: _enum_column(c, topic, sleep), cols))
    # 合并各栏目结果 + 跨栏目去重（保持出现顺序）
    items, seen = [], set()
    for lst in col_lists:
        for it in lst:
            if it["url"] in seen:
                continue
            seen.add(it["url"])
            items.append(it)

    # 1b. 增量/范围过滤：跳过已爬、按 since 截日期、limit 限量
    enumerated = len(items)
    crawled = _crawled_urls(dept_id) if incremental else None
    items = _filter_items(items, crawled, since, limit)
    skipped = enumerated - len(items)

    # 2. 抓正文 + 归档原件 + 写笔记 + 台账（条目级并发：I/O 密集，附件下载是大头）
    n_workers = max(1, int(workers or 1))
    if n_workers == 1:
        ledger = [
            _process_item(it, dept, dept_id, raw_dir, download_files) for it in items
        ]
    else:
        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            ledger = list(
                ex.map(
                    lambda it: _process_item(it, dept, dept_id, raw_dir, download_files),
                    items,
                )
            )

    lp = os.path.join(config.DATA_DIR, dept_id + ".jsonl")
    mode = "a" if (incremental and os.path.exists(lp)) else "w"  # 增量追加，全量覆盖
    with open(lp, mode, encoding="utf-8") as f:
        for r in ledger:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")

    st = {
        "dept": dept_id,
        "enumerated": enumerated,
        "skipped": skipped,
        "items": len(items),
        "ok": sum(1 for r in ledger if r["status"] == "ok"),
        "empty": sum(1 for r in ledger if r["status"] == "empty_or_attachment_only"),
        "login": sum(1 for r in ledger if r["status"] == "login_required"),
        "fail": sum(1 for r in ledger if r["status"] == "fetch_fail"),
        "files": sum(r.get("files", 0) for r in ledger),
    }
    worklog.log("crawl", st)
    return st
