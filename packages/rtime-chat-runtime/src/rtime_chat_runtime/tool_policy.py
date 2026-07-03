# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared per-request tool-policy core (channel-unification P1).

Channel-neutral intent detection + allow/disallow + runtime hints. The channel-specific
bits (label in hints, memory-candidate --entry, extra disallowed tools, personal-library
env var, the formula-rendering guidance) are carried in a ``ToolPolicy`` profile so each
channel (QQ now, Feishu/WeChat later) shares one implementation. Plain text returns an
empty allowlist (None) => all tools allowed, including any brain/MCP tools.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from rtime_chat_runtime.campus_urls import campus_urls_hint

IMAGE_ALLOWED_TOOLS = ["Read"]
WEB_ALLOWED_TOOLS = ["WebFetch", "WebSearch", "Bash(rtime-web-fetch *)"]
REMINDER_ALLOWED_TOOLS = ["Bash(rtime-reminder-register *)"]
MEMORY_ALLOWED_TOOLS = ["Bash(rtime-memory-candidate *)"]
CONTEXT_SOURCE_ALLOWED_TOOLS = ["Bash(rtime-context-source *)"]
PERSONAL_LIBRARY_ALLOWED_TOOLS = ["Read", "Glob", "Grep", "LS"]
CRON_DISALLOWED_TOOLS = ["CronCreate", "CronDelete", "CronList"]

# --- read-only 硬门（公开只读实例，如学生会答疑）------------------------------
# claude CLI 黑白名单语义（生产先例：apps/obsidian-rtime-assistant/dev/local-gateway.mjs
# 只读门 T1 组合的实测结论 + Claude Code 权限规则语义）：
#   * --disallowedTools 是 deny 规则，deny 永远压过 allow：黑名单里放裸 "Bash" 会把
#     Bash(rtime-web-fetch *) 一并禁掉（allow 打不穿 deny），且 deny 不支持"排除某
#     前缀"的反向通配 —— 所以 Bash **不进**黑名单。
#   * --allowedTools 是 allow（自动放行）规则，本身不收窄工具集；收窄靠权限模式：
#     read_only 的调用方必须以 READONLY_PERMISSION_MODE（"dontAsk"：headless 下未被
#     allow 的调用直接拒绝、不挂起交互）运行，绝不能沿用 bypassPermissions。
#   * --tools 能收窄可用集，但会把 MCP 工具整段排除（brain lib_search 失效），不可用。
# 净效果：写/派生类工具（Edit/Write/…/Task/Agent）被 deny 硬禁、任何权限模式都不可
# 覆盖；Bash 默认被 dontAsk 拒绝，唯一例外是显式 allow 的只读受控抓取器
# Bash(rtime-web-fetch *)；Read/Grep/Glob/LS + Web 只读 + scoped brain 网关 MCP 放行。
READONLY_DISALLOWED = ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent")
READONLY_ALLOWED = (
    "Read",
    "Grep",
    "Glob",
    "LS",
    "WebFetch",
    "WebSearch",
    "Bash(rtime-web-fetch *)",
    # scoped 只读 brain 网关（如学生会 8781 实例，QQ_MCP_CONFIG 指过去）；通配对
    # lib.search→lib_search 之类改名天然兼容（同 obsidian 只读门的做法）。
    "mcp__rtime-library-gateway__*",
    # 管理员上报（A3 决策3）：只读公开 bot 也可在需人工/疑似问题时给管理员发通知。
    # 工具本身只按 RTIME_ADMIN_NOTIFY 配置分发,未配置=no-op;不读库、不碰凭据。
    "Bash(rtime-notify-admin *)",
)

# 管理员上报意图（A3 决策3）：非只读渠道命中时也放行通知工具。
NOTIFY_ALLOWED_TOOLS = ["Bash(rtime-notify-admin *)"]
_ESCALATION_CONTEXT_RE = re.compile(
    r"(转人工|人工客服|找(个)?(管理员|老师|学生会|负责人)|联系(管理员|学生会|负责人)"
    r"|投诉|举报|反馈(个)?(问题|意见|建议)|紧急|急事|出事了|值班)"
)
_ESCALATION_HINT = (
    "\n\n[运行环境提示：如果你判断当前情况值得让管理员知道（比如同学反复问你答不上的"
    "事、有人要人工/投诉/反馈、疑似出问题或紧急情况），你可以调用窄口工具 "
    "`rtime-notify-admin --summary <简要说明> --reason <事由> --urgency low|normal|high "
    "--source <来源如studentunion-qq>` 给管理员发一条上报。是否上报由你结合情况自主决定，"
    "不要滥用；工具只发通知、不读库不碰凭据，未配置通道时会明确返回未送达。上报后简短"
    "告诉用户你已经/无法转达即可，不要回显管理员联系方式。]"
)
READONLY_PERMISSION_MODE = "dontAsk"

