#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ 掉线自愈守护 (offline self-heal, Goal D 续).

腾讯风控会周期性把专用小号踢下线(``账号状态变更为离线`` / quick-login ``身份已失效``)。
NapCat 被踢后**不会**自动重登、也不会自动出新码——需要人工重启容器触发登录流程再扫码。
本守护把这件事自动化:

  轮询 OneBot ``get_status.online`` → 确认掉线满 confirm 秒(防抖,避开瞬断)
    → ``docker restart`` NapCat 触发登录
      → 若 quick-login 自动回在线:发一条飞书文字告知,无需扫码
      → 若出了新二维码:把二维码图片发到 owner 的飞书 + 附解码 URL 兜底
    → owner 扫码恢复在线后再发一条 "✅ 已恢复"。

投递复用飞书自建应用凭据(与飞书桥同一份 ``feishu.json``)直接走飞书开放平台 HTTP,
不依赖飞书桥容器、不引入第三方 Python 依赖(仅 stdlib + 系统 ``curl``/``docker``)。

封号不可根治(第三方协议端 + 小号的账号侧风控);本守护只把"人工 SSH 取码"降级成
"飞书里扫一下"。配置见 qqbridge.env(SELFHEAL_* 与 FEISHU_OWNER_OPEN_ID)。
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request

log = logging.getLogger("qq_selfheal")

FEISHU_BASE = "https://open.feishu.cn/open-apis"


# --------------------------------------------------------------------------- config
class Config:
    def __init__(self) -> None:
        self.status_url = os.getenv("SELFHEAL_STATUS_URL", "http://127.0.0.1:3000/get_status")
        self.container = os.getenv("SELFHEAL_NAPCAT_CONTAINER", "qqbr-napcat")
        self.qr_in_container = os.getenv("SELFHEAL_QR_IN_CONTAINER", "/app/napcat/cache/qrcode.png")
        self.qr_host_tmp = os.getenv("SELFHEAL_QR_HOST_TMP", "/tmp/qq-selfheal-qr.png")
        self.poll_seconds = int(os.getenv("SELFHEAL_POLL_SECONDS", "60"))
        self.offline_confirm_seconds = int(os.getenv("SELFHEAL_OFFLINE_CONFIRM_SECONDS", "120"))
        self.cooldown_seconds = int(os.getenv("SELFHEAL_COOLDOWN_SECONDS", "900"))
        self.qr_wait_seconds = int(os.getenv("SELFHEAL_QR_WAIT_SECONDS", "45"))
        # 按需补码的"新鲜"判定:qrcode.png 距今 ≤ 此秒数才值得直接发(QQ 登录码约
        # 2 分钟过期,发一张 90s 前的旧码给 owner 大概率扫不上)。
        self.qr_fresh_seconds = int(os.getenv("SELFHEAL_QR_FRESH_SECONDS", "60"))
        # 旧码过期时,先等 NapCat 登录界面**自己**刷新码(实测约每 2 分钟一轮)这么久;
        # 等不到才重启容器强制出码——重启会把 owner 可能正在扫的码作废,留到真卡死。
        self.qr_refresh_wait_seconds = int(os.getenv("SELFHEAL_QR_REFRESH_WAIT_SECONDS", "150"))
        self.access_token = os.getenv("QQ_ONEBOT_ACCESS_TOKEN", "")
        # 按需补码:飞书桥收到 owner "补码" 时写这个触发文件,守护轮询它 -> 取当前最新
        # qrcode.png 回推 owner 飞书。这是飞书桥容器(无 docker)与本守护(有 docker)之间的
        # 共享文件信号。路径必须与飞书桥的 RTIME_QQ_QR_REQUEST_FILE 指向**同一物理文件**:
        #   host  视角(本守护读): ~/.local/state/rtime-assistant/qq-qr-request
        #   容器视角(飞书桥写): /var/lib/rtime-assistant/qq-qr-request
        self.qr_request_file = os.path.expanduser(
            os.getenv(
                "SELFHEAL_QR_REQUEST_FILE",
                "~/.local/state/rtime-assistant/qq-qr-request",
            )
        )
        # 触发文件检查间隔(秒):比 online 轮询(默认 60s)快得多,让"发一句话即取码"够灵敏。
        self.qr_request_check_seconds = int(os.getenv("SELFHEAL_QR_REQUEST_CHECK_SECONDS", "4"))
        # 管理员上报队列目录(A3 决策3):qq-bridge 容器里的模型调 rtime-notify-admin 往这里
        # 写请求文件(feishu_selfheal 通道),本守护轮询→用 notify_text 发飞书→删文件。
        # 复用本守护已有的飞书凭据,容器零新增密钥(同 qr-request 机制)。
        self.notify_queue_dir = os.path.expanduser(
            os.getenv(
                "SELFHEAL_NOTIFY_QUEUE_DIR",
                "~/.local/state/rtime-assistant/notify-queue",
            )
        )
        # Feishu delivery
        self.feishu_config_json = os.path.expanduser(
            os.getenv("FEISHU_CONFIG_JSON", "~/.config/rtime-assistant/feishu.json")
        )
        self.owner_open_id = os.getenv("FEISHU_OWNER_OPEN_ID", "")

    def feishu_credentials(self) -> tuple[str, str]:
        app_id = os.getenv("FEISHU_APP_ID")
        app_secret = os.getenv("FEISHU_APP_SECRET")
        if app_id and app_secret:
            return app_id, app_secret
        with open(self.feishu_config_json, encoding="utf-8") as f:
            data = json.load(f)
        app_id = data.get("appId") or data.get("app_id")
        app_secret = data.get("appSecret") or data.get("app_secret")
        if not (app_id and app_secret):
            raise RuntimeError(f"feishu.json 缺 appId/appSecret: {self.feishu_config_json}")
        return app_id, app_secret


