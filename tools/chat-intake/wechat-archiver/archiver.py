# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
wechat-archiver —— 跑在香橙派上的轻量归档服务。
把 we-mp-rss 已抓取的公众号文章转成 Markdown + 把图片下载到本地，集中存档在香橙派。
复用 we-mp-rss 的 venv(markdownify/httpx/fastapi 已装)。低负载:单并发、图片下载限速。

环境变量:
  WEMP_BASE_URL   we-mp-rss 地址，默认 http://127.0.0.1:8001
                  (兼容旧名 WEMP_BASE，读到旧名会打一次弃用告警)
  WEMP_USERNAME   默认 admin      (旧名 WEMP_USER)
  WEMP_PASSWORD   必填,只走env(旧名 WEMP_PWD)
  ARCHIVE_DIR     存档根目录，默认 ~/wechat-archive
  ARCH_PORT       监听端口，默认 8011
"""
import os, re, sys, time, html as htmllib, hashlib, threading
from urllib.parse import urlparse
import httpx
from markdownify import markdownify as to_md
from fastapi import FastAPI
from fastapi.responses import JSONResponse
import uvicorn


def _env_compat(new_name: str, old_name: str, default: str) -> str:
    """新名优先、旧名兜底(向后兼容 we-mp-rss-mcp 已用的规范名)。

    读到旧名(且未设新名)时,向 stderr 打一次弃用告警。
    """
    val = os.environ.get(new_name)
    if val is not None:
        return val
    val = os.environ.get(old_name)
    if val is not None:
        print(
            f"[wechat-archiver] warning: env {old_name} is deprecated, "
            f"use {new_name} instead (falling back for now).",
            file=sys.stderr,
        )
        return val
    return default


WEMP = _env_compat("WEMP_BASE_URL", "WEMP_BASE", "http://127.0.0.1:8001").rstrip("/")
USER = _env_compat("WEMP_USERNAME", "WEMP_USER", "admin")
PWD = _env_compat("WEMP_PASSWORD", "WEMP_PWD", "")  # 凭据只走env,不带默认密码
ARCHIVE_DIR = os.environ.get("ARCHIVE_DIR", os.path.join(os.path.expanduser("~"), "wechat-archive"))
PORT = int(os.environ.get("ARCH_PORT", "8011"))
IMG_DELAY = float(os.environ.get("IMG_DELAY", "0.25"))   # 每张图间隔，护着 SBC + 防风控
IMG_MAX = int(os.environ.get("IMG_MAX", "8000000"))      # 单图最大 8MB

app = FastAPI(title="wechat-archiver")
_tok = {"v": None}
_lock = threading.Lock()
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"


def _login():
    r = httpx.post(f"{WEMP}/api/v1/wx/auth/login",
                   data={"username": USER, "password": PWD, "grant_type": "password"}, timeout=30)
    r.raise_for_status()
    _tok["v"] = r.json()["data"]["access_token"]
    return _tok["v"]


def _api(method, path, **kw):
    with _lock:
        tok = _tok["v"] or _login()
    h = {"Authorization": f"Bearer {tok}"}
    r = httpx.request(method, f"{WEMP}{path}", headers=h, timeout=kw.pop("timeout", 60), **kw)
    if r.status_code == 401:
        with _lock:
            tok = _login()
        r = httpx.request(method, f"{WEMP}{path}", headers={"Authorization": f"Bearer {tok}"}, timeout=60, **kw)
    r.raise_for_status()
    return r.json()


def _safe(name, maxlen=80):
    name = re.sub(r'[\\/:*?"<>|\n\r\t]+', "_", (name or "").strip())
    return (name[:maxlen] or "untitled").rstrip(". ")


_mp_cache = {}
def _mp_name(mp_id):
    if not _mp_cache:
        try:
            data = _api("GET", "/api/v1/wx/mps?limit=100").get("data", {})
            lst = data.get("list") if isinstance(data, dict) else data
            for it in (lst or []):
                _mp_cache[it.get("id")] = it.get("mp_name")
        except Exception:
            pass
    return _mp_cache.get(mp_id) or mp_id


def _fmt_time(v):
    try:
        from datetime import datetime, timezone, timedelta
        return datetime.fromtimestamp(int(v), timezone(timedelta(hours=8))).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return str(v or "")


def _get_article(article_id, fetch_if_missing=True):
    d = _api("GET", f"/api/v1/wx/articles/{article_id}").get("data", {})
    if not (d.get("content_html") or d.get("content")) and fetch_if_missing:
        _api("POST", f"/api/v1/wx/articles/{article_id}/refresh", timeout=60)
        for _ in range(10):
            time.sleep(3)
            d = _api("GET", f"/api/v1/wx/articles/{article_id}").get("data", {})
            if d.get("content_html") or d.get("content"):
                break
    return d


def _extract_imgs(html_str):
    # 微信图片优先 data-src(懒加载真实地址)，其次 src
    urls = re.findall(r'<img[^>]+data-src="([^"]+)"', html_str)
    urls += re.findall(r'<img[^>]+src="([^"]+)"', html_str)
    out, seen = [], set()
    for u in urls:
        u = htmllib.unescape(u)
        if u.startswith("http") and u not in seen:
            seen.add(u); out.append(u)
    return out


def _ext_from(url, ct):
    for fmt in ("gif", "png", "jpeg", "jpg", "webp"):
        if f"wx_fmt={fmt}" in url:
            return "jpg" if fmt == "jpeg" else fmt
    if "png" in ct: return "png"
    if "gif" in ct: return "gif"
    if "webp" in ct: return "webp"
    return "jpg"


def archive_article(article_id, fetch_if_missing=True):
    d = _get_article(article_id, fetch_if_missing)
    if not d:
        return {"ok": False, "error": "article not found"}
    raw = d.get("content_html") or d.get("content") or ""
    if not raw:
        return {"ok": False, "error": "no content (未抓到正文)"}
    mp_name = d.get("mp_name") or _mp_name(d.get("mp_id"))
    account = _safe(mp_name or "unknown", 40)
    title = _safe(d.get("title") or article_id, 80)
    folder = os.path.join(ARCHIVE_DIR, account, f"{title}__{article_id}")
    img_dir = os.path.join(folder, "images")
    os.makedirs(img_dir, exist_ok=True)

    imgs = _extract_imgs(raw)
    url_map, n_ok = {}, 0
    headers = {"User-Agent": UA, "Referer": "https://mp.weixin.qq.com/"}
    with httpx.Client(headers=headers, timeout=30, follow_redirects=True) as cli:
        for i, u in enumerate(imgs):
            try:
                rr = cli.get(u)
                if rr.status_code == 200 and rr.content and len(rr.content) <= IMG_MAX:
                    ext = _ext_from(u, rr.headers.get("content-type", ""))
                    fn = f"{i:03d}_{hashlib.md5(u.encode()).hexdigest()[:8]}.{ext}"
                    with open(os.path.join(img_dir, fn), "wb") as f:
                        f.write(rr.content)
                    url_map[u] = f"images/{fn}"
                    n_ok += 1
            except Exception:
                pass
            time.sleep(IMG_DELAY)   # 限速

    # 把 HTML 里的图片地址替换成本地相对路径（data-src 也要替）
    body = raw
    for u, local in url_map.items():
        esc = re.escape(htmllib.escape(u))
        body = re.sub(esc, local, body)
        body = body.replace(u, local)
    # data-src -> src，便于 markdownify 取到
    body = re.sub(r'<img([^>]*?)data-src="', r'<img\1src="', body)

    mdtext = to_md(body, heading_style="ATX", strip=["script", "style"])
    mdtext = re.sub(r"\n{3,}", "\n\n", mdtext).strip()
    front = (f"# {d.get('title','')}\n\n"
             f"> 公众号: {mp_name}　|　发布: {_fmt_time(d.get('publish_time'))}\n"
             f"> 原文: {d.get('url','')}\n\n---\n\n")
    md_path = os.path.join(folder, "index.md")
    with open(md_path, "w", encoding="utf-8") as f:
        f.write(front + mdtext + "\n")
    return {"ok": True, "article_id": article_id, "title": d.get("title"),
            "account": mp_name, "path": md_path,
            "images_total": len(imgs), "images_downloaded": n_ok}


@app.post("/archive/article/{article_id}")
def api_archive_article(article_id: str, fetch_if_missing: bool = True):
    return JSONResponse(archive_article(article_id, fetch_if_missing))


@app.post("/archive/account/{mp_id}")
def api_archive_account(mp_id: str, limit: int = 10):
    arts = _api("GET", f"/api/v1/wx/articles?mp_id={mp_id}&limit={max(1,min(limit,100))}").get("data", {})
    lst = arts.get("list") if isinstance(arts, dict) else arts
    results = []
    for a in (lst or []):
        try:
            results.append(archive_article(a["id"]))
        except Exception as e:
            results.append({"ok": False, "article_id": a.get("id"), "error": str(e)[:120]})
        time.sleep(0.5)  # 文章之间也歇一下，控负载
    ok = sum(1 for r in results if r.get("ok"))
    return JSONResponse({"account": mp_id, "archived": ok, "total": len(results), "results": results})


@app.get("/archive/list")
def api_list():
    out = []
    if os.path.isdir(ARCHIVE_DIR):
        for acc in sorted(os.listdir(ARCHIVE_DIR)):
            ap = os.path.join(ARCHIVE_DIR, acc)
            if os.path.isdir(ap):
                arts = [d for d in os.listdir(ap) if os.path.isdir(os.path.join(ap, d))]
                out.append({"account": acc, "count": len(arts)})
    return JSONResponse({"archive_dir": ARCHIVE_DIR, "accounts": out})


@app.get("/health")
def health():
    return {"ok": True, "archive_dir": ARCHIVE_DIR}


if __name__ == "__main__":
    os.makedirs(ARCHIVE_DIR, exist_ok=True)
    uvicorn.run(app, host="0.0.0.0", port=PORT)