# 校园服务高频词（块2 校园网页意图路由）：口语化问"东区班车几点发车"不带 URL、
# 不带"网页"字样时，也应放行 web 工具并附上已知校园服务地址。取舍：
# - "教务"单独两字过宽（"教务老师说…"并非查网页），收敛为"教务处|教务系统"；
# - "通知"单独过宽，取校园网站栏目整词"通知公告"；
# - "班车|校车|校历|一卡通|选课"专指性强，直接收录；
# - "时刻表"可指火车/航班等泛查询：只进 _WEB_INTENT_RE 放行 web 工具（allowlist
#   只增不减、误伤成本低），不进 _CAMPUS_INTENT_RE（免得给高铁时刻表附校园地址）。
_CAMPUS_TERMS = r"班车|校车|校历|教务处|教务系统|一卡通|选课|通知公告"
_CAMPUS_INTENT_RE = re.compile(_CAMPUS_TERMS)
_WEB_INTENT_RE = re.compile(
    r"https?://|网页|网站|上网|联网|网络搜索|网页搜索|网上|搜一下|搜索一下|"
    r"\bweb\s*(search|fetch)?\b|\bbrowse\b|"
    r"时刻表|" + _CAMPUS_TERMS,
    re.IGNORECASE,
)
_REMINDER_CONTEXT_RE = re.compile(
    r"提醒我|叫我|到点提醒|设(?:个|一个)?(?:提醒|闹钟)|定(?:个|一个)?(?:提醒|闹钟)|"
    r"(?:今天|明天|后天|周[一二三四五六日天]|星期[一二三四五六日天]).{0,24}提醒|"
    r"(?:分钟|小时|天|周|月)后.{0,12}提醒|每(?:天|周|小时|月).{0,24}(?:提醒|叫我)|"
    r"定时提醒|提醒事项|remind(?:er)?|schedule(?:d)? reminder",
    re.IGNORECASE,
)
_MEMORY_CONTEXT_RE = re.compile(
    r"请?记住|帮我记(?:一下|住)?|记忆(?:一下|这个)?|以后.{0,16}(?:记得|按这个|偏好)|"
    r"(?:调整|修改|更新).{0,12}(?:偏好|记忆|画像)|remember this|save this preference",
    re.IGNORECASE,
)
_CONTEXT_SOURCE_CONTEXT_RE = re.compile(
    r"(?:添加|新增|取消|停用|启用|调整|修改|检查|列出|查看).{0,18}(?:上下文源|context source|context-source|计划源|偏好源|动态上下文)|"
    r"(?:把|将).{0,32}(?:计划|偏好|资料|文件).{0,18}(?:设为|加入|移出).{0,18}(?:上下文|context source|动态源)",
    re.IGNORECASE,
)
_PERSONAL_LIBRARY_CONTEXT_RE = re.compile(
    r"personal-data|个人库|个人信息|个人资料|私人资料|隐私资料|我的资料库|"
    r"我的档案|我的经历|我的简历|我的履历|任职记录|聊天记录|对话记录|"
    r"lifelog|profile/records|about[- ]?me|关于我",
    re.IGNORECASE,
)
_ATTACHMENT_SEND_RE = re.compile(
    r"发给我|发我|发送给我|传给我|给我发|把.{0,24}(?:文件|图片|截图|附件).{0,24}(?:发|传|给)|"
    r"导出.{0,24}(?:文件|pdf|png|jpg|jpeg|xlsx|docx|pptx|zip)|"
    r"生成.{0,24}(?:文件|图片|截图|pdf|png|jpg|jpeg|xlsx|docx|pptx|zip)|"
    r"(?:send|attach|upload).{0,24}(?:file|image|attachment)",
    re.IGNORECASE,
)

