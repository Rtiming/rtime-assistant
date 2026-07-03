# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# -*- coding: utf-8 -*-
"""ustc-kb 离线单测：确定性 audit（隐私正则/去重/结构）+ cms 解析（list/article/slugify）。
全部用内联合成 HTML，无网络。重点钉死 privacy_scan 的"手机号 vs URL内数字"校准。"""

import os
import sys

sys.path.insert(
    0, os.path.join(os.path.dirname(__file__), "..", "packages", "ustc-kb", "src")
)

from ustc_kb import audit, cms, colleges, config, crawl, job  # noqa: E402


# ---------------- cms.slugify ----------------
def test_slugify_strips_illegal_keeps_chinese():
    assert cms.slugify("中英文成绩单打印/公证") == "中英文成绩单打印公证"
    assert cms.slugify('a:b*c?"d<e>f|g') == "abcdefg"


def test_slugify_empty_and_long():
    assert cms.slugify("") == "untitled"
    assert cms.slugify("   ") == "untitled"
    assert len(cms.slugify("字" * 200)) == 80


# ---------------- cms.parse_list (siyuan) ----------------
LIST_HTML = """
<ul class="news_list">
  <li><a href="/2023/0615/c5656a605980/page.htm" title="无犯罪记录证明介绍信开具流程">无犯罪</a><span>2023-06-15</span></li>
  <li><a href="/2023/0601/c5656a600001/page.htm">校园车辆通行证申请流程</a><span>2023-06-01</span></li>
  <li><a href="/about/contact.htm">联系我们</a></li>
</ul>
"""


def test_parse_list_siyuan_filters_and_resolves():
    items = cms.parse_list(LIST_HTML, "https://bwc.ustc.edu.cn", "siyuan")
    # 只收 /cNNNNaNNNN/ 文章链接，过滤掉 /about/contact.htm
    assert len(items) == 2
    assert items[0]["url"] == "https://bwc.ustc.edu.cn/2023/0615/c5656a605980/page.htm"
    assert items[0]["title"] == "无犯罪记录证明介绍信开具流程"  # 取 title 属性
    assert items[0]["date"] == "2023-06-15"
    assert items[1]["title"] == "校园车辆通行证申请流程"  # 退回锚文本
    assert items[1]["date"] == "2023-06-01"


def test_parse_list_dedup_same_url():
    html = LIST_HTML + LIST_HTML  # 同链接重复出现
    items = cms.parse_list(html, "https://bwc.ustc.edu.cn", "siyuan")
    assert len(items) == 2  # 仍去重


# ---------------- cms.parse_article ----------------
ARTICLE_HTML = """
<html><head><title>无犯罪记录证明介绍信开具流程 - 保卫与校园管理处</title></head>
<body><div class="wp_articlecontent">
<p>发布时间：2023-06-15</p>
<p>办理流程：持身份证到保卫处窗口办理。</p>
<a href="/_upload/2023/表格/无犯罪申请表.docx">下载申请表</a>
<img src="/_upload/2023/img/flow.jpg">
</div></body></html>
"""


def test_parse_article_title_date_body_attachment():
    art = cms.parse_article(
        ARTICLE_HTML, "https://bwc.ustc.edu.cn/2023/0615/c5656a605980/page.htm"
    )
    assert art["title"] == "无犯罪记录证明介绍信开具流程"  # 站名后缀被剥掉
    assert art["date"] == "2023-06-15"
    assert "办理流程" in art["body_md"]
    assert any(a.endswith("无犯罪申请表.docx") for a in art["attachments"])
    assert any(i.endswith("flow.jpg") for i in art["images"])


# ---------------- audit.privacy_scan（最关键：校准） ----------------
def _set_notes_dir(monkeypatch, path):
    monkeypatch.setattr(config, "NOTES_DIR", str(path))


