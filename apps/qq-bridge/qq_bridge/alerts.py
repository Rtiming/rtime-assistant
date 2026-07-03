# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""风控/掉线告警 (Goal D2).

The QQ account can be kicked offline by Tencent risk-control ("登录已失效"); when that
happens the bridge cannot notify over QQ itself, so alerts go out-of-band. This module
maps OneBot connection lifecycle + heartbeat ``status.online`` transitions to a single
alerter that always logs + writes a run-log event, and optionally POSTs to an owner
webhook (``QQ_ALERT_WEBHOOK``) — Feishu custom-bot, ntfy, or any endpoint taking
``{"text": ...}``. Re-login runbook: docs/qq-bridge-development.zh-CN.md.
"""

from __future__ import annotations

import logging

import aiohttp
from rtime_chat_runtime.run_log import append_run_event

from .config import QQBridgeConfig

log = logging.getLogger("qq_bridge.alerts")

# event -> (user-facing message, whether it is a problem worth a webhook push)
_MESSAGES: dict[str, tuple[str, bool]] = {
    "connect": ("✅ QQ 桥已连接 NapCat。", False),
    "disconnect": ("⚠️ QQ 桥与 NapCat 的连接断开了（NapCat 重启或网络问题）。", True),
    "offline": (
        "🚨 QQ 账号离线——可能被风控踢下线（“登录已失效”）。"
        "需到 orangepi 重新扫码登录：参见 docs/qq-bridge-development.zh-CN.md 的重登 runbook。",
        True,
    ),
    "online": ("✅ QQ 账号恢复在线。", False),
}


async def _post_webhook(url: str, text: str) -> None:
    """Best-effort out-of-band push. Feishu custom-bot if the URL looks like Feishu/Lark,
    else a generic ``{"text": ...}`` body (ntfy and most webhooks accept it)."""
    low = url.lower()
    if "feishu" in low or "larksuite" in low:
        body = {"msg_type": "text", "content": {"text": text}}
    else:
        body = {"text": text}
    timeout = aiohttp.ClientTimeout(total=10)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, json=body) as resp:
            await resp.read()


def build_lifecycle_alerter(config: QQBridgeConfig):
    """Return an async ``alerter(event, detail)`` for OneBotWSServer.on_lifecycle."""

    async def alerter(event: str, detail: dict | None = None) -> None:
        message, is_problem = _MESSAGES.get(event, (f"QQ 桥事件：{event}", True))
        (log.warning if is_problem else log.info)("lifecycle %s: %s", event, message)
        # NB: append_run_event's first positional param is named ``event`` — pass the
        # lifecycle kind under a different key to avoid a "multiple values" collision.
        append_run_event("qq_lifecycle", entry="qq", lifecycle=event, detail=detail or {})
        if is_problem and config.alert_webhook:
            try:
                await _post_webhook(config.alert_webhook, message)
            except Exception as exc:  # noqa: BLE001 — alerting must never crash the bridge
                log.warning("alert webhook failed: %s", exc)

    return alerter