_WEB_FALLBACK_HINT = (
    "\n\n[运行环境提示：本次请求显式涉及网页或搜索，已临时允许 WebFetch、WebSearch "
    "和受控 rtime-web-fetch。若 WebFetch/WebSearch 因安全校验失败不可用，"
    "请用 `rtime-web-fetch url <URL>`、`rtime-web-fetch links <URL>` 或 "
    "`rtime-web-fetch search <query>` 获取公开网页信息。不要读取或外发本地敏感信息，"
    "不要绕过登录、付费墙或站点访问限制。]"
)
_CONTEXT_SOURCE_POLICY_HINT = (
    "\n\n[运行环境提示：用户表达了动态上下文源调整/查看意图。不要直接编辑 brain 源文件。"
    "请使用窄口工具 `rtime-context-source list|add|deactivate|check` 管理 source registry。"
    "添加时 source path 必须是 brain 内相对路径，禁止 personal-data。]"
)
_ATTACHMENT_SEND_HINT = (
    "\n\n[运行环境提示：如果用户明确要求把本机已有文件、刚生成的文件、图片或截图发回聊天，"
    "请先确认文件已经真实存在，再在最终回复的独立一行输出内部标记 "
    "`[[rtime-send-file:/absolute/path]]` 或 `[[rtime-send-image:/absolute/path]]`。"
    "桥接层会移除这个标记并上传为真正的附件。不要用它发送密钥、配置、会话、日志或凭据文件；"
    "不确定文件内容或路径时，先读取/检查再决定。]"
)


def _reminder_hint(channel: str) -> str:
    return (
        f"\n\n[运行环境提示：这是 {channel} 桥接运行。Claude Code 内置 Cron 工具不会通过 "
        "rtime 的系统级 reminder.timer 推送到用户手机；不要使用 CronCreate/CronList/CronDelete "
        "登记、修改或查看提醒。先用北京时间解析成带时区的 ISO 时间。"
        "`notify` 只用于完全自包含的固定文本推送；凡是提醒依赖当前对话背景、学习/考试/出行计划、"
        "项目状态或需要助手到点给建议/检查/总结/判断，默认使用 `wake`："
        "`rtime-reminder-register add --mode wake --due <ISO> --message <简短标题> --prompt <自包含到点任务>`。"
        "查看用 `rtime-reminder-register list --status pending`，取消用 `rtime-reminder-register cancel --id <id>`。]"
    )


def _memory_hint(entry: str) -> str:
    return (
        "\n\n[运行环境提示：用户表达了明确记忆/偏好调整意图。不要直接改写长期 memory/cards。"
        f"如需记录，请只用窄口工具写审核候选：`rtime-memory-candidate add --entry {entry} --claim <用户明确要记住的内容>`。"
        "工具输出不能回显记忆正文；若内容疑似密钥、open_id、验证码、证件或银行卡，工具会 hold。]"
    )


def _personal_library_hint(channel: str) -> str:
    return (
        f"\n\n[运行环境提示：这是 owner 明确授权的单用户 {channel} 入口。"
        "当本轮请求直接需要用户个人库/个人信息时，可只读访问 `/mnt/brain/personal-data/**`、"
        "`/mnt/brain/profile/**` 和 `/mnt/brain/memory/**`，使用 Read/Grep/Glob/LS 查找证据。"
        "只读取与问题相关的最小范围；回复中优先给摘要、判断和下一步，不要整段外发身份证件、token、"
        "密钥、验证码、联系方式或完整聊天原文。需要写入/整理个人资料时，必须先说明计划并等待用户确认。]"
    )


