# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
飞书 × Claude Code Bot
通过飞书 WebSocket 长连接接收私聊/群聊消息，调用本机 claude CLI 回复，支持流式卡片输出。

启动：python main.py
"""

import asyncio
import json
import logging
import sys
import os
import re
import threading
import time
import traceback
from http.server import HTTPServer, BaseHTTPRequestHandler
from types import SimpleNamespace

# 确保项目目录在 sys.path 最前面
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _shared_runtime  # noqa: E402,F401 — side effect: put rtime_chat_runtime on sys.path

import lark_oapi as lark
from lark_oapi.api.im.v1.model import P2ImMessageReceiveV1
from lark_oapi.event.callback.model.p2_card_action_trigger import (
    P2CardActionTrigger, P2CardActionTriggerResponse, CallBackToast,
)

import bot_config as config
from bridge_runner import run_and_display
from card_callbacks import parse_card_action, toast_for_action, warning_toast, Toast
from feishu_client import FeishuClient
from session_store import SessionStore, generate_summary, _write_custom_title
from commands import parse_command, handle_command
from claude_runner import run_claude
import qr_request
from rtime_chat_runtime.chat_queue import (
    PendingChatMessage,
    get_chat_debounce_queue,
    get_chat_lock,
    merge_pending_messages,
)
from handover_service import handle_handover_request
from rtime_chat_runtime.run_control import ActiveRun, ActiveRunRegistry, stop_run
from rtime_chat_runtime.access_policy import is_allowed_actor
from rtime_chat_runtime.archive import make_archive_func
from latency_trace import mark, mark_once, start_trace
from output_policy import (
    extract_options,
    format_tool,
    segmented_output_enabled,
    show_tool_calls,
)
from tool_policy import (
    IMAGE_ALLOWED_TOOLS,
    add_runtime_policy_hints,
    allowed_tools_for_text,
    disallowed_tools_for_text,
)

LARK_SDK_LOG_LEVEL = lark.LogLevel.WARNING
_SENSITIVE_QUERY_RE = re.compile(r"(?i)(access_key|ticket)=([^&\s]+)")


def _redact_log_text(value):
    if not isinstance(value, str):
        return value
    return _SENSITIVE_QUERY_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", value)


def _install_log_redaction():
    """Redact known SDK URL tokens before Python logging formats records."""
    current_factory = logging.getLogRecordFactory()
    if getattr(current_factory, "_rtime_feishu_redaction", False):
        return

    def record_factory(*args, **kwargs):
        record = current_factory(*args, **kwargs)
        record.msg = _redact_log_text(record.msg)
        if isinstance(record.args, dict):
            record.args = {key: _redact_log_text(val) for key, val in record.args.items()}
        elif isinstance(record.args, tuple):
            record.args = tuple(_redact_log_text(val) for val in record.args)
        elif record.args:
            record.args = _redact_log_text(record.args)
        return record

    record_factory._rtime_feishu_redaction = True
    logging.setLogRecordFactory(record_factory)


_install_log_redaction()

# ── 看门狗：定时重启防止 WebSocket 假死 ──────────────────────

DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS = 4 * 3600


def _load_watchdog_max_uptime_seconds() -> float:
    raw = os.getenv("WATCHDOG_MAX_UPTIME_SECONDS")
    if raw is None or raw.strip() == "":
        return float(DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS)
    try:
        value = float(raw)
    except ValueError:
        print(
            f"[watchdog] invalid WATCHDOG_MAX_UPTIME_SECONDS={raw!r}; "
            f"using default {DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS}",
            flush=True,
        )
        return float(DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS)
    return max(0.0, value)


WATCHDOG_MAX_UPTIME_SECONDS = _load_watchdog_max_uptime_seconds()
_start_time = time.time()
_last_event = time.time()


def _watchdog():
    """后台线程，定期检查进程健康。异常时退出让 launchctl 拉起。"""
    while True:
        time.sleep(300)  # 每 5 分钟检查
        uptime = time.time() - _start_time
        idle = time.time() - _last_event

        if WATCHDOG_MAX_UPTIME_SECONDS > 0 and uptime > WATCHDOG_MAX_UPTIME_SECONDS:
            print(f"[watchdog] 运行 {uptime/3600:.1f}h，定时重启刷新连接", flush=True)
            os._exit(0)

        if WATCHDOG_MAX_UPTIME_SECONDS > 0:
            limit = f" limit={WATCHDOG_MAX_UPTIME_SECONDS/3600:.1f}h"
        else:
            limit = " forced-restart=disabled"
        print(f"[watchdog] uptime={uptime/3600:.1f}h idle={idle/60:.0f}min{limit}", flush=True)


# ── 全局单例 ──────────────────────────────────────────────────

# 独立的 asyncio 事件循环，启动时即就绪，不依赖 lark SDK 的首条消息
_bot_loop: asyncio.AbstractEventLoop = asyncio.new_event_loop()


def _start_bot_loop():
    asyncio.set_event_loop(_bot_loop)
    _bot_loop.run_forever()


threading.Thread(target=_start_bot_loop, daemon=True, name="bot-loop").start()

lark_client = lark.Client.builder() \
    .app_id(config.FEISHU_APP_ID) \
    .app_secret(config.FEISHU_APP_SECRET) \
    .log_level(LARK_SDK_LOG_LEVEL) \
    .build()

feishu = FeishuClient(lark_client, app_id=config.FEISHU_APP_ID, app_secret=config.FEISHU_APP_SECRET)
store = SessionStore()
_active_runs = ActiveRunRegistry()

# per-chat 消息队列锁，保证同一群组的消息串行处理，允许不同群组并发处理
_chat_locks: dict[str, asyncio.Lock] = {}
_MAX_CHAT_LOCKS = 200  # 防止无界增长
_chat_debounce_queues = {}
_MAX_CHAT_DEBOUNCE_QUEUES = 200
_FILE_MESSAGE_TYPES = {"file", "media", "audio"}
_FILE_TYPE_LABELS = {
    "file": "文件",
    "media": "视频",
    "audio": "语音",
}
_TEXT_MESSAGE_TYPES = {"text", "post"}


def _health_payload() -> dict:
    """Return a non-sensitive process health payload for Docker healthchecks."""
    now = time.time()
    return {
        "ok": True,
        "service": "feishu-bridge",
        "uptime_seconds": int(now - _start_time),
        "idle_seconds": int(now - _last_event),
        "watchdog_max_uptime_seconds": int(WATCHDOG_MAX_UPTIME_SECONDS),
        "watchdog_forced_restart_enabled": WATCHDOG_MAX_UPTIME_SECONDS > 0,
        "chat_lock_count": len(_chat_locks),
        "debounce_queue_count": len(_chat_debounce_queues),
        "debounce_pending_count": sum(len(queue) for queue in _chat_debounce_queues.values()),
        "debounce_active_count": sum(1 for queue in _chat_debounce_queues.values() if queue.worker_active),
    }


# ── /stop 命令处理 ───────────────────────────────────────────

async def _announce_stopped_run(active_run: ActiveRun):
    try:
        await feishu.update_card(active_run.card_msg_id, "⏹ 已停止当前任务")
    except Exception as exc:
        print(f"[warn] update stopped card failed: {exc}", flush=True)


async def _announce_interrupted(active_run: ActiveRun):
    try:
        await feishu.update_card(active_run.card_msg_id, "⏹ 已被新消息打断")
    except Exception:
        pass


async def _handle_qr_request_command(user_id: str) -> str:
    """按需补码:owner 触发词命中后写触发文件,host 上的 qq_selfheal 守护会取最新码回推。

    只被 owner(ADMIN_USERS)触发(调用方已判定);写文件失败时给出可读兜底提示。
    """
    try:
        path = qr_request.write_qr_request(user_id)
        print(f"[qr] owner={user_id[:8]}... 触发按需补码,已写触发文件 {path}", flush=True)
        return "⏳ 正在生成最新 QQ 二维码,稍等几秒发你。若一直没来,说明 NapCat 侧没出码,可稍后再试或人工检查。"
    except Exception as exc:  # noqa: BLE001 — 写文件失败也要给用户一个可读回复
        print(f"[qr] 写触发文件失败: {type(exc).__name__}: {exc}", flush=True)
        return f"❌ 触发补码失败(写共享文件出错):{exc}"


async def _handle_stop_command(sender_open_id: str) -> str:
    active_run = _active_runs.get_run(sender_open_id)
    if active_run is None:
        return "当前没有正在运行的任务"
    if active_run.stop_requested:
        return "正在停止当前任务，请稍候"
    stopped = await stop_run(
        _active_runs,
        sender_open_id,
        on_stopped=_announce_stopped_run,
    )
    if not stopped:
        return "当前没有正在运行的任务"
    return "已发送停止请求"


# ── 命令菜单（锁外即时响应）──────────────────────────────────

_COMMAND_MENU_GROUPS = [
    ("**会话**", [
        {"text": "🆕 新会话",      "value": {"action": "run_cmd", "cmd": "/new"}},
        {"text": "📋 新会话(规划)", "value": {"action": "run_cmd", "cmd": "/new plan"}},
        {"text": "📂 恢复会话",    "value": {"action": "run_cmd", "cmd": "/resume"}},
        {"text": "⏹ 停止任务",     "value": {"action": "run_cmd", "cmd": "/stop"}},
    ]),
    ("**配置**", [
        {"text": "🔄 切模型",      "value": {"action": "run_cmd", "cmd": "/model"}},
        {"text": "⚙️ 切模式",      "value": {"action": "run_cmd", "cmd": "/mode"}},
        {"text": "📁 工作空间",    "value": {"action": "run_cmd", "cmd": "/ws"}},
    ]),
    ("**查看**", [
        {"text": "📊 状态",        "value": {"action": "run_cmd", "cmd": "/status"}},
        {"text": "📈 用量",        "value": {"action": "run_cmd", "cmd": "/usage"}},
        {"text": "🛠 Skills",      "value": {"action": "run_cmd", "cmd": "/skills"}},
        {"text": "🔌 MCP",         "value": {"action": "run_cmd", "cmd": "/mcp"}},
        {"text": "📄 目录",        "value": {"action": "run_cmd", "cmd": "/ls"}},
        {"text": "❓ 帮助",        "value": {"action": "run_cmd", "cmd": "/help"}},
    ]),
]


async def _show_command_menu(user_id: str, chat_id: str, is_group: bool, msg_id: str):
    """显示分组命令菜单（markdown 标题 + 按钮混排），不走队列锁"""
    elements = []
    for title, buttons in _COMMAND_MENU_GROUPS:
        elements.append({"tag": "markdown", "content": title})
        columns = []
        for btn in buttons:
            value = {**btn["value"], "cid": chat_id}
            columns.append({
                "tag": "column",
                "width": "auto",
                "elements": [{
                    "tag": "button",
                    "text": {"tag": "plain_text", "content": btn["text"]},
                    "type": "default",
                    "size": "small",
                    "name": f"menu_{btn['value']['cmd'].replace('/', '').replace(' ', '_')}",
                    "value": value,
                    "behaviors": [{"type": "callback", "value": value}],
                }],
            })
        elements.append({"tag": "column_set", "flex_mode": "flow", "columns": columns})
    try:
        if is_group:
            card_id = await feishu.reply_card(msg_id, content="⚡ 快捷命令", loading=False)
        else:
            card_id = await feishu.send_card_to_user(user_id, content="⚡ 快捷命令", loading=False)
        await feishu.update_card_elements(card_id, elements)
    except Exception as e:
        print(f"[error] 命令菜单发送失败: {e}", flush=True)


# ── 核心消息处理（async）─────────────────────────────────────


def _segmented_output_enabled() -> bool:
    return segmented_output_enabled(getattr(config, "OUTPUT_STYLE", "segmented"))


def _show_tool_calls() -> bool:
    return show_tool_calls(getattr(config, "SHOW_TOOL_CALLS", False))


def _qq_code_tool_allowed_for_actor(user_id: str, is_group: bool) -> bool:
    return (not is_group) and qr_request.is_owner(
        user_id,
        getattr(config, "ADMIN_USERS", set()),
    )


def _is_allowed_actor(user_id: str, chat_id: str, is_group: bool) -> bool:
    return is_allowed_actor(
        user_id,
        chat_id,
        is_group,
        getattr(config, "ALLOWED_USERS", set()),
        getattr(config, "ALLOWED_CHATS", set()),
    )


def extract_chat_info(event: P2ImMessageReceiveV1) -> tuple[str, str, bool]:
    """
    Extract user_id, chat_id, and is_group from message event.

    Returns:
        (user_id, chat_id, is_group)
        - For private chat: chat_id = user_id
        - For group chat: chat_id = group's chat_id
    """
    sender = event.event.sender
    user_id = sender.sender_id.open_id

    message = event.event.message
    chat_type = message.chat_type
    chat_id_raw = message.chat_id

    is_group = (chat_type == "group")

    if is_group:
        chat_id = chat_id_raw
    else:
        chat_id = user_id

    return user_id, chat_id, is_group


def _message_mentions(msg) -> list:
    """Return Feishu mentions as a plain list; test doubles may expose Mock objects."""
    mentions = getattr(msg, "mentions", None)
    if mentions is None:
        return []
    if isinstance(mentions, list):
        return mentions
    if isinstance(mentions, tuple):
        return list(mentions)
    try:
        return list(mentions)
    except TypeError:
        return []


def _is_text_like_message(msg) -> bool:
    return getattr(msg, "message_type", "") in _TEXT_MESSAGE_TYPES


def _post_segment_text(segment) -> str:
    if isinstance(segment, str):
        return segment
    if isinstance(segment, list):
        return "".join(_post_segment_text(item) for item in segment)
    if not isinstance(segment, dict):
        return ""

    tag = str(segment.get("tag") or "")
    if tag in ("text", "a"):
        return str(segment.get("text") or "")
    if tag == "at":
        return str(
            segment.get("text")
            or segment.get("user_name")
            or segment.get("name")
            or ""
        )
    if isinstance(segment.get("text"), str):
        return segment["text"]
    if "content" in segment:
        return _post_content_text(segment.get("content"))
    return ""


def _post_content_text(content) -> str:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return ""

    paragraphs: list[str] = []
    for paragraph in content:
        if isinstance(paragraph, list):
            text = "".join(_post_segment_text(segment) for segment in paragraph).strip()
        else:
            text = _post_segment_text(paragraph).strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _post_text_from_content(content: dict) -> str:
    candidates: list[dict] = []
    post = content.get("post")
    if isinstance(post, dict):
        for value in post.values():
            if isinstance(value, dict):
                candidates.append(value)
    candidates.append(content)

    for candidate in candidates:
        body = _post_content_text(candidate.get("content"))
        if body.strip():
            return body.strip()
        title = candidate.get("title")
        if isinstance(title, str) and title.strip():
            return title.strip()
    return ""


def _message_text(msg) -> str:
    if msg.message_type not in _TEXT_MESSAGE_TYPES:
        return ""
    content = _message_content(msg)
    if msg.message_type == "text":
        return str(content.get("text") or "").strip()
    return _post_text_from_content(content).strip()


def _routing_text(msg, is_group: bool) -> str:
    if not _is_text_like_message(msg):
        return ""
    text = _message_text(msg)
    if is_group:
        for mention in _message_mentions(msg):
            key = getattr(mention, "key", "")
            if key:
                text = text.replace(key, "").strip()
    return text


def _message_debounce_seconds() -> float:
    return max(0.0, float(getattr(config, "MESSAGE_DEBOUNCE_SECONDS", 0) or 0))


def _message_debounce_max_messages() -> int:
    return max(1, int(getattr(config, "MESSAGE_DEBOUNCE_MAX_MESSAGES", 20) or 20))


def _message_debounce_max_chars() -> int:
    return max(0, int(getattr(config, "MESSAGE_DEBOUNCE_MAX_CHARS", 12000) or 0))


def _debounceable_text(msg, is_group: bool) -> str:
    text = _routing_text(msg, is_group)
    if not text:
        return ""
    if parse_command(text):
        return ""
    return text


def _message_content(msg) -> dict:
    try:
        value = json.loads(getattr(msg, "content", "") or "{}")
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


async def _send_visible_reply(user_id: str, is_group: bool, message_id: str, content: str) -> str:
    if is_group:
        return await feishu.reply_card(message_id, content=content, loading=False)
    return await feishu.send_card_to_user(user_id, content=content, loading=False)


async def _send_visible_text(user_id: str, is_group: bool, message_id: str, text: str) -> str:
    if is_group:
        return await feishu.reply_text(message_id, text)
    return await feishu.send_text_to_user(user_id, text)


def _make_debounced_text_message(item: PendingChatMessage):
    raw = item.raw_message
    msg = SimpleNamespace(
        chat_id=getattr(raw, "chat_id", item.chat_id),
        chat_type="group" if item.is_group else "p2p",
        message_type="text",
        content=json.dumps({"text": item.text}, ensure_ascii=False),
        message_id=item.message_id,
        mentions=[],
    )
    trace = getattr(raw, "_rtime_trace", None)
    if trace is not None:
        setattr(msg, "_rtime_trace", trace)
    return msg


async def _enqueue_debounced_message(
    user_id: str,
    chat_id: str,
    is_group: bool,
    msg,
    text: str,
) -> None:
    queue = get_chat_debounce_queue(
        chat_id,
        _chat_debounce_queues,
        _MAX_CHAT_DEBOUNCE_QUEUES,
    )
    pending_count = queue.append(
        PendingChatMessage(
            user_id=user_id,
            chat_id=chat_id,
            is_group=is_group,
            message_id=msg.message_id,
            text=text,
            raw_message=msg,
        )
    )
    trace = getattr(msg, "_rtime_trace", None)
    mark(trace, "debounce_queued", pending_count=pending_count)
    if queue.worker_active:
        print(f"[debounce] queued chat={chat_id[:8]}... pending={pending_count}", flush=True)
        return

    queue.worker_active = True
    try:
        while True:
            delay = _message_debounce_seconds()
            if delay > 0:
                mark(trace, "debounce_wait_start", debounce_seconds=delay)
                await asyncio.sleep(delay)
                mark(trace, "debounce_wait_done", debounce_seconds=delay)

            batch = queue.drain(_message_debounce_max_messages())
            if not batch:
                return

            merged, overflow = merge_pending_messages(
                batch,
                max_chars=_message_debounce_max_chars(),
            )
            batch_trace = getattr(batch[0].raw_message, "_rtime_trace", trace)
            mark(batch_trace, "debounce_merged", batch_count=len(batch), overflow_chars=overflow)
            if len(batch) > 1 or overflow:
                print(
                    f"[debounce] merged chat={chat_id[:8]}... "
                    f"count={len(batch)} overflow={overflow}",
                    flush=True,
                )

            lock = get_chat_lock(chat_id, _chat_locks, _MAX_CHAT_LOCKS)
            mark(batch_trace, "queue_wait_start", queue="chat_lock")
            async with lock:
                mark(batch_trace, "queue_acquired", queue="chat_lock")
                try:
                    await _process_message(
                        merged.user_id,
                        merged.chat_id,
                        merged.is_group,
                        _make_debounced_text_message(merged),
                    )
                except Exception as exc:
                    print(f"[error] debounce batch failed: {type(exc).__name__}: {exc}", flush=True)
                    traceback.print_exc(file=sys.stdout)
                    sys.stdout.flush()
    finally:
        queue.worker_active = False


async def handle_message_async(event: P2ImMessageReceiveV1):
    """异步处理一条飞书消息"""
    msg = event.event.message
    print(f"[收到消息] type={msg.message_type} chat={msg.chat_type}", flush=True)

    # Extract chat info (supports both private and group chats)
    user_id, chat_id, is_group = extract_chat_info(event)
    trace = start_trace(
        user_id=user_id,
        chat_id=chat_id,
        is_group=is_group,
        message_type=msg.message_type,
        chat_type=msg.chat_type,
    )
    setattr(msg, "_rtime_trace", trace)
    mark(trace, "webhook_received")
    print(f"[Chat Info] user={user_id[:8]}... chat={chat_id[:8]}... is_group={is_group}", flush=True)
    if not _is_allowed_actor(user_id, chat_id, is_group):
        mark(trace, "access_ignored")
        print(f"[access] ignored user={user_id[:8]}... chat={chat_id[:8]}...", flush=True)
        return

    # /stop 和 / 在锁外处理（不需要排队等 Claude）
    if _is_text_like_message(msg):
        _text = _routing_text(msg, is_group)

        if _text.lower() in ("/stop", "/stop") or _text.strip().endswith("/stop"):
            mark(trace, "command_immediate_start", command="stop")
            reply = await _handle_stop_command(user_id)
            if is_group:
                await feishu.reply_card(msg.message_id, content=reply, loading=False)
            else:
                await feishu.send_card_to_user(user_id, content=reply, loading=False)
            mark(trace, "command_immediate_done", command="stop")
            return

        # 单独输入 / → 显示命令菜单（按钮）
        if _text == "/":
            mark(trace, "command_immediate_start", command="menu")
            await _show_command_menu(user_id, chat_id, is_group, msg.message_id)
            mark(trace, "command_immediate_done", command="menu")
            return

        # 按需补码：owner 私聊发 "补码/qq码/qq二维码//qqcode" → 不进模型,
        # 写触发文件让 host 上的 qq_selfheal 守护取最新登录码回推飞书。
        # 只在私聊(is_group=False)且发送者是 owner(ADMIN_USERS)时拦截;
        # 非 owner 或群聊里的同样文本照常走后续流程(交给模型)。
        if (not is_group
                and qr_request.is_qr_request(_text)
                and qr_request.is_owner(user_id, getattr(config, "ADMIN_USERS", set()))):
            mark(trace, "command_immediate_start", command="qq_qr")
            reply = await _handle_qr_request_command(user_id)
            await feishu.send_card_to_user(user_id, content=reply, loading=False)
            mark(trace, "command_immediate_done", command="qq_qr")
            return

    # 群聊只响应 @机器人 的消息
    if is_group and getattr(config, "REQUIRE_MENTION_IN_GROUP", True):
        mentions = _message_mentions(msg)
        if not mentions:
            return  # 没有 @mention，忽略

    debounce_text = _debounceable_text(msg, is_group)
    if debounce_text and _message_debounce_seconds() > 0:
        await _enqueue_debounced_message(user_id, chat_id, is_group, msg, debounce_text)
        return

    # 获取该群组的队列锁，保证同一群组消息串行处理，不同群组可并发
    lock = get_chat_lock(chat_id, _chat_locks, _MAX_CHAT_LOCKS)

    mark(trace, "queue_wait_start", queue="chat_lock")
    async with lock:
        mark(trace, "queue_acquired", queue="chat_lock")
        try:
            await _process_message(user_id, chat_id, is_group, msg)
        except Exception as e:
            print(f"[error] 消息处理异常: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(file=sys.stdout)
            sys.stdout.flush()


async def _run_and_display(
    user_id: str, chat_id: str, is_group: bool,
    text: str, card_msg_id: str, session, notify_msg_id: str,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    latency_trace: dict | None = None,
):
    """调用 Claude 并展示结果。消息处理和按钮回复共用此薄 wrapper。"""
    return await run_and_display(
        user_id=user_id,
        chat_id=chat_id,
        is_group=is_group,
        text=text,
        card_msg_id=card_msg_id,
        session=session,
        notify_msg_id=notify_msg_id,
        feishu=feishu,
        store=store,
        active_runs=_active_runs,
        run_claude_func=run_claude,
        stream_chunk_size=config.STREAM_CHUNK_SIZE,
        segmented_output=_segmented_output_enabled(),
        show_tool_calls=_show_tool_calls(),
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        latency_trace=latency_trace,
        status_heartbeat_seconds=config.STATUS_HEARTBEAT_SECONDS,
    )


async def _process_message(user_id: str, chat_id: str, is_group: bool, msg):
    """实际处理消息的逻辑，在 per-chat lock 保护下执行"""
    print(f"[处理消息] user={user_id[:8]}... chat={chat_id[:8]}... is_group={is_group}", flush=True)
    text = ""
    img_path = None
    card_msg_id = None
    allowed_tools = None
    disallowed_tools = None
    trace = getattr(msg, "_rtime_trace", None)
    mark(trace, "processing_started")

    if _is_text_like_message(msg):
        text = _routing_text(msg, is_group)
        if not text:
            return

        print(f"[文本] {text[:50]}", flush=True)
        allow_qq_code = _qq_code_tool_allowed_for_actor(user_id, is_group)
        allowed_tools = allowed_tools_for_text(text, allow_qq_code=allow_qq_code)
        disallowed_tools = disallowed_tools_for_text(text)
        text = add_runtime_policy_hints(text, allow_qq_code=allow_qq_code)

    elif msg.message_type == "image":
        try:
            mark(trace, "image_received")
            try:
                card_msg_id = await _send_visible_reply(
                    user_id,
                    is_group,
                    msg.message_id,
                    "📷 已收到图片，正在下载…",
                )
                mark_once(trace, "placeholder_sent", "placeholder_sent", message_type="image")
            except Exception as status_err:
                print(f"[warn] 图片占位卡片发送失败: {status_err}", flush=True)
            image_key = str(_message_content(msg).get("image_key", "")).strip()
            if not image_key:
                await _send_visible_text(user_id, is_group, msg.message_id, "❌ 收到图片消息，但飞书事件里没有 image_key，暂时无法下载。")
                return
            mark(trace, "image_download_start")
            img_path = await feishu.download_image(msg.message_id, image_key)
            mark(trace, "image_download_done")
            if card_msg_id:
                await feishu.update_card(card_msg_id, "📷 图片已下载，正在交给模型…")
                mark(trace, "first_card_update", operation="update_card", stage_note="image_downloaded")
            text = f"[用户发送了一张图片，路径：{img_path}，请读取并分析这张图片，直接回复用中文]"
            allowed_tools = list(IMAGE_ALLOWED_TOOLS)
            disallowed_tools = disallowed_tools_for_text(text)
        except Exception as e:
            print(f"[error] 下载图片失败: {e}")
            try:
                await _send_visible_text(user_id, is_group, msg.message_id, f"❌ 下载图片失败：{e}")
            except Exception:
                pass
            return

    elif msg.message_type in _FILE_MESSAGE_TYPES:
        label = _FILE_TYPE_LABELS.get(msg.message_type, "附件")
        try:
            content = _message_content(msg)
            file_key = str(content.get("file_key") or content.get("key") or "").strip()
            file_name = str(content.get("file_name") or content.get("name") or "").strip()
            if not file_key:
                await _send_visible_text(
                    user_id,
                    is_group,
                    msg.message_id,
                    f"❌ 收到{label}消息，但飞书事件里没有 file_key，暂时无法下载。",
                )
                return

            mark(trace, "file_received", message_type=msg.message_type)
            try:
                display_name = f"：{file_name}" if file_name else ""
                card_msg_id = await _send_visible_reply(
                    user_id,
                    is_group,
                    msg.message_id,
                    f"📎 已收到{label}{display_name}，正在下载…",
                )
                mark_once(trace, "placeholder_sent", "placeholder_sent", message_type=msg.message_type)
            except Exception as status_err:
                print(f"[warn] {label}占位卡片发送失败: {status_err}", flush=True)

            mark(trace, "file_download_start", message_type=msg.message_type)
            file_path = await feishu.download_file(msg.message_id, file_key, file_name)
            mark(trace, "file_download_done", message_type=msg.message_type)
            if card_msg_id:
                await feishu.update_card(card_msg_id, f"📎 {label}已下载，正在交给模型…")
                mark(trace, "first_card_update", operation="update_card", stage_note="file_downloaded")
            name_part = f"，原始文件名：{file_name}" if file_name else ""
            text = (
                f"[用户发送了一个{label}{name_part}，本地路径：{file_path}。"
                "请先检查文件类型和内容，再根据用户意图处理；如果格式无法直接读取，"
                "请明确说明需要转换或让用户补充说明。直接用中文回复。]"
            )
            disallowed_tools = disallowed_tools_for_text(text)
        except Exception as e:
            print(f"[error] 下载{label}失败: {e}")
            try:
                await _send_visible_text(user_id, is_group, msg.message_id, f"❌ 下载{label}失败：{e}")
            except Exception:
                pass
            return

    else:
        mark(trace, "unsupported_message_type", message_type=msg.message_type)
        await _send_visible_text(
            user_id,
            is_group,
            msg.message_id,
            f"暂时还不能处理这种飞书消息类型：{msg.message_type}。请发文字、图片或文件。",
        )
        return

    # ── 斜杠命令 ──────────────────────────────────────────────
    parsed = parse_command(text)
    if parsed:
        cmd, args = parsed
        mark(trace, "command_start", command=cmd)
        print(f"[cmd] 执行命令 {cmd}", flush=True)
        reply = await handle_command(cmd, args, user_id, chat_id, store)
        print(f"[cmd] 命令返回 type={type(reply).__name__}", flush=True)
        if reply is not None:
            if isinstance(reply, dict):
                reply_text, reply_buttons = reply["text"], reply.get("buttons", [])
            else:
                reply_text, reply_buttons = reply, []

            if reply_buttons:
                if is_group:
                    card_id = await feishu.reply_card(msg.message_id, content=reply_text, loading=False)
                else:
                    card_id = await feishu.send_card_to_user(user_id, content=reply_text, loading=False)
                print(f"[按钮] 卡片已发送 card_id={card_id}, 准备添加 {len(reply_buttons)} 个按钮", flush=True)
                try:
                    short = all(len(b["text"]) <= 12 for b in reply_buttons)
                    await feishu.update_card_with_buttons(card_id, reply_text, reply_buttons, flow=short)
                    print("[按钮] 按钮添加成功", flush=True)
                except Exception as btn_err:
                    print(f"[按钮] 按钮添加失败: {btn_err}", flush=True)
            else:
                if is_group:
                    await feishu.reply_card(msg.message_id, content=reply_text, loading=False)
                else:
                    await feishu.send_card_to_user(user_id, content=reply_text, loading=False)
            mark(trace, "command_done", command=cmd)
            return
        # reply is None → 不是 bot 命令，当作普通消息（含 /xxx）转发给 Claude

    # ── 普通消息 → 调用 Claude ──────────────────────────────
    session = await store.get_current(user_id, chat_id)
    print(f"[Claude] session={session.session_id} model={session.model}", flush=True)

    # 1. 发送"思考中"占位卡片，拿到 message_id
    if card_msg_id is None:
        try:
            mark(trace, "placeholder_send_start")
            if is_group:
                card_msg_id = await feishu.reply_card(msg.message_id, loading=True)
            else:
                card_msg_id = await feishu.send_card_to_user(user_id, loading=True)
            mark_once(trace, "placeholder_sent", "placeholder_sent", message_type=msg.message_type)
            print(f"[卡片] card_msg_id={card_msg_id}", flush=True)
        except Exception as e:
            mark(trace, "placeholder_send_failed", error_type=type(e).__name__)
            print(f"[error] 发送占位卡片失败: {e}", flush=True)
            if is_group:
                try:
                    await feishu.reply_card(msg.message_id, content=f"❌ 发送消息失败：{e}", loading=False)
                except Exception:
                    pass
            else:
                await feishu.send_text_to_user(user_id, f"❌ 发送消息失败：{e}")
            return

    await _run_and_display(
        user_id,
        chat_id,
        is_group,
        text,
        card_msg_id,
        session,
        msg.message_id,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        latency_trace=trace,
    )


def _extract_options(text: str) -> list[tuple[str, str]]:
    """从文本中提取选项，适配 Claude Code 原生输出格式。返回 [(按钮文字, 回复值), ...]"""
    return extract_options(text)


def _format_tool(name: str, inp: dict) -> str:
    """格式化工具调用的进度提示"""
    return format_tool(name, inp)


# ── 飞书事件回调（同步）→ 调度异步任务 ───────────────────────

# ── 卡片按钮点击处理（选项选择）──────────────────────────────

def _run_intake_approve(tool: str, sha: str) -> bool:
    """Owner clicked '批准入库' → run the owner-only ``rtime-{tool} approve <sha>``, which writes
    the ``<sha>.approved`` token. Reached only after ``_is_allowed_actor`` confirms the clicker is
    the owner, so this is a trusted owner action, not an agent self-approve."""
    if not sha or len(sha) != 16 or any(c not in "0123456789abcdef" for c in sha):
        return False
    import subprocess
    from pathlib import Path
    exe = "rtime-course-intake" if tool == "course-intake" else "rtime-finalize"
    # In the container the repo is bind-mounted at /workspace/rtime-assistant; on the host
    # it is ~/rtime-assistant. Search candidate roots for the approve writer.
    candidates = [os.environ.get("RTIME_ASSISTANT_ROOT"), "/workspace/rtime-assistant",
                  "/app", str(Path.home() / "rtime-assistant")]
    for root in candidates:
        if not root:
            continue
        bin_path = os.path.join(root, "deploy", "bin", exe)
        if not os.path.isfile(bin_path):
            continue
        try:
            result = subprocess.run([sys.executable, bin_path, "approve", sha],
                                    capture_output=True, text=True, timeout=30)
            return result.returncode == 0
        except Exception:
            return False
    return False


def on_card_action(data: P2CardActionTrigger) -> P2CardActionTriggerResponse:
    """用户点击卡片按钮：选项回复 or 模式切换"""
    global _last_event
    _last_event = time.time()

    event = data.event
    user_id = event.operator.open_id
    clicked_msg_id = event.context.open_message_id if event.context else None
    action = parse_card_action(user_id, event.action.value or {}, clicked_msg_id)

    if not _is_allowed_actor(action.user_id, action.chat_id, action.chat_id != action.user_id):
        return _card_response(warning_toast("无权限"))

    # 入库审批按钮：owner 点击 = 受信任的 owner approve（写 <sha>.approved 令牌；agent 拿不到飞书，无法触发）
    raw_value = event.action.value if isinstance(event.action.value, dict) else {}
    if raw_value.get("action") == "intake-approve":
        sha = str(raw_value.get("sha", ""))
        tool = str(raw_value.get("tool", "finalize"))
        ok = _run_intake_approve(tool, sha)
        # Update the card itself so the owner sees a persistent "已批准" state — a toast alone
        # is transient, which is why repeated taps looked like nothing happened.
        new_content = (f"✅ **已批准** `{sha}`\n\nagent 现在可以入库了（apply）。"
                       if ok else
                       f"⚠️ **批准失败** `{sha}`\n\n请用命令行：`rtime-{tool} approve {sha}`")
        if clicked_msg_id:
            try:
                asyncio.run_coroutine_threadsafe(feishu.update_card(clicked_msg_id, new_content), _bot_loop)
            except Exception:
                pass
        return _card_response(Toast("success", "已批准 ✅") if ok else warning_toast("批准失败，请用命令行 approve"))

    # 模式切换按钮
    if action.action_type == "set_mode":
        if action.mode:
            asyncio.run_coroutine_threadsafe(
                _handle_set_mode(action.user_id, action.chat_id, action.mode, action.clicked_msg_id),
                _bot_loop,
            )
        return _card_response(toast_for_action(action))

    # 命令菜单按钮 → 当作用户发了一条命令消息
    if action.action_type == "run_cmd":
        if action.cmd_text:
            asyncio.run_coroutine_threadsafe(
                _handle_menu_command(action.user_id, action.chat_id, action.cmd_text, action.clicked_msg_id),
                _bot_loop,
            )
        return _card_response(toast_for_action(action))

    # 恢复会话按钮
    if action.action_type == "resume_session":
        if action.session_id:
            asyncio.run_coroutine_threadsafe(
                _handle_resume_session(action.user_id, action.chat_id, action.session_id, action.clicked_msg_id),
                _bot_loop,
            )
        return _card_response(toast_for_action(action))

    # 选项回复按钮（发给 Claude）
    if action.reply_text:
        print(f"[按钮] user={action.user_id[:8]}... reply={action.reply_text}", flush=True)
        asyncio.run_coroutine_threadsafe(
            _handle_button_reply(action.user_id, action.chat_id, action.reply_text, action.clicked_msg_id),
            _bot_loop,
        )

    return _card_response(toast_for_action(action))


def _card_response(toast_data) -> P2CardActionTriggerResponse:
    resp = P2CardActionTriggerResponse()
    toast = CallBackToast()
    toast.type = toast_data.type
    toast.content = toast_data.content
    resp.toast = toast
    return resp


async def _handle_menu_command(user_id: str, chat_id: str, cmd_text: str, card_msg_id: str):
    """命令菜单按钮点击 → 执行命令并更新卡片"""
    parsed = parse_command(cmd_text)
    if not parsed:
        return
    cmd, args = parsed

    # /stop 特殊处理
    if cmd == "stop":
        reply_text = await _handle_stop_command(user_id)
        if card_msg_id:
            try:
                await feishu.update_card(card_msg_id, reply_text)
            except Exception:
                pass
        return

    reply = await handle_command(cmd, args, user_id, chat_id, store)
    if reply is None:
        return

    if isinstance(reply, dict):
        reply_text, reply_buttons = reply["text"], reply.get("buttons", [])
    else:
        reply_text, reply_buttons = reply, []

    if card_msg_id:
        try:
            if reply_buttons:
                short = all(len(b["text"]) <= 12 for b in reply_buttons)
                await feishu.update_card_with_buttons(card_msg_id, reply_text, reply_buttons, flow=short)
            else:
                await feishu.update_card(card_msg_id, reply_text)
        except Exception as e:
            print(f"[error] 菜单命令卡片更新失败: {e}", flush=True)


async def _handle_resume_session(user_id: str, chat_id: str, session_id: str, card_msg_id: str):
    """卡片按钮恢复历史会话"""
    sid, old_title = await store.resume_session(user_id, chat_id, session_id)
    if not sid:
        print(f"[resume] 未找到 session: {session_id[:8]}", flush=True)
        return
    print(f"[resume] 已恢复 session: {sid[:8]}", flush=True)
    if card_msg_id:
        try:
            name = store.get_summary(user_id, sid) or f"#{sid[:8]}"
            text = f"✅ 已恢复会话「{name}」，继续对话吧。"
            if old_title:
                text += f"\n上个会话：「{old_title}」"
            await feishu.update_card(card_msg_id, text)
        except Exception:
            pass


async def _handle_set_mode(user_id: str, chat_id: str, mode: str, card_msg_id: str):
    """卡片按钮切换权限模式"""
    from commands import VALID_MODES
    await store.set_permission_mode(user_id, chat_id, mode)
    desc = VALID_MODES.get(mode, "")
    print(f"[模式切换] user={user_id[:8]}... mode={mode}", flush=True)
    if card_msg_id:
        try:
            await feishu.update_card(card_msg_id, f"✅ 已切换为 **{mode}**\n{desc}")
        except Exception:
            pass


async def _handle_button_reply(user_id: str, chat_id: str, text: str, clicked_msg_id: str):
    """按钮点击 → 走正常的 lock + Claude 流程"""
    is_group = (chat_id != user_id)

    lock = get_chat_lock(chat_id, _chat_locks, _MAX_CHAT_LOCKS)

    async with lock:
        try:
            session = await store.get_current(user_id, chat_id)
            try:
                if is_group and clicked_msg_id:
                    card_msg_id = await feishu.reply_card(clicked_msg_id, loading=True)
                else:
                    card_msg_id = await feishu.send_card_to_user(user_id, loading=True)
            except Exception as e:
                print(f"[error] 按钮回复占位卡片失败: {e}", flush=True)
                return
            await _run_and_display(
                user_id, chat_id, is_group, text,
                card_msg_id, session, clicked_msg_id or "",
                disallowed_tools=disallowed_tools_for_text(text),
            )
        except Exception as e:
            print(f"[error] 按钮回复处理异常: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(file=sys.stdout)


# ── 飞书事件回调（同步）→ 调度异步任务 ───────────────────────

# A1.5 通道无关归档(design chat-archive-storage):飞书入站事件在任何业务处理之前
# 先落 raw envelope。root 未配置 => None => 行为与历史完全一致(零归档)。
_archive = make_archive_func(config.ARCHIVE_ROOT, "feishu", config.ARCHIVE_MODE)


def _event_raw(data) -> dict:
    """P2ImMessageReceiveV1 → dict(原样序列化)。序列化失败绝不抛——退化为最小元数据。"""
    try:
        return json.loads(lark.JSON.marshal(data))
    except Exception:  # noqa: BLE001 — 归档序列化永不破坏消息链路
        event_id = ""
        try:
            event_id = str(getattr(getattr(data, "header", None), "event_id", "") or "")
        except Exception:  # noqa: BLE001
            pass
        return {"unserializable": type(data).__name__, "event_id": event_id}


def on_message_receive(data: P2ImMessageReceiveV1) -> None:
    """飞书 SDK 同步回调，调度异步任务到 _bot_loop。"""
    global _last_event
    _last_event = time.time()
    if _archive is not None:
        _archive(_event_raw(data))  # 归档先于一切处理(与 QQ _dispatch 顶部同构)
    asyncio.run_coroutine_threadsafe(handle_message_async(data), _bot_loop)


def on_ignored_lark_event(_data) -> None:
    """Acknowledge subscribed Lark events that do not require assistant work."""
    global _last_event
    _last_event = time.time()


# ── CLI Handover ─────────────────────────────────────────────

async def _handle_handover(session_id: str, cwd: str, model: str,
                           target_user: str = "", target_chat: str = "") -> dict:
    """处理来自 CLI 的 handover 请求：切换飞书当前会话并推送通知"""
    return await handle_handover_request(
        store,
        feishu,
        session_id,
        cwd,
        model,
        target_user=target_user,
        target_chat=target_chat,
    )


# ── 卡片回调 HTTP 服务（可选，本机/反代备用）────────────────

class _CardCallbackHandler(BaseHTTPRequestHandler):
    """处理飞书卡片按钮点击的 HTTP 回调"""

    def do_POST(self):
        length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(length)
        try:
            data = json.loads(body)
        except Exception:
            self._respond(400, {"error": "bad json"})
            return

        # 飞书 URL 验证
        if data.get("type") == "url_verification":
            self._respond(200, {"challenge": data.get("challenge", "")})
            return

        event = data.get("event", {})
        operator = event.get("operator", {})
        user_id = operator.get("open_id", "")
        action = event.get("action", {})
        value = action.get("value", {})
        context = event.get("context", {})

        clicked_msg_id = context.get("open_message_id", "")
        action = parse_card_action(user_id, value, clicked_msg_id)

        print(f"[HTTP回调] user={user_id[:8]}... action={action.action_type or 'reply'}", flush=True)

        if not _is_allowed_actor(action.user_id, action.chat_id, action.chat_id != action.user_id):
            self._respond(200, {"toast": {"type": "warning", "content": "无权限"}})
            return

        if action.action_type == "set_mode":
            if action.mode:
                asyncio.run_coroutine_threadsafe(
                    _handle_set_mode(action.user_id, action.chat_id, action.mode, action.clicked_msg_id),
                    _bot_loop,
                )
            self._respond(200, {"toast": toast_for_action(action).__dict__})
        elif action.action_type == "run_cmd":
            if action.cmd_text:
                asyncio.run_coroutine_threadsafe(
                    _handle_menu_command(action.user_id, action.chat_id, action.cmd_text, action.clicked_msg_id),
                    _bot_loop,
                )
            self._respond(200, {"toast": toast_for_action(action).__dict__})
        elif action.action_type == "resume_session":
            if action.session_id:
                asyncio.run_coroutine_threadsafe(
                    _handle_resume_session(action.user_id, action.chat_id, action.session_id, action.clicked_msg_id),
                    _bot_loop,
                )
            self._respond(200, {"toast": toast_for_action(action).__dict__})
        else:
            if action.reply_text:
                asyncio.run_coroutine_threadsafe(
                    _handle_button_reply(action.user_id, action.chat_id, action.reply_text, action.clicked_msg_id),
                    _bot_loop,
                )
            self._respond(200, {"toast": toast_for_action(action).__dict__})

    def do_GET(self):
        from urllib.parse import urlparse, parse_qs
        parsed = urlparse(self.path)

        if parsed.path == "/healthz":
            self._respond(200, _health_payload())
            return

        if parsed.path == "/handover":
            params = parse_qs(parsed.query)
            session_id = params.get("session_id", [""])[0]
            cwd = params.get("cwd", [""])[0]
            model = params.get("model", [""])[0]
            target_user = params.get("user_id", [""])[0]
            target_chat = params.get("chat_id", [""])[0]

            if not session_id:
                self._respond(400, {"error": "session_id required"})
                return

            try:
                future = asyncio.run_coroutine_threadsafe(
                    _handle_handover(session_id, cwd, model, target_user, target_chat),
                    _bot_loop,
                )
                result = future.result(timeout=15)
                self._respond(200, result)
            except Exception as e:
                self._respond(500, {"error": str(e)})
        else:
            self._respond(404, {"error": "not found"})

    def _respond(self, code, data):
        body = json.dumps(data).encode()
        self.send_response(code)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        pass  # 静默 HTTP 日志


# ── 后台定时摘要生成 ─────────────────────────────────────────

def _bg_summary_thread():
    """后台线程: 每 10 分钟扫描未摘要的会话，逐个生成摘要"""
    time.sleep(60)  # 启动后等 1 分钟再开始
    while True:
        try:
            unsummarized = store.get_all_unsummarized()
            if unsummarized:
                print(f"[摘要] 发现 {len(unsummarized)} 个未摘要会话", flush=True)
                count = 0
                for user_id, sid in unsummarized[:5]:
                    try:
                        summary = generate_summary(sid)
                        if summary:
                            store._data.setdefault(user_id, {}).setdefault("summaries", {})[sid] = summary
                            _write_custom_title(sid, summary)
                            count += 1
                            print(f"[摘要] #{sid[:8]} → {summary}", flush=True)
                    except Exception as e:
                        print(f"[摘要] #{sid[:8]} 失败: {e}", flush=True)
                    time.sleep(5)  # 每个请求间隔 5 秒，避免 429
                if count:
                    store._save()  # 同步原子写入
                    print(f"[摘要] 本轮完成 {count}/{len(unsummarized)} 个", flush=True)
        except Exception as e:
            print(f"[摘要] 定时任务异常: {e}", flush=True)
        time.sleep(600)  # 10 分钟


def _start_callback_server(port):
    server = HTTPServer(('0.0.0.0', port), _CardCallbackHandler)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()


def _start_ngrok(port):
    """启动 ngrok 隧道，返回公网 URL"""
    import subprocess
    import urllib.request

    # 先检查已有的 ngrok 隧道
    try:
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=2) as r:
            tunnels = json.loads(r.read())
            for t in tunnels.get("tunnels", []):
                if t.get("proto") == "https":
                    return t["public_url"]
    except Exception:
        pass

    # 启动新 ngrok（有固定域名就用，保证重启后 URL 不变）
    try:
        ngrok_domain = os.environ.get("NGROK_DOMAIN", "")
        ngrok_cmd = ["ngrok", "http", "--url", ngrok_domain, str(port)] if ngrok_domain else ["ngrok", "http", str(port)]
        subprocess.Popen(
            ngrok_cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        time.sleep(3)
        with urllib.request.urlopen("http://127.0.0.1:4040/api/tunnels", timeout=5) as r:
            tunnels = json.loads(r.read())
            for t in tunnels.get("tunnels", []):
                if t.get("proto") == "https":
                    return t["public_url"]
    except Exception as e:
        print(f"   [warn] ngrok 启动失败: {e}", flush=True)
    return None


# ── 启动 ──────────────────────────────────────────────────────

def main():
    print("🚀 飞书 Claude Bot 启动中...")
    print(f"   App ID      : {config.FEISHU_APP_ID}")
    print(f"   默认模型    : {config.DEFAULT_MODEL}")
    print(f"   默认工作目录: {config.DEFAULT_CWD}")
    print(f"   权限模式    : {config.PERMISSION_MODE}")

    # Card actions are registered on the Feishu WebSocket dispatcher below.
    # The HTTP callback server remains as a local/reverse-proxy fallback and
    # also serves health/handover endpoints.
    cb_port = config.CALLBACK_PORT
    _start_callback_server(cb_port)
    ngrok_url = _start_ngrok(cb_port)
    print("   卡片事件    : WebSocket 长连接已注册")
    if ngrok_url:
        print(f"   HTTP 回调   : {ngrok_url}/callback")
    else:
        print(f"   HTTP 回调   : http://localhost:{cb_port}/callback (本机/反代备用)")

    handler = lark.EventDispatcherHandler.builder("", "") \
        .register_p2_im_message_receive_v1(on_message_receive) \
        .register_p2_im_chat_access_event_bot_p2p_chat_entered_v1(on_ignored_lark_event) \
        .register_p2_im_message_message_read_v1(on_ignored_lark_event) \
        .register_p2_card_action_trigger(on_card_action) \
        .build()

    ws_client = lark.ws.Client(
        config.FEISHU_APP_ID,
        config.FEISHU_APP_SECRET,
        event_handler=handler,
        log_level=LARK_SDK_LOG_LEVEL,
    )

    # 启动后台线程
    threading.Thread(target=_watchdog, daemon=True).start()
    threading.Thread(target=_bg_summary_thread, daemon=True).start()

    print("✅ 连接飞书 WebSocket 长连接（自动重连）...")
    ws_client.start()  # 阻塞，内部运行 asyncio loop


if __name__ == "__main__":
    main()