# --------------------------------------------------------------------------- OneBot / docker
def _http_json(url: str, body: dict, headers: dict | None = None, timeout: int = 8) -> dict:
    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method="POST")
    req.add_header("Content-Type", "application/json")
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _auth_headers(cfg: Config) -> dict:
    return {"Authorization": f"Bearer {cfg.access_token}"} if cfg.access_token else {}


def _functional_online(cfg: Config) -> bool | None:
    """get_status 报在线之后的**功能性验真**:拉好友列表(get_friend_list)。

    真机实锤(2026-07-04):
    - get_status.online 被踢后会残留 True(缓存),单信它会致盲(假在线);
    - **但 get_cookies 反过来:真在线时也常返回空票据(NapCat 状态/cookies 不一致,
      社区已知),单信它会把真在线误判成离线 → selfheal 反复重启 napcat → churn 换
      device 标识 → 触发"新设备"验证 + 真踢线。我上一版用 get_cookies 就踩了这个坑。**
    正确的验真是**发一个需要真实会话的业务调用**:get_friend_list 只有真在线才返回
    retcode=0 + 列表;被踢/等扫码时拿不到或报错。以此为准(比 cookies/status 都可靠)。
    返回 True(真在线)/False(假在线)/None(验真通道不可用,由调用方回退信 status)。
    """
    url = cfg.status_url.rsplit("/", 1)[0] + "/get_friend_list"
    try:
        payload = _http_json(url, {}, headers=_auth_headers(cfg), timeout=10)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("get_friend_list unreachable: %s", exc)
        return None
    if payload.get("retcode") != 0 or payload.get("status") not in ("ok", "async"):
        return False  # 业务调用失败 = 没有有效会话(被踢/等扫码)
    return isinstance(payload.get("data"), list)  # 返回列表(可空)= 真在线