def test_privacy_scan_flags_real_mobile_not_url_digits(tmp_path, monkeypatch):
    _set_notes_dir(monkeypatch, tmp_path)
    # 一行真手机号；一行上传URL里恰好含11位数字串（曾被裸 grep 误判）
    (tmp_path / "a.md").write_text(
        "咨询电话 13800138000\n"
        "附件：/uploadfiles/2025/09/20250916100046492.jpg\n"
        "时间戳 1763085598360\n",
        encoding="utf-8",
    )
    hits = audit.privacy_scan()
    values = [h["value"] for h in hits]
    assert "13800138000" in values  # 真手机号 → 命中
    assert (
        "16100046492" not in values
    )  # 上传URL内数字 → 不命中（行含 uploadfiles 被跳过）
    assert all("http" not in h.get("file", "") for h in hits)


def test_privacy_scan_landline_not_flagged(tmp_path, monkeypatch):
    _set_notes_dir(monkeypatch, tmp_path)
    (tmp_path / "b.md").write_text("总机 0551-63602251\n", encoding="utf-8")
    assert audit.privacy_scan() == []  # 座机不是 1[3-9] 开头的11位 → 不误报


# ---------------- audit.dedup ----------------
def test_dedup_keeps_shortest_basename(tmp_path, monkeypatch):
    _set_notes_dir(monkeypatch, tmp_path)
    body = "---\ntype: ustc-procedure\ninstitution: ustc\ntopic: procedures\nsource: https://x/1\n---\n# t\n"
    (tmp_path / "jiaowu_a.md").write_text(body, encoding="utf-8")
    (tmp_path / "benke_aaaa.md").write_text(body, encoding="utf-8")
    removed = audit.dedup()
    assert removed == 1
    assert (tmp_path / "jiaowu_a.md").exists()  # 短名留下
    assert not (tmp_path / "benke_aaaa.md").exists()


def test_dedup_no_false_merge_distinct_sources(tmp_path, monkeypatch):
    _set_notes_dir(monkeypatch, tmp_path)
    (tmp_path / "a.md").write_text("source: https://x/1\n", encoding="utf-8")
    (tmp_path / "b.md").write_text("source: https://x/2\n", encoding="utf-8")
    assert audit.dedup() == 0


# ---------------- audit.structure_scan ----------------
def test_structure_scan_flags_missing_frontmatter(tmp_path, monkeypatch):
    _set_notes_dir(monkeypatch, tmp_path)
    (tmp_path / "good.md").write_text(
        "---\ntype: ustc-procedure\ninstitution: ustc\ntopic: procedures\nsource: https://x/1\n---\n# t\n",
        encoding="utf-8",
    )
    (tmp_path / "bad.md").write_text("# 没有 frontmatter\n", encoding="utf-8")
    bad = audit.structure_scan()
    assert "bad.md" in bad
    assert "good.md" not in bad


# ---------------- crawl 增量过滤（_filter_items / _crawled_urls） ----------------
def _mk(*specs):
    return [{"url": u, "title": t, "date": d} for u, t, d in specs]


def test_filter_items_skips_crawled():
    items = _mk(("https://x/1", "a", "2024-01-01"), ("https://x/2", "b", "2024-02-01"))
    out = crawl._filter_items(items, crawled={"https://x/1"})
    assert [it["url"] for it in out] == ["https://x/2"]


def test_filter_items_since_keeps_undated():
    items = _mk(
        ("https://x/1", "old", "2024-01-01"),
        ("https://x/2", "new", "2024-06-01"),
        ("https://x/3", "nodate", ""),
    )
    urls = [it["url"] for it in crawl._filter_items(items, since="2024-03-01")]
    assert "https://x/1" not in urls  # 早于 since 被剔
    assert "https://x/2" in urls
    assert "https://x/3" in urls  # 无日期保留


def test_filter_items_limit():
    items = _mk(*[("https://x/%d" % i, str(i), "") for i in range(10)])
    assert len(crawl._filter_items(items, limit=3)) == 3


