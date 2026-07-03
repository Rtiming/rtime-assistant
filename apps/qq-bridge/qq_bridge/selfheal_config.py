# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema-driven config for the QQ self-heal ops sidecar — ``QQSelfhealConfig``.

P2 config 收编 (批 3 · coverage-sweep, see docs/design/config-full-coverage-plan
-2026-07.zh-CN.md §二 批 2 + docs/reference/qq-selfheal-config.zh-CN.md): the
``SELFHEAL_*`` env surface of the offline self-heal daemon (apps/qq-bridge/ops/
qq_selfheal.py) is expressed here as ``QQSelfhealConfig`` — a ``rtime-config``
pydantic-settings model — and REGISTERED into the admin-core registry as module
``qq-selfheal`` so the panel / config-agent can manage it (全覆盖).

Why a SEPARATE module (not inside qq_selfheal.py): the self-heal daemon runs under
the SYSTEM python (``/usr/bin/python3``, see ops/qq-selfheal.service) with a
DELIBERATE stdlib-only footprint (only stdlib + system ``curl``/``docker`` — see
its module docstring). Importing pydantic there would break that deployment
contract. So the daemon keeps its own plain-stdlib ``Config`` class reading env
directly; this schema mirrors those exact env names via ``env_aliases`` so the
coverage guard counts them as covered WITHOUT touching the daemon's hot path. This
is the SAME split the library-gateway uses (a zero-dep runtime leaf whose schema is
owned elsewhere): registration is behaviour-neutral, the runtime is untouched.

BEHAVIOUR-PRESERVING: every field default matches the daemon's ``os.getenv``
default verbatim (a drift here would misreport the panel default, not change
runtime — the daemon never reads this class). Secrets: ``access_token`` and
``feishu_owner_open_id`` are ``secret_field`` (x-secret) — the panel/API only ever
see ``***``. The owner open_id is PII (never in handoffs/logs), so it is treated as
a secret here too. The shared Feishu credentials (``FEISHU_APP_ID`` /
``FEISHU_APP_SECRET`` / ``FEISHU_CONFIG_JSON``) and the QQ access token
(``QQ_ONEBOT_ACCESS_TOKEN``) are OWNED by the feishu / qq modules respectively — NOT
re-registered here, to avoid double-owning a shared name (``access_token`` reuses
the ``QQ_ONEBOT_ACCESS_TOKEN`` alias only so the daemon's read is attributed).
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field, secret_field
from rtime_config.fields import Reload


class QQSelfhealConfig(RtimeBaseSettings):
    # env_prefix="" (not "SELFHEAL_"): every field declares its COMPLETE accepted env
    # name via env_aliases, so the accepted surface equals exactly what is declared
    # (and what x-env-aliases documents) — no implicit prefix-derived names silently
    # widening it. Mirrors QQBridgeConfig / WebChatConfig.
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    status_url: str = config_field(
        default="http://127.0.0.1:3000/get_status",
        description="OneBot get_status 控制端点 URL(轮询 online 状态)。改后重启守护。",
        scope="write:channel",
        env_aliases=["SELFHEAL_STATUS_URL"],
    )
    napcat_container: str = config_field(
        default="qqbr-napcat",
        description="NapCat docker 容器名(掉线时 docker restart 它触发重登)。改后重启。",
        scope="write:channel",
        env_aliases=["SELFHEAL_NAPCAT_CONTAINER"],
    )
    qr_in_container: str = config_field(
        default="/app/napcat/cache/qrcode.png",
        description="容器内二维码图片路径(docker cp 取出来投递飞书)。改后重启。",
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_IN_CONTAINER"],
    )
    qr_host_tmp: str = config_field(
        default="/tmp/qq-selfheal-qr.png",
        description="宿主机侧二维码临时落盘路径(docker cp 目标 → 上传飞书)。改后重启。",
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_HOST_TMP"],
    )
    poll_seconds: int = config_field(
        default=60,
        description="online 状态轮询间隔(秒)。守护主循环每轮读,是热载语义"
        "(当前守护启动时读一次 Config;面板管理时按 hot 生效)。",
        ge=1,
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["SELFHEAL_POLL_SECONDS"],
    )
    offline_confirm_seconds: int = config_field(
        default=120,
        description="连续掉线满多少秒才判定真掉线并动手(防抖,避开瞬断)。改后重启。",
        ge=1,
        scope="write:channel",
        env_aliases=["SELFHEAL_OFFLINE_CONFIRM_SECONDS"],
    )
    cooldown_seconds: int = config_field(
        default=900,
        description="两次自愈(重启 NapCat)之间的最小间隔(秒),防抖动风暴。改后重启。",
        ge=0,
        scope="write:channel",
        env_aliases=["SELFHEAL_COOLDOWN_SECONDS"],
    )
    qr_wait_seconds: int = config_field(
        default=45,
        description="重启后等新二维码/自动 quick-login 恢复的最长等待(秒)。改后重启。",
        ge=1,
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_WAIT_SECONDS"],
    )
    qr_fresh_seconds: int = config_field(
        default=60,
        description="按需补码'新鲜'判定:qrcode.png 距今 ≤ 此秒数才直接发(QQ 登录码"
        "约 2 分钟过期,发旧码=死码)。改后重启。",
        ge=1,
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_FRESH_SECONDS"],
    )
    qr_refresh_wait_seconds: int = config_field(
        default=150,
        description="旧码过期时先等 NapCat 登录界面自刷新码这么久(秒,实测约每 2 分钟"
        "一轮);等不到才重启容器强制出码(重启会作废正被扫的码)。改后重启。",
        ge=1,
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_REFRESH_WAIT_SECONDS"],
    )
    qr_request_check_seconds: int = config_field(
        default=4,
        description="按需补码触发文件的检查间隔(秒);比 online 轮询快,决定"
        "'发一句话到取码'的响应延迟。热载语义(面板管理时 hot 生效)。",
        ge=1,
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_REQUEST_CHECK_SECONDS"],
    )
    qr_request_file: str = config_field(
        default="~/.local/state/rtime-assistant/qq-qr-request",
        description="按需补码触发文件路径(飞书桥写、本守护读的共享文件信号);~ 展开。"
        "必须与飞书桥 RTIME_QQ_QR_REQUEST_FILE 指向同一物理文件。改后重启。",
        scope="write:channel",
        env_aliases=["SELFHEAL_QR_REQUEST_FILE"],
    )
    notify_queue_dir: str = config_field(
        default="~/.local/state/rtime-assistant/notify-queue",
        description="管理员上报队列目录(A3 决策3):渠道 bot 容器里的 rtime-notify-admin 写"
        "请求文件,本守护轮询→用飞书投递→删文件。凭据只在 host,容器零新增密钥。~ 展开。改后重启。",
        scope="write:channel",
        env_aliases=["SELFHEAL_NOTIFY_QUEUE_DIR"],
    )
    feishu_owner_open_id: str = secret_field(
        default="",
        description="owner 飞书 open_id(投递告警/二维码的接收方)。PII:不入 handoff/日志,"
        "面板/API 只见 ***。空 => 只重启不投递。改后重启。",
        scope="write:channel",
        env_aliases=["FEISHU_OWNER_OPEN_ID"],
    )