@dataclass(frozen=True)
class ToolPolicy:
    """A channel's tool-policy profile. Methods take the user text and return the
    per-request allowlist / disallowlist / appended runtime hints."""

    channel: str = "chat"  # label inserted into hints (e.g. "QQ", "飞书")
    entry: str = "chat"  # --entry for rtime-memory-candidate
    extra_disallowed: tuple[
        str, ...
    ] = ()  # beyond Cron* (e.g. ("Task", "Agent") for QQ)
    personal_library_env: str = ""  # env var that unlocks personal-library read access
    formula_hint: str = (
        ""  # channel-specific math-rendering guidance (appended to text)
    )
    # read-only 硬门：置 True（或 read_only_env 指向的环境变量为 "1"，QQ 用
    # QQ_READ_ONLY）时，allowlist 恒为封闭的 READONLY_ALLOWED、disallowlist 恒并入
    # READONLY_DISALLOWED。调用方还必须以 READONLY_PERMISSION_MODE 运行本轮（见上）。
    read_only: bool = False
    read_only_env: str = ""  # env var that (== "1") switches read-only on

    def _personal_library_enabled(self) -> bool:
        # read-only 公开实例永不解锁个人库（即使运维误设了 personal_library_env）。
        if self.is_read_only():
            return False
        return (
            bool(self.personal_library_env)
            and os.getenv(self.personal_library_env, "0") == "1"
        )

    def is_read_only(self) -> bool:
        return self.read_only or (
            bool(self.read_only_env) and os.getenv(self.read_only_env, "0") == "1"
        )

    @staticmethod
    def _merge(*groups: list[str] | None) -> list[str] | None:
        merged: list[str] = []
        for group in groups:
            for item in group or []:
                if item not in merged:
                    merged.append(item)
        return merged or None

    def allowed_tools_for_text(self, text: str) -> list[str] | None:
        if self.is_read_only():
            # 只读门：恒返回封闭 allowlist（配合 dontAsk，未列出的调用一律被拒）。
            # web 只读工具已在基线内（公开实例要读校园网页），命中 web 意图时行为
            # 照常；提醒/记忆/上下文源等窄口**写**工具刻意不进来 —— 公开提问者
            # 不能往 owner 的提醒/记忆/context source 里写东西。
            return list(READONLY_ALLOWED)
        groups: list[list[str]] = []
        if _WEB_INTENT_RE.search(text or ""):
            groups.append(WEB_ALLOWED_TOOLS)
        if _ESCALATION_CONTEXT_RE.search(text or ""):
            groups.append(NOTIFY_ALLOWED_TOOLS)
        if _REMINDER_CONTEXT_RE.search(text or ""):
            groups.append(REMINDER_ALLOWED_TOOLS)
        if _MEMORY_CONTEXT_RE.search(text or ""):
            groups.append(MEMORY_ALLOWED_TOOLS)
        if _CONTEXT_SOURCE_CONTEXT_RE.search(text or ""):
            groups.append(CONTEXT_SOURCE_ALLOWED_TOOLS)
        if self._personal_library_enabled() and _PERSONAL_LIBRARY_CONTEXT_RE.search(
            text or ""
        ):
            groups.append(PERSONAL_LIBRARY_ALLOWED_TOOLS)
        return self._merge(*groups)

    def disallowed_tools_for_text(self, text: str) -> list[str]:
        disallowed = list(CRON_DISALLOWED_TOOLS) + list(self.extra_disallowed)
        if self.is_read_only():
            # 注意：不禁裸 "Bash"（deny 压过 allow，会连带禁掉 Bash(rtime-web-fetch *)）；
            # 其余 Bash 由 READONLY_PERMISSION_MODE=dontAsk + 封闭 allowlist 拒绝。
            disallowed += [t for t in READONLY_DISALLOWED if t not in disallowed]
        return disallowed

    def add_runtime_policy_hints(self, text: str) -> str:
        # Slash commands (/model, /new, ...) are parsed downstream; never append hints.
        if (text or "").lstrip().startswith("/"):
            return text
        enriched = text
        read_only = self.is_read_only()
        if _WEB_INTENT_RE.search(text or ""):
            enriched += _WEB_FALLBACK_HINT
        if _CAMPUS_INTENT_RE.search(text or ""):
            enriched += campus_urls_hint()
        if self.formula_hint:
            enriched += self.formula_hint
        # read-only 门下提醒/记忆/上下文源的窄口写工具已被拒，相应"请用 xx 工具写入"
        # 的提示不再附加（避免指挥模型去调一定会被拒的工具）。
        if not read_only and _REMINDER_CONTEXT_RE.search(text or ""):
            enriched += _reminder_hint(self.channel)
        if not read_only and _MEMORY_CONTEXT_RE.search(text or ""):
            enriched += _memory_hint(self.entry)
        if not read_only and _CONTEXT_SOURCE_CONTEXT_RE.search(text or ""):
            enriched += _CONTEXT_SOURCE_POLICY_HINT
        if self._personal_library_enabled() and _PERSONAL_LIBRARY_CONTEXT_RE.search(
            text or ""
        ):
            enriched += _personal_library_hint(self.channel)
        if _ATTACHMENT_SEND_RE.search(text or ""):
            enriched += _ATTACHMENT_SEND_HINT
        # 管理员上报提示:read_only 公开 bot 也保留(通知工具在只读门 allowlist 内)。
        if _ESCALATION_CONTEXT_RE.search(text or ""):
            enriched += _ESCALATION_HINT
        return enriched