def test_crawled_urls_only_successful(tmp_path, monkeypatch):
    import json as _json

    monkeypatch.setattr(config, "DATA_DIR", str(tmp_path))
    lines = [
        {"url": "https://x/ok", "status": "ok"},
        {"url": "https://x/empty", "status": "empty_or_attachment_only"},
        {"url": "https://x/fail", "status": "fetch_fail"},
        {"url": "https://x/login", "status": "login_required"},
    ]
    (tmp_path / "jiaowu.jsonl").write_text(
        "\n".join(_json.dumps(x) for x in lines) + "\n", encoding="utf-8"
    )
    assert crawl._crawled_urls("jiaowu") == {"https://x/ok", "https://x/empty"}


# ---------------- colleges 自动发现 ----------------
YXJS_HTML = """
<div class="yx">
  <a href="https://physics.ustc.edu.cn/">物理学院</a>
  <a href="https://math.ustc.edu.cn/">数学科学学院</a>
  <a href="https://phys.ustc.edu.cn/">物理系</a>
  <a href="https://www.ustc.edu.cn/">学校首页</a>
  <a href="https://news.ustc.edu.cn/">新闻网</a>
  <a href="https://physics.ustc.edu.cn/2010/1014/c3617a31842/page.htm">光学与光学工程系</a>
</div>
"""


def test_discover_colleges_keeps_real_subdomains():
    cols = colleges.discover_colleges(YXJS_HTML)
    # 取学院链接(按子域名)，跳过 www/news 门户；路径型子条目折叠到同子域名
    assert set(cols) == {"physics", "math", "phys"}
    assert cols["physics"]["base"] == "https://physics.ustc.edu.cn"
    assert cols["physics"]["name"] == "物理学院"


GLJG_HTML = """
<div class="gljg">
  <a href="https://sie.ustc.edu.cn/main.htm">创新创业学院</a>
  <a href="https://zhb.ustc.edu.cn/">科技成果转化处</a>
  <a href="https://www.kyb.ustc.edu.cn/">科研部</a>
  <a href="https://www.ustc.edu.cn/">学校首页</a>
</div>
"""


def test_discover_admin_units_handles_www_and_paths():
    units = colleges.discover_admin_units(GLJG_HTML)
    # 管理机构全收（非门户）；可选 www. 前缀与后续路径都归一到子域名根
    assert set(units) == {"sie", "zhb", "kyb"}
    assert units["sie"]["base"] == "https://sie.ustc.edu.cn"  # /main.htm 被剥掉
    assert units["kyb"]["base"] == "https://kyb.ustc.edu.cn"  # www. 被剥掉
    assert units["zhb"]["name"] == "科技成果转化处"


def test_seed_units_in_build_registered():
    # 种子单位(创新实践基地 epc-ietc 等)即便不在 yxjs/gljg，也应进 _SEED_UNITS
    assert "epc-ietc" in colleges._SEED_UNITS


def _raise(*_a, **_k):
    raise RuntimeError("boom")


def test_process_item_fetch_fail(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl.http, "fetch", _raise)
    r = crawl._process_item(
        {"url": "https://x/1", "title": "T", "date": ""}, {"name": "D"}, "dep",
        str(tmp_path), True,
    )
    assert r["status"] == "fetch_fail" and "boom" in r["reason"]


def test_process_item_ok(tmp_path, monkeypatch):
    monkeypatch.setattr(crawl.config, "DATA_ROOT", str(tmp_path))
    body = "这是一段足够长的正文内容用于单元测试判定逻辑确保超过三十字符门槛"
    monkeypatch.setattr(
        crawl.http, "fetch", lambda u: ("<html><body><p>%s</p></body></html>" % body, u)
    )
    monkeypatch.setattr(crawl.files, "archive", lambda *a, **k: [])
    notep = str(tmp_path / "notes" / "n.md")
    monkeypatch.setattr(crawl.notes, "write", lambda *a, **k: notep)
    r = crawl._process_item(
        {"url": "https://x/1", "title": "标题", "date": "2026-01-01"},
        {"name": "D", "topic": "procedures"}, "dep", str(tmp_path), True,
    )
    assert r["status"] == "ok"
    assert r["url"] == "https://x/1" and r["note"] == os.path.join("notes", "n.md")