def get_online(cfg: Config) -> bool | None:
    """True/False online state, or None when the control port is unreachable.

    两段:①get_status(快,但被踢后会残留 online=true)②说在线时再用
    **get_friend_list 功能性验真**(需真实会话,比 get_cookies 可靠——后者真在线时
    也常空,会误判离线引发重启 churn)。验真通道不可用时回退信 get_status。
    """
    try:
        payload = _http_json(cfg.status_url, {}, headers=_auth_headers(cfg), timeout=6)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        log.debug("get_status unreachable: %s", exc)
        return None
    data = payload.get("data") or {}
    val = data.get("online")
    if not isinstance(val, bool):
        return None
    if not val:
        return False
    verified = _functional_online(cfg)
    if verified is None:
        return True  # 验真不可用:回退 get_status 的说法
    if not verified:
        log.warning("get_status 报在线但 get_friend_list 失败 — 假在线(等扫码/被踢)")
    return verified


def _docker(*args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, timeout=timeout
    )


# 掉线原因分类(供 owner 判断是"另一台终端抢登录"还是"会话失效/风控"):
# NapCat 踢线日志形如 `[KickedOffLine] [下线通知] <原因>`。
_KICK_RE = re.compile(r"KickedOffLine.*?\]\s*(.+?)(?:\x1b|$)")
_ANOTHER_TERMINAL = "已在另一台终端登录"


def last_kick_reason(cfg: Config, since: str = "10m") -> str | None:
    """从 NapCat 近期日志抓最后一条踢线原因文本(去 ANSI 色码);无则 None。

    这是掉线归因的关键信号:'已在另一台终端登录'=有别的设备用同一 QQ 登录(非风控,
    owner 该去下线那台设备);'登录已失效'=会话失效(重扫/密码回退)。"""
    r = _docker("logs", cfg.container, "--since", since, timeout=15)
    if r.returncode != 0:
        return None
    text = (r.stdout or "") + (r.stderr or "")
    hits = _KICK_RE.findall(text)
    if not hits:
        return None
    reason = re.sub(r"\x1b\[[0-9;]*m", "", hits[-1]).strip()
    return reason or None


def classify_kick(reason: str | None) -> str:
    """归因摘要(给飞书通知加一行,让 owner 立刻知道该怎么处理)。"""
    if not reason:
        return "原因未知(NapCat 日志无踢线记录,可能是崩溃/网络断而非被踢)"
    if _ANOTHER_TERMINAL in reason:
        return "另一台终端用同一 QQ 登录把它挤下线了(非风控)——请检查你的手机/电脑 QQ 登录设备并下线多余的"
    if "失效" in reason:
        return "会话已失效(需重扫码;配了密码回退则自动恢复)"
    if "冻结" in reason or "异常" in reason:
        return "账号被判异常/建议冻结——请人工确认账号安全"
    return reason


def restart_napcat(cfg: Config) -> None:
    log.info("restarting napcat container %s", cfg.container)
    r = _docker("restart", cfg.container, timeout=90)
    if r.returncode != 0:
        raise RuntimeError(f"docker restart failed: {r.stderr.strip()}")


def qr_mtime(cfg: Config) -> float:
    """Epoch mtime of the QR file inside the container, or 0 if absent."""
    r = _docker("exec", cfg.container, "stat", "-c", "%Y", cfg.qr_in_container, timeout=15)
    if r.returncode != 0:
        return 0.0
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def copy_qr_out(cfg: Config) -> str:
    r = _docker("cp", f"{cfg.container}:{cfg.qr_in_container}", cfg.qr_host_tmp, timeout=20)
    if r.returncode != 0:
        raise RuntimeError(f"docker cp qr failed: {r.stderr.strip()}")
    return cfg.qr_host_tmp


def qr_decode_url(cfg: Config) -> str:
    """Best-effort: pull the human-readable 解码URL from recent napcat logs (text fallback)."""
    r = _docker("logs", cfg.container, "--since", "90s", timeout=15)
    blob = (r.stdout or "") + (r.stderr or "")
    for line in reversed(blob.splitlines()):
        if "txz.qq.com" in line:
            idx = line.find("https://")
            if idx != -1:
                return line[idx:].strip()
    return ""


