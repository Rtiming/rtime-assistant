# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
微信公众号检索 MCP server —— 给 AI 助手提供"搜公众号文章"的工具。

后端：自托管的 we-mp-rss（默认 http://127.0.0.1:8001）。
鉴权：用管理员账号登录换 JWT，401 自动重登。

环境变量：
  WEMP_BASE_URL   默认 http://127.0.0.1:8001
  WEMP_USERNAME   默认 admin
  WEMP_PASSWORD   必填,只走env(不带默认密码)
"""
import os
import re
import time
import threading
from datetime import datetime, timezone, timedelta

import httpx
from mcp.server.fastmcp import FastMCP

BASE = os.environ.get("WEMP_BASE_URL", "http://127.0.0.1:8001").rstrip("/")  # 远程实例走env显式指
ARCHIVER = os.environ.get("ARCHIVER_BASE_URL", "http://127.0.0.1:8011").rstrip("/")
USER = os.environ.get("WEMP_USERNAME", "admin")
PWD = os.environ.get("WEMP_PASSWORD", "")  # 凭据只走env,不带默认密码
API = "/api/v1/wx"

mcp = FastMCP("wechat-mp")

_token = {"v": None}
_lock = threading.Lock()
CST = timezone(timedelta(hours=8))


def _login() -> str:
    r = httpx.post(
        f"{BASE}{API}/auth/login",
        data={"username": USER, "password": PWD, "grant_type": "password"},
        timeout=30,
    )
    r.raise_for_status()
    tok = r.json()["data"]["access_token"]
    _token["v"] = tok
    return tok


def _req(method: str, path: str, **kw):
    with _lock:
        tok = _token["v"] or _login()
    timeout = kw.pop("timeout", 60)
    headers = {"Authorization": f"Bearer {tok}"}
    r = httpx.request(method, f"{BASE}{path}", headers=headers, timeout=timeout, **kw)
    if r.status_code == 401:
        with _lock:
            tok = _login()
        headers["Authorization"] = f"Bearer {tok}"
        r = httpx.request(method, f"{BASE}{path}", headers=headers, timeout=timeout, **kw)
    r.raise_for_status()
    return r.json()


def _fmt_time(v):
    try:
        return datetime.fromtimestamp(int(v), CST).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(v) if v else ""


def _strip_html(html: str) -> str:
    if not html:
        return ""
    text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"&nbsp;", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _resolve_account(account: str):
    """把账号名(或内部id)解析成内部 mp_id；空字符串返回 None(=全部)。"""
    if not account:
        return None
    if account.startswith("MP_"):
        return account
    data = _req("GET", f"{API}/mps?limit=100").get("data", {})
    lst = data.get("list") if isinstance(data, dict) else data
    for it in (lst or []):
        if account in (it.get("mp_name") or ""):
            return it.get("id")
    return account  # 没匹配上就原样传，让后端去判断


def _article_brief(a: dict) -> dict:
    return {
        "article_id": a.get("id"),
        "title": a.get("title"),
        "account": a.get("mp_name") or a.get("mp_id"),
        "publish_time": _fmt_time(a.get("publish_time")),
        "url": a.get("url"),
        "has_content": bool(a.get("has_content")),
    }


@mcp.tool()
def search_articles(keyword: str, account: str = "", limit: int = 10) -> list:
    """在已订阅的公众号文章里按关键词全文检索。

    keyword: 搜索词（匹配标题/内容）。
    account: 可选，限定某个公众号（可传公众号名字的一部分，或内部id）。
    limit:   返回条数，默认 10。
    返回：文章简要列表（article_id/title/account/publish_time/url）。
    """
    mp_id = _resolve_account(account)
    params = {"search": keyword, "limit": max(1, min(limit, 50))}
    if mp_id:
        params["mp_id"] = mp_id
    data = _req("GET", f"{API}/articles", params=params).get("data", {})
    lst = data.get("list") if isinstance(data, dict) else data
    return [_article_brief(a) for a in (lst or [])]


@mcp.tool()
def latest_articles(account: str = "", limit: int = 10) -> list:
    """获取最新文章（可按公众号过滤），按发布时间倒序。

    account: 可选，公众号名字的一部分或内部id；留空=全部订阅号。
    limit:   返回条数，默认 10。
    """
    mp_id = _resolve_account(account)
    params = {"limit": max(1, min(limit, 50))}
    if mp_id:
        params["mp_id"] = mp_id
    data = _req("GET", f"{API}/articles", params=params).get("data", {})
    lst = data.get("list") if isinstance(data, dict) else data
    return [_article_brief(a) for a in (lst or [])]


@mcp.tool()
def get_article(article_id: str, fetch_if_missing: bool = True) -> dict:
    """获取单篇文章正文（纯文本）。若正文未抓取且 fetch_if_missing=True，会触发抓取并等待。

    返回：{title, account, publish_time, url, content}（content 为纯文本，最多约 8000 字）。
    """
    data = _req("GET", f"{API}/articles/{article_id}").get("data", {})
    content = data.get("content") or data.get("content_html") or ""
    if not content and fetch_if_missing:
        _req("POST", f"{API}/articles/{article_id}/refresh", timeout=60)
        for _ in range(10):
            time.sleep(3)
            data = _req("GET", f"{API}/articles/{article_id}").get("data", {})
            content = data.get("content") or data.get("content_html") or ""
            if content:
                break
    text = _strip_html(content)
    return {
        "title": data.get("title"),
        "account": data.get("mp_name") or data.get("mp_id"),
        "publish_time": _fmt_time(data.get("publish_time")),
        "url": data.get("url"),
        "content": text[:8000],
    }


@mcp.tool()
def list_subscriptions() -> list:
    """列出当前已订阅（已抓取）的公众号。返回 [{id, name, intro}]。"""
    data = _req("GET", f"{API}/mps?limit=100").get("data", {})
    lst = data.get("list") if isinstance(data, dict) else data
    return [
        {"id": it.get("id"), "name": it.get("mp_name"), "intro": it.get("mp_intro")}
        for it in (lst or [])
    ]


@mcp.tool()
def find_official_accounts(keyword: str) -> list:
    """在微信上搜索公众号（用于发现并准备订阅新号）。返回 [{name, mp_id(fakeid), alias, intro}]。"""
    data = _req("GET", f"{API}/mps/search/{keyword}").get("data", {})
    lst = data.get("list") if isinstance(data, dict) else data
    return [
        {
            "name": it.get("nickname"),
            "mp_id": it.get("fakeid"),
            "alias": it.get("alias"),
            "intro": it.get("signature"),
        }
        for it in (lst or [])
    ]


@mcp.tool()
def subscribe_account(mp_name: str, mp_id: str) -> dict:
    """订阅一个公众号（mp_id 用 find_official_accounts 返回的 fakeid）。订阅后会自动开始抓取历史文章。"""
    payload = {"mp_name": mp_name, "mp_id": mp_id}
    res = _req("POST", f"{API}/mps", json=payload)
    return {"code": res.get("code"), "message": res.get("message")}


@mcp.tool()
def refresh_account(account: str) -> dict:
    """抓取某个已订阅公众号的最新文章。account 传公众号名字的一部分或内部id。"""
    mp_id = _resolve_account(account)
    if not mp_id:
        return {"error": "未找到该公众号，请先用 list_subscriptions 查看"}
    res = _req("GET", f"{API}/mps/update/{mp_id}", timeout=90)
    return {"code": res.get("code"), "message": res.get("message"), "mp_id": mp_id}


@mcp.tool()
def archive_article(article_id: str) -> dict:
    """把一篇公众号文章转成 Markdown 并把里面的图片下载到本地，集中存档在香橙派。
    返回存档路径、图片数。存档目录在香橙派 ~/wechat-archive/公众号名/标题__id/(index.md + images/)。
    """
    r = httpx.post(f"{ARCHIVER}/archive/article/{article_id}", timeout=200)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def archive_account(account: str, limit: int = 5) -> dict:
    """批量把某公众号最近 limit 篇文章存档成 Markdown + 本地图片(存香橙派)。
    account 传公众号名字的一部分或内部id；limit 默认 5(批量较慢，护着设备，可分多次)。
    """
    mp_id = _resolve_account(account)
    if not mp_id:
        return {"error": "未找到该公众号，请先用 list_subscriptions 查看"}
    r = httpx.post(f"{ARCHIVER}/archive/account/{mp_id}", params={"limit": max(1, min(limit, 20))}, timeout=1800)
    r.raise_for_status()
    return r.json()


@mcp.tool()
def list_archive() -> dict:
    """列出香橙派上已存档的公众号及篇数。"""
    r = httpx.get(f"{ARCHIVER}/archive/list", timeout=30)
    r.raise_for_status()
    return r.json()


if __name__ == "__main__":
    mcp.run()