def test_research_seed_present():
    # 科研机构种子(火灾安全国重/语音国家工程中心等)落 research/<id>
    assert "sklfs" in colleges._RESEARCH_SEED
    assert "nelslip" in colleges._RESEARCH_SEED
    assert colleges._RESEARCH_SEED["sklfs"] == "火灾安全全国重点实验室"


# ---------------- WordPress 适配（oic 国合部这类非思源站） ----------------
WP_LIST_HTML = """
<div class="posts">
  <a href="https://oic.ustc.edu.cn/master/7727-2/">出访回国人员工资申请</a>
  <a href="https://oic.ustc.edu.cn/?post_type=news&p=18463">国际交流新闻一则</a>
  <a href="https://oic.ustc.edu.cn/about/">关于我们</a>
</div>
"""


def test_parse_list_wordpress_matches_permalinks():
    items = cms.parse_list(WP_LIST_HTML, "https://oic.ustc.edu.cn", "wordpress")
    urls = {i["url"] for i in items}
    assert any("7727-2" in u for u in urls)  # /slug/NNNN-N/ 固定链接
    assert any("p=18463" in u for u in urls)  # ?p=N
    assert all("/about/" not in u for u in urls)  # 非文章链接排除


def test_page_urls_wordpress_paginates():
    u = "https://oic.ustc.edu.cn/news-category/project-news/"
    pages = cms.page_urls(u, "wordpress")
    assert pages[0] == u
    assert "https://oic.ustc.edu.cn/news-category/project-news/page/2/" in pages
    # 思源形态不受影响
    sy = cms.page_urls("https://x.ustc.edu.cn/12/list.htm")
    assert sy[0].endswith("/12/list.htm")


WP_HOME_HTML = """
<html><head><link href="/wp-content/themes/x/style.css"></head><body>
<nav>
  <a href="https://oic.ustc.edu.cn/news-category/project-news/">项目新闻</a>
  <a href="https://oic.ustc.edu.cn/policy">规章制度</a>
  <a href="https://other.com/category/x/">外站</a>
  <a href="https://oic.ustc.edu.cn/login">返回登录</a>
</nav></body></html>
"""


def test_discover_columns_detects_wordpress():
    cols = colleges.discover_columns("https://oic.ustc.edu.cn", WP_HOME_HTML)
    assert cols and all(c["cms"] == "wordpress" for c in cols)
    urls = {c["url"] for c in cols}
    assert "https://oic.ustc.edu.cn/news-category/project-news/" in urls  # 分类法
    assert "https://oic.ustc.edu.cn/policy/" in urls  # 含"规章"关键词的板块
    assert all("other.com" not in u for u in urls)  # 站外排除


# ---------------- column 适配（gradschool 研究生院这类新版栏目站） ----------------
COLUMN_LIST_HTML = """
<div class="list">
  <a href="/article/3417">研究生奖学金评定通知</a>
  <a href="/article/3384">学位授予工作安排</a>
  <a href="/column/43">子栏目</a>
</div>
"""


def test_parse_list_column_matches_article_links():
    items = cms.parse_list(COLUMN_LIST_HTML, "https://gradschool.ustc.edu.cn", "column")
    urls = {i["url"] for i in items}
    assert "https://gradschool.ustc.edu.cn/article/3417" in urls
    assert "https://gradschool.ustc.edu.cn/article/3384" in urls
    assert all("/column/" not in u for u in urls)  # 子栏目不当文章


def test_page_urls_column_underscore_pagination():
    pages = cms.page_urls("https://gradschool.ustc.edu.cn/column/9", "column")
    assert pages[0] == "https://gradschool.ustc.edu.cn/column/9"
    assert "https://gradschool.ustc.edu.cn/column/9_2" in pages
    assert "https://gradschool.ustc.edu.cn/column/9_3" in pages