# --------------------------------------------------------------------------- Feishu delivery
def _feishu_token(cfg: Config) -> str:
    app_id, app_secret = cfg.feishu_credentials()
    payload = _http_json(
        f"{FEISHU_BASE}/auth/v3/tenant_access_token/internal",
        {"app_id": app_id, "app_secret": app_secret},
        timeout=8,
    )
    token = payload.get("tenant_access_token")
    if not token:
        raise RuntimeError(f"飞书取 token 失败: {payload}")
    return token


def _feishu_upload_image(token: str, path: str) -> str:
    # multipart via curl — stdlib multipart is verbose/fragile; curl is always present here.
    r = subprocess.run(
        [
            "curl", "-s", "-m", "20",
            "-H", f"Authorization: Bearer {token}",
            "-F", "image_type=message",
            "-F", f"image=@{path}",
            f"{FEISHU_BASE}/im/v1/images",
        ],
        capture_output=True, text=True, timeout=30,
    )
    try:
        payload = json.loads(r.stdout)
    except ValueError as exc:
        raise RuntimeError(f"飞书上传图片响应非JSON: {r.stdout[:200]}") from exc
    key = (payload.get("data") or {}).get("image_key")
    if not key:
        raise RuntimeError(f"飞书上传图片失败: {payload}")
    return key


def _feishu_send(token: str, open_id: str, msg_type: str, content: dict) -> None:
    payload = _http_json(
        f"{FEISHU_BASE}/im/v1/messages?receive_id_type=open_id",
        {"receive_id": open_id, "msg_type": msg_type,
         "content": json.dumps(content, ensure_ascii=False)},
        headers={"Authorization": f"Bearer {token}"},
        timeout=10,
    )
    if payload.get("code") not in (0, None):
        raise RuntimeError(f"飞书发送({msg_type})失败: {payload}")


def notify_text(cfg: Config, text: str) -> None:
    if not cfg.owner_open_id:
        log.warning("FEISHU_OWNER_OPEN_ID 未设置,跳过飞书文字告警: %s", text)
        return
    try:
        token = _feishu_token(cfg)
        _feishu_send(token, cfg.owner_open_id, "text", {"text": text})
        log.info("feishu text sent: %s", text)
    except Exception as exc:  # noqa: BLE001 — 告警失败不能拖垮守护
        log.warning("飞书文字告警失败: %s", exc)


def send_qr(cfg: Config, qr_path: str, decode_url: str, *, caption: str | None = None) -> None:
    if not cfg.owner_open_id:
        log.warning("FEISHU_OWNER_OPEN_ID 未设置,无法投递二维码")
        return
    token = _feishu_token(cfg)
    image_key = _feishu_upload_image(token, qr_path)
    _feishu_send(token, cfg.owner_open_id, "image", {"image_key": image_key})
    # caption 按触发场景给准确措辞:自动自愈=被踢下线;按需补码不许谎称"被踢"。
    caption = caption or (
        "🚨 QQ 小号被风控踢下线,已生成新登录二维码(上图)。\n"
        "用小号手机QQ→扫一扫→授权登录,约2分钟内有效。"
    )
    if decode_url:
        caption += f"\n扫不动可用备用解码URL生成二维码:\n{decode_url}"
    _feishu_send(token, cfg.owner_open_id, "text", {"text": caption})
    log.info("feishu QR delivered to owner")


# --------------------------------------------------------------------------- heal
# 最近一次重启 NapCat 的时刻(auto heal 与按需补码共享,防两条路径互踩重启风暴——
# 重启会作废 owner 正在扫的码)。
LAST_HEAL_TS = 0.0


