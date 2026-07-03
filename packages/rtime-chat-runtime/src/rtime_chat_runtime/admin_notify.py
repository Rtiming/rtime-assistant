# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""管理员通知/上报的通道无关分发器(A3 决策3)。

owner:"做个工具,助手结合情况看要不要给管理员发消息,飞书/QQ给管理员/邮箱等等"。
这是给渠道 bot 的模型自主调用的**上报**能力:当遇到答不了、需人工、疑似问题、值得
管理员知道的情况时,模型调 deploy/bin/rtime-notify-admin,消息经此分发到配置的通道。

设计要点:
- **通道无关 + 可插拔**:通道由 env ``RTIME_ADMIN_NOTIFY``(JSON 数组)配置,每项
  ``{"type": feishu_webhook|webhook|email, ...}``;加一种通道=注册一个 backend 函数。
- **best-effort**:任一通道失败不影响其他,返回每通道结果;未配置=明确告知(no-op)。
- **stdlib-only**(本包零依赖叶子):urllib + smtplib。
- **隐私**:消息正文是模型自拟的摘要(不是库原文);但仍不把正文写进 service log
  (CLI 只回元数据)。凭据(webhook URL/SMTP 口令)走 env,不入 git、不回显。

QQ-给管理员通道需要在线桥的发送路径(工具是独立子进程,够不到运行中的 WS),
留作扩展点(type=qq → 写共享触发文件给桥,类比 rtime-qq-code);本模块先实现
feishu_webhook / webhook / email 三种即时可用的。
"""

from __future__ import annotations

import json
import os
import secrets
import smtplib
import time
import urllib.error
import urllib.request
from email.mime.text import MIMEText
from typing import Any, Callable

CONFIG_ENV = "RTIME_ADMIN_NOTIFY"
_TIMEOUT = 8


def _post_json(url: str, payload: dict[str, Any], timeout: int = _TIMEOUT) -> tuple[bool, str]:
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 — url from owner env, not user
            body = resp.read(4096).decode("utf-8", errors="replace")
        return True, f"http {resp.status}: {body[:120]}"
    except (urllib.error.URLError, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _fmt(summary: str, reason: str, urgency: str, source: str) -> str:
    tag = {"high": "🔴", "normal": "🟡", "low": "⚪"}.get(urgency, "🟡")
    lines = [f"{tag} 助手上报({source})"]
    if reason:
        lines.append(f"事由:{reason}")
    lines.append(f"内容:{summary}")
    return "\n".join(lines)


# ------------------------------------------------------------------ backends
def _send_feishu_webhook(cfg: dict[str, Any], text: str) -> tuple[bool, str]:
    """飞书自定义机器人 incoming webhook(单 URL,无需 app secret)。"""
    url = str(cfg.get("url") or "").strip()
    if not url:
        return False, "feishu_webhook: missing url"
    return _post_json(url, {"msg_type": "text", "content": {"text": text}})


def _send_generic_webhook(cfg: dict[str, Any], text: str) -> tuple[bool, str]:
    """通用 webhook:把结构化字段 POST 过去(接自建后端/其他 IM)。"""
    url = str(cfg.get("url") or "").strip()
    if not url:
        return False, "webhook: missing url"
    return _post_json(url, {"text": text, "source": "rtime-admin-notify"})


def _send_email(cfg: dict[str, Any], text: str) -> tuple[bool, str]:
    """SMTP 邮件到管理员邮箱。cfg: host/port/user/password/to[/from/tls]。"""
    host = str(cfg.get("host") or "").strip()
    to = cfg.get("to")
    if not host or not to:
        return False, "email: missing host/to"
    to_list = [to] if isinstance(to, str) else list(to)
    msg = MIMEText(text, "plain", "utf-8")
    msg["Subject"] = str(cfg.get("subject") or "助手上报")
    sender = str(cfg.get("from") or cfg.get("user") or "rtime-assistant")
    msg["From"] = sender
    msg["To"] = ", ".join(to_list)
    try:
        port = int(cfg.get("port") or 587)
        server = smtplib.SMTP(host, port, timeout=_TIMEOUT)
        try:
            if cfg.get("tls", True):
                server.starttls()
            user = cfg.get("user")
            if user:
                server.login(str(user), str(cfg.get("password") or ""))
            server.sendmail(sender, to_list, msg.as_string())
        finally:
            server.quit()
        return True, f"email sent to {len(to_list)} recipient(s)"
    except (smtplib.SMTPException, OSError, ValueError) as exc:
        return False, f"{type(exc).__name__}: {exc}"


def _send_qq(cfg: dict[str, Any], text: str) -> tuple[bool, str]:
    """QQ 给管理员:扩展点。工具是独立子进程,够不到在线桥的 WS,需类比
    rtime-qq-code 写共享触发文件让桥转发。首期未接线,明确返回 not-implemented。"""
    return False, "qq channel not wired yet (needs bridge trigger-file integration)"


def _send_feishu_selfheal(cfg: dict[str, Any], text: str) -> tuple[bool, str]:
    """复用 host 上 qq_selfheal 守护已在工作的飞书投递(它有飞书凭据+owner open_id)。

    容器里够不到飞书 app secret,也不该给容器加密钥。这里只往共享队列目录写一个
    请求文件(与 rtime-qq-code 写 qr-request 同一机制),host 的 selfheal 轮询该目录、
    用自己的 notify_text 发飞书、发完删文件。凭据只在 host,容器零新增密钥。
    queue_dir 容器视角默认 /var/lib/rtime-assistant/notify-queue(= host
    ~/.local/state/rtime-assistant/notify-queue,同一物理目录)。"""
    queue_dir = str(cfg.get("queue_dir") or "/var/lib/rtime-assistant/notify-queue").strip()
    try:
        os.makedirs(queue_dir, exist_ok=True)
        name = f"{int(time.time() * 1000)}-{secrets.token_hex(4)}.json"
        tmp = os.path.join(queue_dir, f".{name}.tmp")
        final = os.path.join(queue_dir, name)
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump({"text": text, "requested_at": time.time()}, fh, ensure_ascii=False)
        os.replace(tmp, final)  # 原子出现,selfheal 不会读到半个文件
        return True, "queued for feishu delivery via selfheal"
    except OSError as exc:
        return False, f"feishu_selfheal queue write failed: {exc}"


_BACKENDS: dict[str, Callable[[dict[str, Any], str], tuple[bool, str]]] = {
    "feishu_webhook": _send_feishu_webhook,
    "feishu_selfheal": _send_feishu_selfheal,
    "webhook": _send_generic_webhook,
    "email": _send_email,
    "qq": _send_qq,
}


def load_channels(raw: str | None = None) -> list[dict[str, Any]]:
    """从 env(或传入串)解析通道配置数组。坏 JSON / 非数组 => 空(no-op)。"""
    text = (raw if raw is not None else os.getenv(CONFIG_ENV, "")).strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except ValueError:
        return []
    if isinstance(data, dict):  # 容忍单对象
        data = [data]
    return [c for c in data if isinstance(c, dict) and c.get("type") in _BACKENDS]


def notify_admin(
    summary: str,
    *,
    reason: str = "",
    urgency: str = "normal",
    source: str = "assistant",
    channels: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """向配置的所有通道分发一条上报。best-effort;返回每通道结果(无正文)。"""
    chans = channels if channels is not None else load_channels()
    text = _fmt(summary.strip(), reason.strip(), urgency, source)
    results: list[dict[str, Any]] = []
    for cfg in chans:
        ctype = str(cfg.get("type"))
        backend = _BACKENDS.get(ctype)
        if backend is None:
            results.append({"type": ctype, "ok": False, "detail": "unknown channel type"})
            continue
        ok, detail = backend(cfg, text)
        results.append({"type": ctype, "ok": ok, "detail": detail})
    delivered = sum(1 for r in results if r["ok"])
    return {
        "ok": delivered > 0,
        "configured_channels": len(chans),
        "delivered": delivered,
        "results": results,
        "no_channel": len(chans) == 0,
    }
