# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""get_online 的 cookies 验真回归(2026-07-03 真机缺陷)。

缺陷:账号被踢、快速登录失败落到等扫码后,NapCat get_status 仍报 online=true,
守护发假"✅已恢复在线"后致盲。修法:报在线时再用 get_cookies 验真(被踢=空票据)。
用 stdlib 假 HTTP 服务模拟 NapCat 的四种状态,不碰真容器。
"""

from __future__ import annotations

import http.server
import importlib.util
import json
import threading
from pathlib import Path

_OPS = Path(__file__).resolve().parents[1] / "ops" / "qq_selfheal.py"
_spec = importlib.util.spec_from_file_location("qq_selfheal", _OPS)
qq_selfheal = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(qq_selfheal)


class _FakeNapCat(http.server.BaseHTTPRequestHandler):
    """POST /get_status 与 /get_cookies,行为由 server.scenario 决定。"""

    def do_POST(self):  # noqa: N802 — http.server 命名约定
        scenario = self.server.scenario  # type: ignore[attr-defined]
        if self.path.endswith("/get_status"):
            body = {"status": "ok", "retcode": 0, "data": {"online": scenario["status_online"]}}
        elif self.path.endswith("/get_friend_list"):
            if scenario.get("friend_http_error"):
                self.send_error(500)
                return
            body = scenario["friend_body"]
        else:
            self.send_error(404)
            return
        raw = json.dumps(body).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def log_message(self, *args):  # 静音
        pass


def _run_case(scenario: dict, n_requests: int = 2):
    server = http.server.HTTPServer(("127.0.0.1", 0), _FakeNapCat)
    server.scenario = scenario  # type: ignore[attr-defined]
    threads = [threading.Thread(target=server.handle_request, daemon=True) for _ in range(n_requests)]
    for t in threads:
        t.start()
    cfg = qq_selfheal.Config()
    cfg.status_url = f"http://127.0.0.1:{server.server_address[1]}/get_status"
    cfg.access_token = ""
    try:
        return qq_selfheal.get_online(cfg)
    finally:
        server.server_close()


def test_real_online_friend_list_returns():
    # 真在线:get_status online + get_friend_list 返回列表(可空)
    assert _run_case({
        "status_online": True,
        "friend_body": {"status": "ok", "retcode": 0, "data": [{"user_id": 1}, {"user_id": 2}]},
    }) is True


def test_real_online_with_zero_friends_still_online():
    # 0 好友的号:get_friend_list 返回空列表也算真在线(retcode 0 + data 是 list)
    assert _run_case({
        "status_online": True,
        "friend_body": {"status": "ok", "retcode": 0, "data": []},
    }) is True


def test_fake_online_friend_list_fails_is_offline():
    # 真机缺陷场景:被踢等扫码,get_status 撒谎 online=true 但业务调用失败 → 判离线
    assert _run_case({
        "status_online": True,
        "friend_body": {"status": "failed", "retcode": 1200, "data": None},
    }) is False


def test_status_offline_short_circuits():
    # get_status 直说离线:不需要功能验真(只发一个请求)
    assert _run_case({"status_online": False}, n_requests=1) is False


def test_friend_channel_broken_falls_back_to_status():
    # 验真通道自身 500:回退信 get_status(避免验真挂了误判离线引发重启 churn)
    assert _run_case({
        "status_online": True,
        "friend_http_error": True,
    }) is True


def test_status_unreachable_returns_none():
    cfg = qq_selfheal.Config()
    cfg.status_url = "http://127.0.0.1:1/get_status"  # 关死的端口
    cfg.access_token = ""
    assert qq_selfheal.get_online(cfg) is None


# --- 掉线归因(kick reason 分类) -------------------------------------------
def test_classify_kick_another_terminal():
    reason = "您的账号已在另一台终端登录。如非本人操作，则密码可能已泄露，建议..."
    summary = qq_selfheal.classify_kick(reason)
    assert "另一台终端" in summary and "非风控" in summary


def test_classify_kick_session_invalid():
    assert "失效" in qq_selfheal.classify_kick("你的账号当前登录已失效，请重新登录。")


def test_classify_kick_none_and_frozen():
    assert "未知" in qq_selfheal.classify_kick(None)
    assert "冻结" in qq_selfheal.classify_kick("账号异常，建议紧急冻结账号")


def test_last_kick_reason_parses_ansi_log(monkeypatch):
    # 模拟 docker logs 输出(带 ANSI 色码的 NapCat 踢线行)
    fake_log = (
        "07-04 01:00:00 \x1b[32minfo\x1b[39m 正常\n"
        "07-04 01:05:02 \x1b[31merror\x1b[39m meiyong-bot | "
        "[KickedOffLine] [下线通知] 你的账号当前登录已失效，请重新登录。\n"
    )

    class _R:
        returncode = 0
        stdout = fake_log
        stderr = ""

    monkeypatch.setattr(qq_selfheal, "_docker", lambda *a, **k: _R())
    reason = qq_selfheal.last_kick_reason(qq_selfheal.Config())
    assert reason is not None and "登录已失效" in reason and "\x1b" not in reason