COLUMN_HOME_HTML = """
<html><head><script src="/static/js/page/kkpager.min.js"></script></head><body>
<nav>
  <a href="https://gradschool.ustc.edu.cn/column/9">通知公告</a>
  <a href="https://gradschool.ustc.edu.cn/column/7">规章制度</a>
  <a href="https://other.org/column/1">外站</a>
</nav></body></html>
"""


def test_siyuan_col_rewrites_main_htm():
    # /col/list.htm、/column/N 原样;/col/main.htm 改写成 /col/list.htm;根 /main.htm 不当栏目
    assert colleges._siyuan_col("https://x.ustc.edu.cn/12/list.htm") == "https://x.ustc.edu.cn/12/list.htm"
    assert colleges._siyuan_col("https://zhb.ustc.edu.cn/cgzhgs/main.htm") == "https://zhb.ustc.edu.cn/cgzhgs/list.htm"
    assert colleges._siyuan_col("https://zhb.ustc.edu.cn/main.htm") is None
    assert colleges._siyuan_col("https://x.ustc.edu.cn/about.htm") is None


def test_discover_columns_detects_column_cms():
    cols = colleges.discover_columns("https://gradschool.ustc.edu.cn", COLUMN_HOME_HTML)
    assert cols and all(c["cms"] == "column" for c in cols)
    urls = {c["url"] for c in cols}
    assert "https://gradschool.ustc.edu.cn/column/9" in urls
    assert all("other.org" not in u for u in urls)


# ---------------- job 就业处（ahbys JSON-API）解析 ----------------
def test_job_parse_columns():
    columnlist = [
        "<li><a style='x' href='javascript:ClassChange(1,1004,1)'>热点新闻</a></li>",
        "<li><a href='javascript:ClassChange(1,1005,1)'>新闻公告</a></li>",
    ]
    cols = job.parse_columns(columnlist)
    assert cols == {"1004": "热点新闻", "1005": "新闻公告"}


def test_job_parse_rows_cid_title_date():
    rows = [
        "<tr><td><span style='color:red'>【置顶】</span>"
        "<a href='Article.html?parentid=1&columnid=1005&cid=9076'>困难毕业生求职补贴通知</a></td>"
        "<td>2026-06-15</td></tr>",
        "<tr><td><a href='Article.html?parentid=1&columnid=1005&cid=9075'>校园双选会安排</a></td>"
        "<td>2026/6/1</td></tr>",
    ]
    items = job.parse_rows(rows)
    assert items[0] == {"cid": "9076", "title": "困难毕业生求职补贴通知", "date": "2026-06-15"}
    assert items[1]["cid"] == "9075" and items[1]["title"] == "校园双选会安排"


COLLEGE_HOME_HTML = """
<nav>
  <a href="/3584/list.htm">通知公告</a>
  <a href="/xsgz/list.htm">学生工作</a>
  <a href="/3574/list.htm">文档表格</a>
  <a href="https://other.example.com/list.htm">站外通知</a>
  <a href="/about.htm">关于我们</a>
  <a href="/3588/list.htm">学院要闻</a>
</nav>
"""


def test_discover_columns_filters_keyword_listpat_samehost():
    cols = colleges.discover_columns("https://physics.ustc.edu.cn", COLLEGE_HOME_HTML)
    urls = {c["url"] for c in cols}
    # 收：含关键词 + list.htm 形态 + 同域；弃：站外、无关键词的 /about.htm
    assert "https://physics.ustc.edu.cn/3584/list.htm" in urls
    assert "https://physics.ustc.edu.cn/xsgz/list.htm" in urls
    assert all("other.example.com" not in u for u in urls)
    assert all(u.endswith("list.htm") for u in urls)
    assert all(c["cms"] == "siyuan" for c in cols)
