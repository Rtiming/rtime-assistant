# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Per-request Claude Code tool allowlist policy (Feishu bridge).

Channel-unification P3: the intent regexes and ``*_ALLOWED_TOOLS`` lists live once in
``rtime_chat_runtime.tool_policy`` (byte-identical across channels) and are reused here,
killing the literal duplication. Feishu's *runtime hint strings* stay LOCAL — they are
worded differently from other channels (longer reminder/web guidance, an always-on
formula hint) and are shown to the model on the owner's daily bridge, so the exact
wording must not change. Narrow owner-only operational helpers that only exist in
the Feishu Docker runtime, such as QQ code requests, stay local to this module.
"""

from __future__ import annotations

import os
import re

from rtime_chat_runtime import tool_policy as _core
from rtime_chat_runtime.campus_urls import campus_urls_hint as _campus_urls_hint

# Shared allowlist constants (single source) re-exposed under the names Feishu uses.
IMAGE_ALLOWED_TOOLS = _core.IMAGE_ALLOWED_TOOLS
WEB_ALLOWED_TOOLS = _core.WEB_ALLOWED_TOOLS
REMINDER_ALLOWED_TOOLS = _core.REMINDER_ALLOWED_TOOLS
MEMORY_ALLOWED_TOOLS = _core.MEMORY_ALLOWED_TOOLS
CONTEXT_SOURCE_ALLOWED_TOOLS = _core.CONTEXT_SOURCE_ALLOWED_TOOLS
PERSONAL_LIBRARY_ALLOWED_TOOLS = _core.PERSONAL_LIBRARY_ALLOWED_TOOLS
QQ_CODE_ALLOWED_TOOLS = ["Bash(rtime-qq-code *)"]
FEISHU_DEFAULT_DISALLOWED_TOOLS = list(_core.CRON_DISALLOWED_TOOLS)

# Shared intent regexes (single source — identical across channels unless a
# Feishu-only runtime helper is explicitly defined below).
_WEB_INTENT_RE = _core._WEB_INTENT_RE
_CAMPUS_INTENT_RE = _core._CAMPUS_INTENT_RE
_REMINDER_CONTEXT_RE = _core._REMINDER_CONTEXT_RE
_MEMORY_CONTEXT_RE = _core._MEMORY_CONTEXT_RE
_CONTEXT_SOURCE_CONTEXT_RE = _core._CONTEXT_SOURCE_CONTEXT_RE
_PERSONAL_LIBRARY_CONTEXT_RE = _core._PERSONAL_LIBRARY_CONTEXT_RE
_ATTACHMENT_SEND_RE = _core._ATTACHMENT_SEND_RE
_QQ_CODE_CONTEXT_RE = re.compile(
    r"("
    r"(?:qq|QQ|napcat|NapCat|小号).{0,18}(?:码|二维码|登录码|扫码|重新登录|掉线|离线)"
    r"|(?:码|二维码|登录码).{0,18}(?:qq|QQ|napcat|NapCat|小号)"
    r"|补码|发码|调码|取码|把码发"
    r")",
    re.IGNORECASE,
)

# ---- Feishu-local hint strings (intentionally divergent from core; keep verbatim) ----
WEB_FALLBACK_HINT = (
    "\n\n[运行环境提示：本次请求显式涉及网页或搜索，已临时允许 WebFetch、WebSearch "
    "和受控 rtime-web-fetch。若 WebFetch/WebSearch 因安全校验失败不可用，"
    "请用 `rtime-web-fetch url <URL>`、`rtime-web-fetch links <URL>` 或 "
    "`rtime-web-fetch search <query>` 获取公开网页信息。只有在用户确认自己有权访问、"
    "并已把登录 cookie 作为外部 runtime session 放好时，才可用 "
    "`rtime-web-fetch ... --session <name>`；附件下载使用 "
    "`rtime-web-fetch download <URL> --session <name>`，先落到 runtime download 目录。"
    "不要读取或外发本地敏感信息，不要绕过登录、付费墙或站点访问限制。]"
)

REMINDER_POLICY_HINT = (
    "\n\n[运行环境提示：这是飞书桥接运行。Claude Code 内置 Cron 工具不会通过 "
    "rtime 的系统级 reminder.timer 推送到用户手机；不要使用 CronCreate/CronList/CronDelete "
    "登记、修改或查看提醒。先用北京时间解析成带时区的 ISO 时间。"
    "`notify` 只用于完全自包含的固定文本推送，例如喝水、取快递、带证件。"
    "凡是提醒依赖当前对话背景、学习/考试/出行计划、项目状态、用户当时是否已完成、或需要助手到点给建议/检查/总结/判断，"
    "默认使用 `wake`：`rtime-reminder-register add --mode wake --due <ISO> --message <简短标题> --prompt <自包含到点任务>`。"
    "`wake` 的 prompt 必须把当前对话里对到点执行有用的事实压进去，包括原始需求、时间、地点/材料/目标、用户担心点、"
    "希望发给用户的输出风格；不要只写一句“提醒我”。只有用户明确要求到点原样发送一段固定消息，才用 "
    "`rtime-reminder-register add --mode notify --due <ISO> --message <内容> --repeat none|hourly|daily|weekly`。"
    "查看用 `rtime-reminder-register list --status pending`，取消用 `rtime-reminder-register cancel --id <id>`。]"
)

MEMORY_POLICY_HINT = (
    "\n\n[运行环境提示：用户表达了明确记忆/偏好调整意图。不要直接改写长期 memory/cards。"
    "如需记录，请只用窄口工具写审核候选："
    "`rtime-memory-candidate add --entry feishu --claim <用户明确要记住的内容>`。"
    "工具输出不能回显记忆正文；若内容疑似密钥、open_id、验证码、证件、银行卡或其他敏感值，工具会 hold。]"
)

CONTEXT_SOURCE_POLICY_HINT = (
    "\n\n[运行环境提示：用户表达了动态上下文源调整/查看意图。不要直接编辑 brain 源文件。"
    "请使用窄口工具 `rtime-context-source list|add|deactivate|check` 管理 source registry。"
    "添加时 source path 必须是 brain 内相对路径，禁止 personal-data；取消计划时 deactivate/cancel source，保留历史。]"
)

PERSONAL_LIBRARY_POLICY_HINT = (
    "\n\n[运行环境提示：这是 owner 明确授权的单用户 Feishu 入口。"
    "当本轮请求直接需要用户个人库/个人信息时，可只读访问 `/mnt/brain/personal-data/**`、"
    "`/mnt/brain/profile/**` 和 `/mnt/brain/memory/**`，使用 Read/Grep/Glob/LS 查找证据。"
    "只读取与问题相关的最小范围；回复中优先给摘要、判断和下一步，不要整段外发身份证件、open_id、token、"
    "密钥、验证码、联系方式、完整聊天原文或其他高敏明文。"
    "不要把 personal-data 加入长期 context source；需要写入/整理/移动个人资料时，必须先说明计划并等待用户明确确认。]"
)

ATTACHMENT_SEND_HINT = (
    "\n\n[运行环境提示：如果用户明确要求把本机已有文件、刚生成的文件、图片或截图发回飞书，"
    "请先确认文件已经真实存在，再在最终回复的独立一行输出内部标记 "
    "`[[rtime-send-file:/absolute/path]]` 或 `[[rtime-send-image:/absolute/path]]`。"
    "桥接层会移除这个标记并上传为真正的飞书附件。不要用它发送密钥、配置、会话、日志或凭据文件；"
    "不确定文件内容或路径时，先读取/检查再决定。]"
)

FORMULA_OUTPUT_HINT = (
    "\n\n[运行环境提示：飞书桥会自动把回复正文里的 LaTeX 公式（`$...$`、`$$...$$`、"
    "`\\(...\\)`、`\\[...\\]`）渲染成行内 Unicode 显示。需要写数学公式时，直接在消息正文里"
    "用 LaTeX 写即可；**不要**生成或保存公式图片（png/svg）、不要调用 matplotlib/绘图/截图"
    "工具把公式画成图、也不要把公式写到文件再当附件发——用户明确不要图片形式的公式，"
    "直接把 LaTeX 写进回复文本就会被渲染。]"
)

QQ_CODE_POLICY_HINT = (
    "\n\n[运行环境提示：用户正在请求 QQ/NapCat 登录码、补码或重新扫码登录。"
    "不要只口头说明，也不要尝试读取 Docker、qrcode.png、日志或凭据。"
    "请调用窄口工具 `rtime-qq-code request` 写共享请求文件；host 上的 "
    "`qq_selfheal` 守护会取一张**新鲜可扫**的二维码回推飞书（QQ 在线时会直接告知无需扫码；"
    "旧码过期会先等新码、必要时重启 NapCat，最多约 2-3 分钟）。工具 stdout 不含二维码，"
    "调用后只需简短告诉用户码稍后到飞书/若在线会收到免扫提示。]"
)


def owner_personal_library_access_enabled() -> bool:
    return os.getenv("FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS", "0") == "1"


def merge_allowed_tools(*groups: list[str] | None) -> list[str] | None:
    merged: list[str] = []
    for group in groups:
        for item in group or []:
            if item not in merged:
                merged.append(item)
    return merged or None


def allowed_tools_for_text(text: str, *, allow_qq_code: bool = True) -> list[str] | None:
    groups: list[list[str]] = []
    if _WEB_INTENT_RE.search(text or ""):
        groups.append(WEB_ALLOWED_TOOLS)
    if allow_qq_code and qq_code_intent_detected(text):
        groups.append(QQ_CODE_ALLOWED_TOOLS)
    if reminder_intent_detected(text):
        groups.append(REMINDER_ALLOWED_TOOLS)
    if memory_intent_detected(text):
        groups.append(MEMORY_ALLOWED_TOOLS)
    if context_source_intent_detected(text):
        groups.append(CONTEXT_SOURCE_ALLOWED_TOOLS)
    if owner_personal_library_access_enabled() and personal_library_intent_detected(
        text
    ):
        groups.append(PERSONAL_LIBRARY_ALLOWED_TOOLS)
    return merge_allowed_tools(*groups)


def reminder_intent_detected(text: str) -> bool:
    return bool(_REMINDER_CONTEXT_RE.search(text or ""))


def qq_code_intent_detected(text: str) -> bool:
    return bool(_QQ_CODE_CONTEXT_RE.search(text or ""))


def memory_intent_detected(text: str) -> bool:
    return bool(_MEMORY_CONTEXT_RE.search(text or ""))


def context_source_intent_detected(text: str) -> bool:
    return bool(_CONTEXT_SOURCE_CONTEXT_RE.search(text or ""))


def personal_library_intent_detected(text: str) -> bool:
    return bool(_PERSONAL_LIBRARY_CONTEXT_RE.search(text or ""))


def disallowed_tools_for_text(text: str) -> list[str]:
    # Feishu runs are short-lived and cannot rely on Claude Code's internal Cron
    # scheduler for phone notifications. Keep the real reminder path in JSONL.
    return list(FEISHU_DEFAULT_DISALLOWED_TOOLS)


def add_web_fallback_hint(text: str) -> str:
    if _WEB_INTENT_RE.search(text or ""):
        return text + WEB_FALLBACK_HINT
    return text


def add_runtime_policy_hints(text: str, *, allow_qq_code: bool = True) -> str:
    # Slash commands (/model, /new, /help, ...) are parsed downstream from this
    # same text; never append hints to them or the command argument is corrupted.
    if (text or "").lstrip().startswith("/"):
        return text
    enriched = add_web_fallback_hint(text)
    # 校园服务意图（块2）：附上已知校园服务 URL 表（内置 + RTIME_CAMPUS_URLS_FILE 覆盖）。
    if _CAMPUS_INTENT_RE.search(text or ""):
        enriched += _campus_urls_hint()
    enriched += FORMULA_OUTPUT_HINT
    if allow_qq_code and qq_code_intent_detected(text):
        enriched += QQ_CODE_POLICY_HINT
    if reminder_intent_detected(text):
        enriched += REMINDER_POLICY_HINT
    if memory_intent_detected(text):
        enriched += MEMORY_POLICY_HINT
    if context_source_intent_detected(text):
        enriched += CONTEXT_SOURCE_POLICY_HINT
    if owner_personal_library_access_enabled() and personal_library_intent_detected(
        text
    ):
        enriched += PERSONAL_LIBRARY_POLICY_HINT
    if _ATTACHMENT_SEND_RE.search(text or ""):
        enriched += ATTACHMENT_SEND_HINT
    return enriched