def heal(cfg: Config) -> None:
    """Restart napcat, then deliver a QR (or confirm auto-recovery) to the owner."""
    global LAST_HEAL_TS
    log.warning("offline confirmed — healing")
    reason = last_kick_reason(cfg)
    log.warning("kick reason: %s", reason or "(none in log)")
    notify_text(
        cfg,
        "🚨 QQ 小号掉线,正在自动重启 NapCat 取新登录码…\n"
        f"📋 归因:{classify_kick(reason)}",
    )
    restart_ts = time.time()
    LAST_HEAL_TS = restart_ts
    try:
        restart_napcat(cfg)
    except Exception as exc:  # noqa: BLE001
        log.error("重启失败: %s", exc)
        notify_text(cfg, f"⚠️ 自动重启 NapCat 失败,请人工检查:{exc}")
        return

    deadline = time.time() + cfg.qr_wait_seconds
    while time.time() < deadline:
        time.sleep(4)
        if get_online(cfg) is True:
            log.info("auto quick-login recovered")
            notify_text(cfg, "✅ NapCat 已自动 quick-login 恢复在线,无需扫码。")
            return
        if qr_mtime(cfg) >= restart_ts:
            try:
                path = copy_qr_out(cfg)
                send_qr(cfg, path, qr_decode_url(cfg))
            except Exception as exc:  # noqa: BLE001
                log.error("投递二维码失败: %s", exc)
                notify_text(cfg, f"⚠️ 生成了二维码但投递飞书失败,请人工到 orangepi 取码:{exc}")
            return
    log.warning("重启后既未恢复也没出二维码")
    notify_text(cfg, "⚠️ NapCat 重启后既没自动恢复、也没出二维码,请人工检查。")


# --------------------------------------------------------------------------- on-demand QR
def _qr_request_mtime(path: str) -> float:
    """触发文件的 mtime,不存在返回 0(无请求)。"""
    try:
        return os.stat(path).st_mtime
    except OSError:
        return 0.0


def handle_qr_request(cfg: Config, last_seen_mtime: float) -> float:
    """检查按需补码触发文件:存在且是新请求(mtime 变化)则取当前最新码回推 owner。

    去抖:用触发文件 mtime 判定"这是不是上次已处理过的同一个请求",避免删除失败/
    重复轮询时重复发送。返回**下一次调用应传入的 last_seen_mtime**(已处理的 mtime,
    或没有请求时原样返回 last_seen_mtime)。发送/删除失败都不抛(不能拖垮守护)。
    """
    path = cfg.qr_request_file
    mtime = _qr_request_mtime(path)
    if mtime == 0.0:
        return last_seen_mtime  # 没有请求文件
    if mtime == last_seen_mtime:
        # 同一个请求还没被删掉(上轮删除失败),已处理过 -> 尽力再删一次,不重复发。
        try:
            os.remove(path)
        except OSError:
            pass
        return last_seen_mtime

    log.info("on-demand QR request detected (mtime=%s)", mtime)
    # 先删触发文件:即便下面发送失败,也不会因为文件还在而一直重复触发;失败会记日志。
    try:
        os.remove(path)
    except OSError as exc:
        log.warning("删除触发文件失败(将靠 mtime 去抖防重复): %s", exc)
    try:
        _deliver_fresh_qr(cfg)
    except Exception as exc:  # noqa: BLE001 — 投递失败不能拖垮守护
        log.error("按需补码投递失败: %s", exc)
        notify_text(cfg, f"⚠️ 收到补码请求,但取新码失败,请人工检查:{exc}")
    return mtime


ONDEMAND_QR_CAPTION = (
    "🔑 按需补码:最新登录二维码(上图)。\n"
    "用小号手机QQ→扫一扫→授权登录,约2分钟内有效;扫完等我在飞书报「已恢复在线」。"
)


