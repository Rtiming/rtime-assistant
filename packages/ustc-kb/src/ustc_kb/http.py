# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""HTTP 抓取与文件下载（标准库，零依赖外网服务）。USTC 站多为自签/旧证书，放宽校验。"""

import hashlib
import os
import re
import ssl
import time
import urllib.parse
import urllib.request

_CTX = ssl.create_default_context()
_CTX.check_hostname = False
_CTX.verify_mode = ssl.CERT_NONE
_UA = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36"
}


def _enc(url):
    """百分号编码非 ASCII（中文）路径/查询，保留 URL 结构字符。"""
    return urllib.parse.quote(url, safe="/:?=&#%+,;@()~!$*'")


def fetch(url, tries=3, delay=1.0):
    """抓 HTML 文本，返回 (text, final_url)。识别 gb 系编码。"""
    url = _enc(url)
    last = None
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            r = urllib.request.urlopen(req, timeout=25, context=_CTX)
            raw = r.read()
            head = raw[:3000].decode("latin-1", "replace").lower()
            m = re.search(r'charset=["\']?([\w-]+)', head)
            enc = (
                "gb18030"
                if (m and m.group(1).lower() in ("gb2312", "gbk", "gb18030"))
                else "utf-8"
            )
            return raw.decode(enc, "replace"), r.geturl()
        except Exception as e:  # noqa: BLE001
            last = e
            time.sleep(delay * (i + 1))
    raise last if last else RuntimeError("fetch failed: " + url)


def download(url, dest_dir, tries=3, delay=1.0):
    """下载原始二进制文件到 dest_dir。返回 (local_path, size, sha256) 或 None。按 sha256 去重。"""
    url = _enc(url)
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=_UA)
            r = urllib.request.urlopen(req, timeout=60, context=_CTX)
            data = r.read()
            sha = hashlib.sha256(data).hexdigest()
            name = urllib.parse.unquote(
                os.path.basename(urllib.parse.urlparse(url).path)
            )
            if not name or name.lower() in ("undefined", "null"):
                name = sha[:12]
            name = re.sub(r'[\\/:*?"<>|]+', "_", name)
            os.makedirs(dest_dir, exist_ok=True)
            path = os.path.join(dest_dir, name)
            # 同名不同内容 -> 加 sha 前缀；同名同内容 -> 复用
            if os.path.exists(path):
                with open(path, "rb") as _f:
                    old = hashlib.sha256(_f.read()).hexdigest()
                if old != sha:
                    stem, ext = os.path.splitext(name)
                    path = os.path.join(dest_dir, "%s_%s%s" % (stem, sha[:8], ext))
            if not os.path.exists(path):
                with open(path, "wb") as _f:
                    _f.write(data)
            return path, len(data), sha
        except Exception:  # noqa: BLE001
            time.sleep(delay * (i + 1))
    return None
