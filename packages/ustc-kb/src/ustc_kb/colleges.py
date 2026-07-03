# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""各学院/系 + 管理机构等 USTC 组织单位的自动发现 + 注册。

科大 50+ 学院 + 40+ 管理机构（处/部/办/中心/院/集团…）手工逐站登记不现实，改自动：
  yxjs.htm 院系介绍   -> 学院/系 (kind=college, topic=colleges/<子域名>)
  xxgk/gljg.htm 管理机构 -> 处/部/办/中心等 (kind=admin, topic=orgs/<子域名>)
每个单位抽出子域名 -> 逐站首页启发式发现内容栏目(通知/规章/下载/教学/概况…的 list.htm)
-> 写 colleges.json。栏目复用 cms.py 的 "siyuan" 适配器（/cNNNNaNNNN/ + list.htm 翻页）。
crawl 时各单位当作一个 dept(dept_id=子域名)，附件落 files/<子域名>/。
已在手工 sites.json 里登记的 10 个部处（按子域名）自动跳过，不重复抓。
"""

import json
import os
import re
import urllib.parse

from bs4 import BeautifulSoup

from . import http, registry

YXJS_URL = "https://www.ustc.edu.cn/yxjs.htm"
GLJG_URL = "https://www.ustc.edu.cn/xxgk/gljg.htm"
COLLEGES_PATH = os.path.join(os.path.dirname(__file__), "colleges.json")

# 院系名特征（yxjs 里过滤掉非院系导航链接）
_NAME_KW = re.compile(r"(学院|研究院|学校|系$|系（|系\(|学部)")
# 内容栏目锚文本关键词（含"界面/结构"信息:组织架构/现任领导/部门职责/机构设置）
_COL_KW = re.compile(
    r"(通知|公告|新闻|动态|资讯|规章|制度|规定|办法|下载|表格|文件|培养|教学|"
    r"本科|研究生|教务|概况|简介|师资|人才培养|教育教学|学生工作|党建|学术|报告|讲座|招生|"
    r"领导|机构|架构|职责|组织|沿革|历史|关于|设置|部门|中心|联系)"
)

# 已知独立站点但不在 yxjs/gljg 两个注册页里的单位（教辅/中心/基地等），手工补种。
# 用户可继续往这里加；kind=admin 落 orgs/<子域名>。
_SEED_UNITS = {
    "epc-ietc": "工程实践与创新创业教学中心",  # 创新实践基地/双创服务
    "institution": "实验室主页",
}

# 科研机构/重点实验室（科研部「科研平台」目录 + jianj.jsp 抽得，逐站核实可达+取真名）。
# 多在独立子域名,siyuan/column/WP 自动识别;kind=research 落 research/<子域名>。
# 用户可继续加(nsrl 国家同步辐射/hfnl 微尺度等返 HTTPError 暂略,可补)。
_RESEARCH_SEED = {
    "sklfs": "火灾安全全国重点实验室",
    "nelslip": "语音及语言信息处理国家工程研究中心",
    "leinao": "类脑智能技术及应用国家工程实验室",
    "ceni": "未来网络试验设施",
    "immune": "免疫应答与免疫治疗全国重点实验室",
    "pichem": "精准智能化学全国重点实验室",
    "erctst": "热安全技术国家地方联合工程研究中心",
    "quantum": "量子物理与量子信息研究部",
    "journals": "中国科学技术大学期刊中心",
}
# 思源栏目列表页 URL 形态：/<栏目>/list.htm、/NNNN/list.htm、/column/N、/list.htm
_LISTPAT = re.compile(r"(?:/[\w\-]+)?/list\d*\.htm$|/column/\d+$")
# 排除的子域名（门户/新闻/非组织单位）
_SKIP_SUB = {"www", "news", "en", "today", "i", "passport", "id", "mail"}
# 单位站点子域名（允许可选 www. 前缀、忽略后续路径）
_HOST_RE = re.compile(r"https?://(?:www\.)?([a-z0-9\-]+)\.ustc\.edu\.cn", re.I)


def _curated_subs():
    """手工 sites.json 已登记部处的子域名（去 www.），自动发现时跳过避免重复。"""
    subs = set()
    for v in registry.departments().values():
        m = _HOST_RE.match(str(v.get("base", "")))
        if m:
            subs.add(m.group(1).lower())
    return subs


def _extract(html):
    """从注册页抽取 {子域名: 单位名}（任意 *.ustc.edu.cn 锚链接，首个名字优先）。"""
    out = {}
    for m in re.finditer(
        r'href="(https?://[^"]*?\.ustc\.edu\.cn[^"]*)"[^>]*>\s*([^<]{2,40})\s*</a>',
        html,
    ):
        href, name = m.group(1), m.group(2).strip()
        hm = _HOST_RE.match(href)
        if not hm:
            continue
        sub = hm.group(1).lower()
        if sub in _SKIP_SUB:
            continue
        out.setdefault(sub, name)
    return out


def discover_colleges(html=None):
    """yxjs.htm -> {子域名: {name, base}}（院系名过滤）。"""
    if html is None:
        html, _ = http.fetch(YXJS_URL)
    out = {}
    for sub, name in _extract(html).items():
        if _NAME_KW.search(name):
            out[sub] = {"name": name, "base": "https://%s.ustc.edu.cn" % sub}
    return out


def discover_admin_units(html=None):
    """xxgk/gljg.htm 管理机构 -> {子域名: {name, base}}（该页本身即单位清单，全收）。"""
    if html is None:
        html, _ = http.fetch(GLJG_URL)
    return {
        sub: {"name": name, "base": "https://%s.ustc.edu.cn" % sub}
        for sub, name in _extract(html).items()
    }


def _discover_wp_columns(base, html):
    """WordPress 站（如 oic 国合部）的栏目：分类法 /(news-)category/<x>/ + 含关键词的一级板块。"""
    soup = BeautifulSoup(html, "html.parser")
    host = urllib.parse.urlparse(base).netloc
    cols = {}
    for a in soup.find_all("a", href=True):
        t = (a.get_text() or "").strip()
        full = urllib.parse.urljoin(base, str(a["href"])).split("?")[0].split("#")[0]
        pu = urllib.parse.urlparse(full)
        if pu.netloc != host:
            continue
        is_tax = re.search(r"/(?:news-)?category/[\w\-]+/?$", pu.path)
        is_section = bool(re.match(r"^/[\w\-]+/?$", pu.path)) and t and _COL_KW.search(t)
        if is_tax or is_section:
            cols.setdefault(full.rstrip("/") + "/", t or "栏目")
    return [{"url": u, "cms": "wordpress", "label": t} for u, t in cols.items()]


def _discover_column_columns(base, html):
    """新版栏目站（如 gradschool）：栏目是 /column/N（文章 /article/N、kkpager 分页）。"""
    soup = BeautifulSoup(html, "html.parser")
    host = urllib.parse.urlparse(base).netloc
    cols = {}
    for a in soup.find_all("a", href=True):
        t = (a.get_text() or "").strip()
        if not t or len(t) > 14 or not _COL_KW.search(t):
            continue
        full = urllib.parse.urljoin(base, str(a["href"])).split("?")[0].split("#")[0]
        if urllib.parse.urlparse(full).netloc != host:
            continue
        if re.search(r"/column/\d+$", full):
            cols.setdefault(full, t)
    return [{"url": u, "cms": "column", "label": t} for u, t in cols.items()]


def _siyuan_col(full):
    """把一个链接归一成思源可枚举的列表页 URL，不匹配则 None。
    /xxx/list.htm、/column/N 原样;/xxx/main.htm 改写成 /xxx/list.htm
    (部分思源站列页用 main.htm 而非 list.htm,如 zhb 成果转化处,否则被漏发现)。"""
    if _LISTPAT.search(full):
        return full
    m = re.search(r"(.+/[\w\-]+)/main\.htm$", full)
    if m:
        return m.group(1) + "/list.htm"
    return None


def discover_columns(base, html=None):
    """启发式发现一个单位站的内容栏目 -> [{url, cms, label}]。自动识别 CMS：
    WordPress（wp-content）走分类法；新版栏目站（kkpager/article）走 /column/N；
    否则按思源 list.htm 形态。"""
    if html is None:
        try:
            html, _ = http.fetch(base)
        except Exception:  # noqa: BLE001
            return []
    if "wp-content" in html or "/wp-json" in html:
        return _discover_wp_columns(base, html)
    # 新版栏目站：kkpager 分页插件 / 有 /article/ 链接 / 首页有多个 /column/N 导航
    if (
        "kkpager" in html
        or "/article/" in html
        or len(set(re.findall(r"/column/\d+", html))) >= 3
    ):
        cols = _discover_column_columns(base, html)
        if cols:
            return cols
    soup = BeautifulSoup(html, "html.parser")
    host = urllib.parse.urlparse(base).netloc
    cols = {}
    for a in soup.find_all("a", href=True):
        t = (a.get_text() or "").strip()
        if not t or len(t) > 14 or not _COL_KW.search(t):
            continue
        full = urllib.parse.urljoin(base, str(a["href"])).split("?")[0].split("#")[0]
        if urllib.parse.urlparse(full).netloc != host:
            continue
        col_url = _siyuan_col(full)
        if col_url:
            cols.setdefault(col_url, t)
    return [{"url": u, "cms": "siyuan", "label": t} for u, t in cols.items()]


def build(limit_cols=40, colleges_html=None, admin_html=None):
    """构建 colleges.json（逐站联网发现栏目）。返回 {子域名: dept-like dict}。

    合并 学院(kind=college) + 管理机构(kind=admin)，按子域名去重(学院优先)，
    跳过 sites.json 已手工登记的部处。topic 按 kind 分目录。
    """
    curated = _curated_subs()
    def _seed(d):
        return {
            sub: {"name": name, "base": "https://%s.ustc.edu.cn" % sub}
            for sub, name in d.items()
        }

    sources = [
        ("college", "colleges", discover_colleges(colleges_html)),
        ("admin", "orgs", discover_admin_units(admin_html)),
        ("admin", "orgs", _seed(_SEED_UNITS)),
        ("research", "research", _seed(_RESEARCH_SEED)),
    ]
    reg = {}
    for kind, top, found in sources:
        for cid, info in found.items():
            if cid in curated or cid in reg:
                continue  # 已手工登记 / 已被前一来源(学院优先)收录
            cols = discover_columns(info["base"])[:limit_cols]
            reg[cid] = {
                "name": info["name"],
                "base": info["base"],
                "kind": kind,
                "topic": "%s/%s" % (top, cid),
                "columns": cols,
                "contact": "",
            }
    payload = {
        "_about": "各学院/系 + 管理机构等 USTC 组织单位自动发现清单（colleges.build 生成，"
        "源 yxjs.htm + xxgk/gljg.htm）。重跑 ustc-kb colleges-discover 刷新。"
        "kind=college 落 colleges/<id>，kind=admin 落 orgs/<id>。",
        "sources": [YXJS_URL, GLJG_URL],
        "colleges": reg,
    }
    with open(COLLEGES_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=1)
    return reg
