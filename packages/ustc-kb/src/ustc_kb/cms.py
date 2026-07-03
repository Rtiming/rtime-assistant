# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""CMS 解析适配器。USTC 主要 3 类：siyuan(/cNNNNaNNNN/page.htm)、teach(/service/svc-*/N.html)、generic。
解析为纯函数（输入 html 文本，输出结构），抓取/翻页由 crawl.py 负责。"""

import re
import urllib.parse

from bs4 import BeautifulSoup

# 每种 CMS 的「文章链接」识别正则 + 是否分页
LINK_PATTERNS = {
    "siyuan": [r"/c\d+a\d+/"],  # 思源/WeCMS
    "teach": [r"/service/svc-\w+/\d+\.html", r"/document/\S+/\d+\.html"],
    "lib": [
        r"/服务指南/[^/]+/?$",
        r"/问图书馆员/[^/]+/?$",
        r"/本馆概况/[^/]+/?$",
    ],  # 图书馆 WordPress 中文permalink
    # 通用 WordPress（如 oic 国合部）：文章固定链接 /slug/NNNN-N/、?p=NNN、/archives/NNN
    "wordpress": [r"[?&]p=\d+", r"/\d{3,}-\d+/?$", r"/archives/\d+"],
    # 新版栏目站（如 gradschool 研究生院）：栏目 /column/N、文章 /article/N，kkpager 分页
    "column": [r"/article/\d+"],
    "generic": [r"/\d{4}/\d{3,4}/\w+/page\.\w+", r"/c\d+a\d+/", r"\.html?$"],
}
ARTICLE_SELECTORS = [
    ".wp_articlecontent",
    "#vsb_content",
    ".v_news_content",
    ".article-content",
    ".entry-content",
    ".articleContent",
    "article",
    "#content",
    ".content",
    ".main",
]
_FILE_RE = re.compile(r"\.(pdf|docx?|xlsx?|pptx?|zip|rar)$", re.I)
_DATE_RE = re.compile(r"(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})")


def _norm(base, href):
    u = urllib.parse.urljoin(base, href.strip())
    return re.sub(r"\.psp(\?|$)", r".htm\1", u)


def parse_list(html, base, cms="siyuan"):
    """枚举列表页文章条目 -> [{title,url,date}]，按出现顺序去重。"""
    soup = BeautifulSoup(html, "html.parser")
    pats = [
        re.compile(p, re.I) for p in LINK_PATTERNS.get(cms, LINK_PATTERNS["siyuan"])
    ]
    out, seen = [], set()
    for a in soup.find_all("a", href=True):
        href = str(a["href"])
        href_dec = urllib.parse.unquote(href)
        if not any(p.search(href) or p.search(href_dec) for p in pats):
            continue
        url = _norm(base, href)
        if url in seen or url.rstrip("/") == base.rstrip("/"):
            continue
        title = (str(a.get("title") or "") or a.get_text() or "").strip()
        if not title or len(title) < 2:
            continue
        date = ""
        node = a
        for _ in range(4):
            node = node.parent
            if node is None:
                break
            m = _DATE_RE.search(node.get_text(" ", strip=True))
            if m:
                date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3)))
                break
        seen.add(url)
        out.append({"title": title, "url": url, "date": date})
    return out


def page_urls(col_url, cms="siyuan"):
    """分页 URL 列表。思源：list.htm→list2.htm…；WordPress：栏目/page/2/…；
    其余单页返回自身。抓取循环遇到空页即停，故多生成几页无害。"""
    if cms == "wordpress":
        base = col_url.rstrip("/")
        return [col_url] + ["%s/page/%d/" % (base, n) for n in range(2, 30)]
    if cms == "column":
        m = re.match(r"(.*/column/\d+)/?$", col_url)
        if not m:
            return [col_url]
        return [col_url] + ["%s_%d" % (m.group(1), n) for n in range(2, 60)]
    m = re.match(r"(.*/)(list)(\d*)\.(htm|psp)$", col_url)
    if not m:
        return [col_url]
    return [col_url] + [
        "%slist%d.%s" % (m.group(1), n, m.group(4)) for n in range(2, 12)
    ]


def parse_article(html, url):
    """解析文章页 -> {title,date,body_md,attachments(原始文件URL),images}。忠实，不丢信息。"""
    soup = BeautifulSoup(html, "html.parser")
    title = ""
    t = soup.find("title")
    if t:
        title = re.sub(r"\s*[-_|｜·]\s*[^-_|｜·]*$", "", t.get_text()).strip()
    h = soup.select_one(".arti_title, .article_title, h1.title, h1")
    if h and h.get_text(strip=True):
        title = h.get_text(strip=True)
    m = re.search(
        r"(?:发布[时日][间期]|时间)[:：]?\s*(20\d{2})[-./年](\d{1,2})[-./月](\d{1,2})",
        html,
    )
    if not m:
        m = _DATE_RE.search(html)
    date = "%s-%02d-%02d" % (m.group(1), int(m.group(2)), int(m.group(3))) if m else ""
    node = None
    for sel in ARTICLE_SELECTORS:
        node = soup.select_one(sel)
        if node and len(node.get_text(strip=True)) > 20:
            break
    if node is None:
        node = soup.body or soup
    body_md, attaches, images = _to_md(node, url)
    return {
        "title": title,
        "date": date,
        "body_md": body_md,
        "attachments": attaches,
        "images": images,
    }


def _to_md(node, base):
    lines, attaches, images = [], [], []
    for a in node.find_all("a", href=True):
        full = urllib.parse.urljoin(base, str(a["href"]))
        if _FILE_RE.search(full):
            attaches.append(full)
    for el in node.find_all(["h1", "h2", "h3", "h4", "p", "li", "td", "img"]):
        if el.name == "img":
            src = el.get("src") or el.get("data-src")
            if src:
                full = urllib.parse.urljoin(base, str(src))
                images.append(full)
                lines.append("![图片](%s)" % full)
            continue
        for a in el.find_all("a", href=True):
            full = urllib.parse.urljoin(base, str(a["href"]))
            a.replace_with("[%s](%s)" % (a.get_text(strip=True) or full, full))
        txt = el.get_text(" ", strip=True)
        if not txt:
            continue
        if el.name in ("h1", "h2", "h3", "h4"):
            lines.append("\n### " + txt)
        elif el.name == "li":
            lines.append("- " + txt)
        else:
            lines.append(txt)
    md = "\n\n".join(x for x in lines if x.strip())
    return md.strip(), sorted(set(attaches)), sorted(set(images))


def slugify(title):
    s = re.sub(r'[\\/:*?"<>|\n\r\t]+', "", str(title)).strip()
    return s[:80] or "untitled"
