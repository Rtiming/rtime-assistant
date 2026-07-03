# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""就业处 job.ustc.edu.cn 爬虫。

该站是 ahbys 厂商的 JSON-API 站（非思源/WordPress/column），列表与正文都走
https://ustc.ahbys.com/API/Web/index10358.ashx：
  列表 action=contentlist&parentid=1&columnid=<col>&pageindex=N&pagesize=M
       -> Content.Contentclass（<tr> 行，内含 Article.html?...&cid=NNNN 链接 + 日期）
  正文 action=contentinfo&cid=<cid> -> {Title, Content(正文HTML), AddTime, ...}
必须带 XHR 头（X-Requested-With/Origin/Referer），否则返回「系统繁忙」。
正文 HTML 复用 cms.parse_article 解析（含附件/图片），笔记落 orgs/job/。
独立模块（不在 crawl colleges/orgs 的 siyuan 流程里），CLI: ustc-kb crawl-job。
"""

import json
import re
import ssl
import time
import urllib.parse
import urllib.request

from . import cms, config, files, notes

API = "https://ustc.ahbys.com/API/Web/index10358.ashx"
PARENTID = "1"
_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_HDR = {
    "User-Agent": "Mozilla/5.0",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://job.ustc.edu.cn",
    "Referer": "https://job.ustc.edu.cn/",
}
DEPT = {
    "name": "就业处",
    "topic": "orgs/job",
    "contact": "中国科学技术大学学生就业指导中心；地址：安徽省合肥市金寨路96号；"
    "网站：https://job.ustc.edu.cn/",
}


def _api(action, tries=3, **params):
    q = urllib.parse.urlencode({"action": action, "parentid": PARENTID, **params})
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(API + "?" + q, headers=_HDR)
            raw = urllib.request.urlopen(req, timeout=30, context=_CTX).read()
            return json.loads(raw.decode("utf-8", "ignore"))
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(1.2 * (i + 1))
    raise last or RuntimeError("api failed: " + action)


def parse_columns(columnlist):
    """从 Columnlist（<li><a href='javascript:ClassChange(parent,columnid,..)'>名</a>）抽 {columnid: 名}。纯函数。"""
    cols = {}
    for li in columnlist or []:
        m = re.search(r"ClassChange\(\d+,\s*(\d+)[^)]*\)[^>]*>\s*([^<]+)", li)
        if m:
            cols[m.group(1)] = m.group(2).strip()
    return cols


def parse_rows(contentclass):
    """从 Content.Contentclass（<tr> 行）抽 [{cid,title,date}]。纯函数。"""
    out = []
    for row in contentclass or []:
        m = re.search(r"cid=(\d+)[^>]*>\s*([^<]+?)\s*</a>", row)
        if not m:
            continue
        dm = re.search(r"(20\d{2})-(\d{1,2})-(\d{1,2})", row)
        date = (
            "%s-%02d-%02d" % (dm.group(1), int(dm.group(2)), int(dm.group(3)))
            if dm
            else ""
        )
        out.append({"cid": m.group(1), "title": m.group(2).strip(), "date": date})
    return out


def _columns():
    d = _api("contentlist", columnid="1005", pageindex=1, pagesize=1)
    return parse_columns(d.get("Columnlist")) or {"1005": "新闻公告"}


def _list_cids(columnid, limit=None, sleep=0.3):
    first = _api("contentlist", columnid=columnid, pageindex=1, pagesize=20)
    pages = int(first.get("Content", {}).get("PageCount", 1) or 1)
    out, seen = [], set()
    for p in range(1, pages + 1):
        d = first if p == 1 else _api(
            "contentlist", columnid=columnid, pageindex=p, pagesize=20
        )
        for it in parse_rows(d.get("Content", {}).get("Contentclass")):
            if it["cid"] in seen:
                continue
            seen.add(it["cid"])
            out.append(it)
            if limit and len(out) >= limit:
                return out
        time.sleep(sleep)
    return out


def crawl(download_files=True, limit=None, sleep=0.3):
    """抓 job 全部栏目 -> 笔记(orgs/job/) + 附件归档。返回统计。"""
    config.ensure_dirs()
    n_ok = n_files = 0
    for columnid in _columns():
        for it in _list_cids(columnid, limit=limit, sleep=sleep):
            try:
                info = _api("contentinfo", cid=it["cid"])
            except Exception:  # noqa: BLE001
                continue
            body_html = "<html><body><h1>%s</h1>%s</body></html>" % (
                info.get("Title") or it["title"],
                info.get("Content") or "",
            )
            url = (
                "https://job.ustc.edu.cn/Announcement/Article.html"
                "?parentid=%s&columnid=%s&cid=%s" % (PARENTID, columnid, it["cid"])
            )
            art = cms.parse_article(body_html, url)
            art["title"] = info.get("Title") or it["title"]
            art["date"] = it["date"] or (str(info.get("AddTime") or "")[:10])
            dl = (art["attachments"] + art["images"][:10]) if download_files else []
            archived = (
                files.archive("job", DEPT["name"], art["title"], url, dl) if dl else []
            )
            notes.write(DEPT, "job", art, url, archived)
            n_ok += 1
            n_files += sum(1 for a in archived if a.get("status") == "ok")
            time.sleep(sleep)
    return {"dept": "job", "notes": n_ok, "files": n_files}
