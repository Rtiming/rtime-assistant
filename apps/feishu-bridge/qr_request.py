# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""按需补码：owner 私聊一句话 → 写触发文件 → qq_selfheal(host)取最新登录码回推飞书。

飞书桥容器**没有 docker 访问**,拿不到 NapCat 的 qrcode.png;只有部署在 host 上的
``qq_selfheal.py`` 守护有(docker cp)。所以按需补码是一条跨容器/主机的文件信号:

  飞书桥(容器,收到 owner 的"补码")→ 往共享 state 目录写触发文件
    → qq_selfheal(host,轮询该文件)→ docker cp 出最新 qrcode.png → 发 owner 飞书

共享通道 = 飞书桥容器把 host ``~/.local/state/rtime-assistant`` 挂到容器
``/var/lib/rtime-assistant``(见 compose.prod.yml),两边是**同一个目录**。触发文件:

  容器视角(飞书桥写):  /var/lib/rtime-assistant/qq-qr-request     (RTIME_QQ_QR_REQUEST_FILE)
  host  视角(守护读):  ~/.local/state/rtime-assistant/qq-qr-request (SELFHEAL_QR_REQUEST_FILE)

两个 env 必须指向**同一物理文件**(一个容器路径、一个 host 路径)。

本模块只做飞书侧的三件小事:识别触发词、判定 owner、写触发文件。不引第三方依赖。
"""

from __future__ import annotations

import json
import os
import re
import time
from collections.abc import Set

# 触发词:qq码 / qq二维码 / 补码 / /qqcode。大小写、中英空格、前后空白宽松。
# 例:"QQ 码"、" 补码 "、"/QQCode"、"qq 二维码" 都命中。
_QR_TRIGGER_RE = re.compile(
    r"^\s*/?\s*(?:qq\s*(?:码|二维码|code)|补码)\s*$",
    re.IGNORECASE,
)

# 容器视角默认路径(飞书桥在容器里跑,HOME=/var/lib/rtime-assistant)。
DEFAULT_QR_REQUEST_FILE = "/var/lib/rtime-assistant/qq-qr-request"


def is_qr_request(text: str) -> bool:
    """文本是否是"补码"触发词(不含 @mention 占位;调用方先剥离)。"""
    if not text:
        return False
    return _QR_TRIGGER_RE.match(text) is not None


def is_owner(user_id: str, admin_users: Set[str]) -> bool:
    """只有 owner(admin)能触发按需补码。admin_users 为空 => 未配置,一律拒。"""
    if not admin_users:
        return False
    return user_id in admin_users


def qr_request_file() -> str:
    """触发文件路径(容器视角)。env RTIME_QQ_QR_REQUEST_FILE 覆盖。"""
    return os.getenv("RTIME_QQ_QR_REQUEST_FILE", DEFAULT_QR_REQUEST_FILE).strip() or DEFAULT_QR_REQUEST_FILE


def write_qr_request(user_id: str, path: str | None = None) -> str:
    """写触发文件(内容=时间戳/请求者,便于 host 侧审计)。返回写入的路径。

    每次覆盖写(内容含新 ts),host 侧用 mtime 去抖识别"这是一条新请求"。
    父目录缺失时创建;写失败向上抛给调用方兜底回复。
    """
    target = path or qr_request_file()
    parent = os.path.dirname(target)
    if parent:
        os.makedirs(parent, exist_ok=True)
    payload = {
        "requested_at": time.time(),
        "requested_at_iso": time.strftime("%Y-%m-%dT%H:%M:%S%z", time.localtime()),
        "requester_open_id": user_id,
        "source": "feishu-bridge",
    }
    with open(target, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    return target
