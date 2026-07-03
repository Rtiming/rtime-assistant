# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""每天温和地抓取所有订阅公众号的最新文章。由 cron 调用。账号之间歇 40s，防风控。

凭据只从 env 读(WEMP_USERNAME/WEMP_PASSWORD,兼容 WEMP_USER/WEMP_PWD),绝不硬编码
——cron 行里 source 部署机的 env 文件(如 ~/.config/rtime/wemp.env,600)。
"""
import json, os, sys, urllib.request, urllib.parse, time, datetime
B = os.environ.get("WEMP_BASE_URL", "http://127.0.0.1:8001").rstrip("/")

def login():
    user = os.environ.get("WEMP_USERNAME") or os.environ.get("WEMP_USER")
    pwd = os.environ.get("WEMP_PASSWORD") or os.environ.get("WEMP_PWD")
    if not user or not pwd:
        sys.exit("WEMP_USERNAME/WEMP_PASSWORD 未设置(凭据只走 env,不硬编码)")
    r = urllib.request.urlopen(urllib.request.Request(
        B + "/api/v1/wx/auth/login",
        data=urllib.parse.urlencode({"username": user, "password": pwd, "grant_type": "password"}).encode(),
        headers={"Content-Type": "application/x-www-form-urlencoded"}), timeout=30)
    return json.load(r)["data"]["access_token"]

def main():
    tok = login()
    def g(path, timeout=120):
        return urllib.request.urlopen(urllib.request.Request(
            B + path, headers={"Authorization": "Bearer " + tok}), timeout=timeout)
    mps = json.load(g("/api/v1/wx/mps?limit=100"))["data"]["list"]
    print(datetime.datetime.now().isoformat(), f"start, {len(mps)} accounts")
    for m in mps:
        try:
            code = json.load(g("/api/v1/wx/mps/update/" + m["id"])).get("code")
            print("  updated", m.get("mp_name"), "code=", code)
        except Exception as e:
            print("  ERR", m.get("mp_name"), str(e)[:80])
        time.sleep(40)  # 温和：账号之间歇 40s
    print(datetime.datetime.now().isoformat(), "done")

if __name__ == "__main__":
    main()