def _deliver_fresh_qr(cfg: Config) -> None:
    """按需补码的正确语义:发一张**扫得上**的码,而不是容器里现存的旧码。

    owner 要码几乎总是因为旧码已过期(QQ 登录码约 2 分钟失效),所以三档:
    ①在线 => 告知免扫;②现存码仍新鲜 => 直接发;③过期/没码 => 先等 NapCat
    登录界面的自刷(登录态下它约每 2 分钟自己换码),等不到才重启容器强制出码
    ——重启会作废 owner 可能正在扫的码,必须留到登录流真卡死时。
    """
    global LAST_HEAL_TS
    if get_online(cfg) is True:
        notify_text(cfg, "✅ QQ 小号当前在线,无需扫码。")
        return
    start_ts = time.time()
    m = qr_mtime(cfg)
    if m and start_ts - m <= cfg.qr_fresh_seconds:
        send_qr(cfg, copy_qr_out(cfg), qr_decode_url(cfg), caption=ONDEMAND_QR_CAPTION)
        log.info("on-demand QR delivered (existing fresh code, age=%.0fs)", start_ts - m)
        return
    notify_text(cfg, "⏳ 现存码已过期,正在取新登录码(最多约 2-3 分钟,取到即发)…")
    # 第一档:等 NapCat 自己刷出新码(不重启)。
    deadline = start_ts + cfg.qr_refresh_wait_seconds
    while time.time() < deadline:
        time.sleep(4)
        if get_online(cfg) is True:
            notify_text(cfg, "✅ QQ 小号已在线,无需扫码。")
            return
        if qr_mtime(cfg) >= start_ts:
            send_qr(cfg, copy_qr_out(cfg), qr_decode_url(cfg), caption=ONDEMAND_QR_CAPTION)
            log.info("on-demand QR delivered (self-refreshed)")
            return
    # 第二档:登录流没在自刷(卡死/未进登录态),重启强制出码。
    log.info("on-demand: no self-refreshed QR in %ss; restarting napcat", cfg.qr_refresh_wait_seconds)
    restart_ts = time.time()
    restart_napcat(cfg)
    LAST_HEAL_TS = restart_ts
    deadline = restart_ts + cfg.qr_wait_seconds
    while time.time() < deadline:
        time.sleep(4)
        if get_online(cfg) is True:
            notify_text(cfg, "✅ NapCat 已自动 quick-login 恢复在线,无需扫码。")
            return
        if qr_mtime(cfg) >= restart_ts:
            send_qr(cfg, copy_qr_out(cfg), qr_decode_url(cfg), caption=ONDEMAND_QR_CAPTION)
            log.info("on-demand QR delivered (after restart)")
            return
    notify_text(cfg, "⚠️ 重启 NapCat 后仍未生成二维码,请人工检查。")


def handle_notify_queue(cfg: Config) -> int:
    """A3 决策3:发送管理员上报队列里的所有请求文件到飞书,发完删除。返回发送条数。

    每个请求是一个独立 .json 文件(容器原子写入),本函数扫描→发→删,天然支持多条、
    无并发写读竞争。任一条失败只记日志、留文件下轮重试,不拖垮守护。"""
    d = cfg.notify_queue_dir
    try:
        names = sorted(n for n in os.listdir(d) if n.endswith(".json") and not n.startswith("."))
    except OSError:
        return 0  # 队列目录不存在=无请求
    sent = 0
    for name in names:
        path = os.path.join(d, name)
        try:
            with open(path, encoding="utf-8") as fh:
                text = str((json.load(fh) or {}).get("text") or "").strip()
        except (OSError, ValueError) as exc:
            log.warning("notify 队列坏文件 %s(删除): %s", name, exc)
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        if not text:
            try:
                os.remove(path)
            except OSError:
                pass
            continue
        try:
            notify_text(cfg, text)
            sent += 1
            os.remove(path)  # 发成功才删
        except Exception as exc:  # noqa: BLE001 — 发送失败留文件下轮重试,不拖垮守护
            log.error("notify 上报投递失败(留待重试): %s", exc)
            break  # 飞书暂时不可用就别继续刷,下个 tick 再来
    return sent


def watch(cfg: Config) -> None:
    log.info(
        "self-heal watching: poll=%ss confirm=%ss cooldown=%ss qr_check=%ss "
        "container=%s owner=%s qr_request_file=%s",
        cfg.poll_seconds, cfg.offline_confirm_seconds, cfg.cooldown_seconds,
        cfg.qr_request_check_seconds, cfg.container,
        (cfg.owner_open_id[:5] + "…") if cfg.owner_open_id else "(unset)",
        cfg.qr_request_file,
    )
    last_online: bool | None = None
    consecutive_offline = 0
    healed_this_episode = False
    needed_polls = max(1, cfg.offline_confirm_seconds // max(1, cfg.poll_seconds))

    # 短 tick 检查触发文件,每 online_every 个 tick 才查一次 online(省 HTTP/docker 调用)。
    tick = max(1, cfg.qr_request_check_seconds)
    online_every = max(1, cfg.poll_seconds // tick)
    tick_count = 0
    # 启动时把已存在的旧触发文件的 mtime 当作"已处理",避免重启守护时误发一次陈旧请求。
    last_qr_mtime = _qr_request_mtime(cfg.qr_request_file)

    while True:
        # 每个短 tick 都查按需补码触发文件(灵敏)。
        last_qr_mtime = handle_qr_request(cfg, last_qr_mtime)
        # 同 tick 顺带发管理员上报队列(A3 决策3)。
        handle_notify_queue(cfg)

        if tick_count % online_every == 0:
            online = get_online(cfg)
            if online is None:
                log.debug("status unreachable this tick")
            elif online:
                if last_online is False:
                    notify_text(cfg, "✅ QQ 小号已恢复在线。")
                consecutive_offline = 0
                healed_this_episode = False
                last_online = True
            else:  # offline
                consecutive_offline += 1
                log.info("offline tick %d/%d", consecutive_offline, needed_polls)
                now = time.time()
                # 冷却基于共享 LAST_HEAL_TS(heal 与按需补码都会更新):按需刚重启过,
                # 自动路径就不要立刻再重启一次把 owner 正在扫的码作废。
                if (not healed_this_episode
                        and consecutive_offline >= needed_polls
                        and now - LAST_HEAL_TS >= cfg.cooldown_seconds):
                    heal(cfg)
                    healed_this_episode = True
                last_online = False

        tick_count += 1
        time.sleep(tick)


# --------------------------------------------------------------------------- CLI
def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="QQ 掉线自愈守护")
    p.add_argument("--test-send", action="store_true",
                   help="用当前 napcat 里的二维码测试飞书投递链路(不重启、不改状态),验证凭据/连通/open_id")
    p.add_argument("--test-text", metavar="MSG", help="给 owner 发一条飞书测试文字后退出")
    p.add_argument("--once", action="store_true", help="只跑一轮检查(掉线则执行一次 heal)后退出")
    p.add_argument("--check-qr-request", action="store_true",
                   help="只检查一次按需补码触发文件(存在则取当前码发飞书+删文件)后退出")
    p.add_argument("--check-notify-queue", action="store_true",
                   help="只发一次管理员上报队列(存在则发飞书+删文件)后退出")
    p.add_argument("-v", "--verbose", action="store_true")
    args = p.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    cfg = Config()

    if args.test_text is not None:
        notify_text(cfg, args.test_text)
        return 0

    if args.test_send:
        log.info("test-send: 复制当前二维码并投递飞书…")
        path = copy_qr_out(cfg)
        send_qr(cfg, path, qr_decode_url(cfg))
        return 0

    if args.check_qr_request:
        log.info("check-qr-request: 检查触发文件 %s", cfg.qr_request_file)
        handle_qr_request(cfg, last_seen_mtime=0.0)
        return 0

    if args.check_notify_queue:
        log.info("check-notify-queue: 发送队列 %s", cfg.notify_queue_dir)
        n = handle_notify_queue(cfg)
        log.info("check-notify-queue: 发送 %d 条", n)
        return 0

    if args.once:
        online = get_online(cfg)
        log.info("once: online=%s", online)
        if online is False:
            heal(cfg)
        return 0

    try:
        watch(cfg)
    except KeyboardInterrupt:
        return 0
    return 0


if __name__ == "__main__":
    sys.exit(main())
