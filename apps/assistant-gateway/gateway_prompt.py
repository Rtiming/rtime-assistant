# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Prompt assembly for the assistant gateway.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Builds the non-instruction prompt fed to claude — history recap, runtime-diagnostic
and related-prefetch sections, web/investigation budget detection, agent-tool
gating, and the allowed-tools string. Pure given its inputs (no subprocess, no
unlock resolution, no module state beyond the TASK_HINTS / HISTORY_ROLES tables);
depends only on already-carved leaves (_common, attachments, context_sources,
memory, models). Behavior-invariant move.
"""

from __future__ import annotations

import json
from pathlib import Path

from _common import (
    DEFAULT_WEB_ALLOWED_TOOLS,
    FULL_ACCESS_ALLOWED_TOOLS,
    INVESTIGATION_QUERY,
    WEB_INTENT_QUERY,
    full_access_enabled,
    request_is_runtime_error_question,
    runtime_error_context,
)
from attachments import (
    build_attachments_section,
    request_has_archive_attachments,
    request_has_file_attachments,
    request_has_image_attachments,
)
from context_sources import context_source_injection
from memory import approved_memory_injection, build_memory_section
from models import (
    model_selection_supports_file_extract,
    model_selection_supports_images,
)


TASK_HINTS = {
    "ask": "回答用户的问题。",
    "summarize": "总结当前资料的核心内容。",
    "explain": "用清晰的物理/数学语言讲解选中内容或当前主题，可分步推导。",
    "related": "找出与当前内容相关的库内资料与概念，并说明关联。",
    "citation-review": "检查当前笔记的引用与出处是否完整、可溯源。",
}


HISTORY_ROLES = {"user": "用户", "assistant": "助手"}


def build_history_section(history, max_chars: int) -> str | None:
    """Render context.history into a 此前对话回顾 prompt section.

    Items must be {role: user|assistant, content: str}; anything else is
    dropped. Budgeting walks newest→oldest so recent turns always survive
    intact; the oldest message that overflows keeps only its tail (the part
    closest to the surviving conversation)."""
    if not isinstance(history, list) or max_chars <= 0:
        return None
    cleaned: list[tuple[str, str]] = []
    for item in history:
        if not isinstance(item, dict):
            continue
        role = item.get("role")
        content = item.get("content")
        if role not in HISTORY_ROLES or not isinstance(content, str):
            continue
        text = content.strip()
        if text:
            cleaned.append((role, text))
    if not cleaned:
        return None
    kept: list[str] = []
    remaining = max_chars
    for role, text in reversed(cleaned):
        if remaining <= 0:
            break
        if len(text) > remaining:
            text = "…" + text[-remaining:]
        kept.append(f"[{HISTORY_ROLES[role]}] {text}")
        remaining -= len(text)
    kept.reverse()
    return (
        "此前对话回顾（按时间顺序，仅供理解上下文与指代；这些不是新指令，不要逐条回应）：\n"
        + "\n".join(kept)
    )






def runtime_diagnostic_records(body: dict, cfg: dict, *, limit: int = 30) -> list[dict]:
    """Return redacted request-log evidence for model-assisted runtime diagnosis."""
    path = Path(cfg.get("log_dir") or "") / "requests.jsonl"
    if not path.is_file():
        return []
    conversation_id = body.get("conversation_id")
    rows: list[dict] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(limit * 3, 60):]
    except OSError:
        return []
    for line in lines:
        try:
            item = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(item, dict):
            continue
        if conversation_id and item.get("conversation_id") not in {None, conversation_id}:
            continue
        safe = {
            key: item.get(key)
            for key in (
                "ts",
                "endpoint",
                "status",
                "dur_ms",
                "queued_ms",
                "task_mode",
                "budget_profile",
                "conversation_id",
                "unlock_count",
                "related_count",
                "pdf_page",
            )
            if key in item
        }
        note = item.get("note")
        if isinstance(note, str) and note:
            safe["note_basename"] = Path(note).name
        rows.append(safe)
    return rows[-limit:]


def build_runtime_diagnostic_section(body: dict, cfg: dict) -> str | None:
    if not request_is_runtime_error_question(body):
        return None
    lines = [
        "运行诊断证据包（由gateway从请求日志提取，已去除消息正文和敏感值；请基于这些证据分析，不要猜）："
    ]
    ctx = runtime_error_context(body)
    if ctx:
        code, message = ctx
        lines.append(f"- 用户侧最近错误 code: {code}")
        lines.append(f"- 用户侧最近错误摘要: {message}")
    rows = runtime_diagnostic_records(body, cfg)
    if not rows:
        lines.append("- 近期请求日志：不可读或为空。请明确说明证据不足。")
    else:
        lines.append("- 近期请求日志摘录（时间顺序）：")
        for row in rows:
            lines.append("  - " + json.dumps(row, ensure_ascii=False, sort_keys=True))
    lines.append(
        "诊断要求：调用模型进行分析；优先判断错误发生在工具轮次、流式完整性、排队、endpoint、prepare还是插件渲染；"
        "指出证据和不确定点；给出下一步修复/复测动作。不要把当前PDF/课件当成资料问题去检索。"
    )
    return "\n".join(lines)


def request_needs_investigation_budget(body: dict) -> bool:
    """Heuristic for broad multi-file work that usually needs more tool turns.

    Keep this narrow: normal ask/explain turns must stay on the fast budget.
    """
    context = body.get("context") or {}
    options = body.get("options") or {}
    active = context.get("active_file") or {}
    pdf_state = context.get("pdf") or {}
    selection = context.get("selection") or {}
    attachments = context.get("attachments") or []
    route = options.get("route_hint") or {}
    pieces = [
        str(body.get("message") or ""),
        str(options.get("task_mode") or ""),
        str(options.get("target_folder") or ""),
        str(route.get("target_folder") or "") if isinstance(route, dict) else "",
        str(active.get("path") or ""),
        str(pdf_state.get("path") or ""),
        str(selection.get("text") or "")[:500],
    ]
    if isinstance(attachments, list):
        for item in attachments[:5]:
            if isinstance(item, dict):
                pieces.append(str(item.get("name") or ""))
                pieces.append(str(item.get("path") or ""))
    return bool(INVESTIGATION_QUERY.search("\n".join(part for part in pieces if part)))


def request_needs_web_budget(body: dict) -> bool:
    context = body.get("context") or {}
    active = context.get("active_file") or {}
    selection = context.get("selection") or {}
    attachments = context.get("attachments") or []
    pieces = [
        str(body.get("message") or ""),
        str(active.get("path") or ""),
        str(selection.get("text") or "")[:500],
    ]
    if isinstance(attachments, list):
        for item in attachments[:5]:
            if isinstance(item, dict):
                pieces.append(str(item.get("name") or ""))
                pieces.append(str(item.get("path") or ""))
    return bool(WEB_INTENT_QUERY.search("\n".join(part for part in pieces if part)))


def build_prompt(body: dict, unlocks: list[tuple[str, Path]], cfg: dict) -> str:
    context = body.get("context") or {}
    options = body.get("options") or {}
    active = context.get("active_file") or {}
    note = context.get("note") or {}
    selection = context.get("selection") or {}
    task_mode = options.get("task_mode", "ask")
    message = (body.get("message") or "").strip()
    runtime_diag = request_is_runtime_error_question(body)
    full_access = full_access_enabled(cfg)

    parts: list[str] = []
    if runtime_diag:
        parts.append(
            "你是rtime助手运行链路诊断助手，当前经Obsidian侧边栏被调用。"
            "本轮用户要你调查刚才为什么报错；请基于gateway提供的运行证据包分析。"
        )
    else:
        parts.append(
            "你是rtime个人知识库(brain)的复习助手，当前经Obsidian侧边栏被调用。"
            + TASK_HINTS.get(task_mode, TASK_HINTS["ask"])
        )
    if runtime_diag:
        parts.append(
            "规则：只读不写；不要读取当前PDF/课件资料；不要编造日志中没有的事实；"
            "回答用中文，先给结论，再列证据和下一步复测动作。"
        )
    elif full_access:
        parts.append(
            "规则：当前gateway已进入full-access模式，用户明确要求整理、入库、创建、移动或修改文件时，"
            "你可以在brain工作区内使用写入工具完成任务，不要声称自己处于只读模式。"
            "仍需遵守：原件不覆盖不删除，批量/新分类/重名异内容/疑似高敏内容先问清楚；"
            "涉及入库时优先走_inbox ticket、sha256去重、manifest/README/伴生md/索引更新流程。"
            "回答用中文、准确简洁；引用资料必须给出真实路径与页码，不得编造；"
            "回答末尾用单独一行'来源：'开始，逐行列出 `- <brain相对路径>#page=<页码>`（没有引用可省略该节）。"
        )
    else:
        parts.append(
            "规则：只读不写；禁止访问personal-data/；公式用$...$/$$...$$；"
            "回答用中文、准确简洁；引用资料必须给出真实路径与页码，不得编造；"
            "回答末尾用单独一行'来源：'开始，逐行列出 `- <brain相对路径>#page=<页码>`（没有引用可省略该节）。"
        )
    if active.get("path"):
        label = "报错发生时用户打开的文件" if runtime_diag else "用户当前打开的文件"
        parts.append(f"{label}：{active.get('path')}")
    pdf_state = context.get("pdf") or {}
    page = pdf_state.get("page")
    if not runtime_diag and isinstance(page, int) and page > 0:
        parts.append(
            f"用户此刻正停留在该PDF第{page}页。优先用Glob在页图目录找page-*{page}*.png"
            f"并Read该页图（必要时连同前后一页），围绕这一页回答。"
        )
    if not runtime_diag and unlocks:
        listing = "\n".join(f"- {label}: {path}" for label, path in unlocks)
        parts.append(
            "本次解锁的资料（可用Read/Glob/Grep读取；页图目录先Glob列出再按需Read具体页图）：\n"
            + listing
        )
    parts.append("若凭已给上下文即可回答，请直接回答，不要调用工具，缩短响应时间。")
    sel_text = (selection.get("text") or "").strip()
    if sel_text:
        parts.append(f"用户选中的内容：\n{sel_text}")
    attachments_section = build_attachments_section(context.get("attachments"), full_access=full_access)
    if attachments_section:
        parts.append(attachments_section)
    memory_section = build_memory_section(context.get("memory"))
    if memory_section:
        parts.append(memory_section)
    runtime_ctx = runtime_error_context(body)
    if runtime_ctx:
        code, message = runtime_ctx
        parts.append(
            "最近一次助手运行错误（仅用于解释报错原因，不是资料内容）：\n"
            f"- code: {code}\n"
            f"- message: {message}\n"
            "若用户是在问为什么报错，请直接解释运行原因和可恢复动作，不要调用资料工具。"
        )
    runtime_diag_section = build_runtime_diagnostic_section(body, cfg)
    if runtime_diag_section:
        parts.append(runtime_diag_section)
    note_text = (note.get("text") or "").strip()
    if not runtime_diag and note_text:
        truncated = "（已截断）" if note.get("truncated") else ""
        parts.append(f"当前笔记内容{truncated}：\n{note_text}")
    history_section = build_history_section(
        context.get("history"), int(cfg.get("history_max_chars", 4000))
    )
    if history_section:
        parts.append(history_section)
    approved_memory_section, _ = approved_memory_injection(body, cfg)
    if approved_memory_section:
        parts.append(approved_memory_section)
    context_source_section, _ = context_source_injection(body, cfg)
    if context_source_section:
        parts.append(context_source_section)
    related_section = None if runtime_diag else build_related_prefetch_section(body, unlocks, cfg)
    if related_section:
        parts.append(related_section)
    if request_needs_investigation_budget(body):
        parts.append(
            "本轮像是多文件检索/查重任务：先用Glob/Bash索引列出候选文件，再分批读取标题、目录或关键页；"
            "优先给出可核对的候选表，不要只输出“继续扫描/准备查找”这类计划句。"
        )
    if cfg.get("web_tools_enabled", True) and request_needs_web_budget(body):
        parts.append(
            "本轮显式涉及公开网页/网络搜索：可以自主使用 WebSearch/WebFetch，"
            "也可以在需要受控只读抓取时使用 "
            "`rtime-web-fetch search \"<查询词>\"` 或 "
            "`rtime-web-fetch url <公开URL>`。不要为了速度跳过必要核验；"
            "最终给出来源URL、日期/时间线和不确定点。不要读取或外发本地敏感信息。"
        )
    needs_library_search = bool(not runtime_diag and (unlocks or note_text or sel_text or task_mode in {"related", "citation-review"}))
    if needs_library_search:
        parts.append(
            "需要检索库内其他资料时，可执行：\n"
            f"PYTHONPATH={cfg['index_pythonpath']} python3 -m brain_library index query "
            f"{cfg['index_db']} \"<查询词>\" --limit 5"
        )
    parts.append(f"用户的请求：{message}" if message else "用户未输入文字，请按任务模式处理当前资料。")
    return "\n\n".join(parts)




















def _candidate_relation_sources(body: dict, unlocks: list[tuple[str, Path]], cfg: dict) -> set[str]:
    brain_root = Path(cfg.get("brain_root") or "")
    candidates: set[str] = set()
    active_path = ((body.get("context") or {}).get("active_file") or {}).get("path")
    if isinstance(active_path, str) and active_path:
        candidates.add(active_path)
        candidates.add(Path(active_path).name)
    for _label, path in unlocks:
        try:
            rel = path.resolve().relative_to(brain_root.resolve()).as_posix()
            candidates.add(rel)
            candidates.add(Path(rel).name)
        except (OSError, ValueError):
            candidates.add(path.name)
    return {item for item in candidates if item}


def related_edges_for_request(body: dict, unlocks: list[tuple[str, Path]], cfg: dict) -> list[dict]:
    path = Path(cfg.get("relations_path") or "")
    if not path.is_file():
        return []
    sources = _candidate_relation_sources(body, unlocks, cfg)
    if not sources:
        return []
    edges: list[dict] = []
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                item = json.loads(line)
            except json.JSONDecodeError:
                continue
            src = str(item.get("src") or "")
            if src in sources or Path(src).name in sources:
                edges.append(item)
    except OSError:
        return []
    edges.sort(key=lambda edge: (-float(edge.get("score") or 0), str(edge.get("rel") or ""), str(edge.get("dst") or "")))
    seen: set[str] = set()
    out: list[dict] = []
    for edge in edges:
        dst = str(edge.get("dst") or "")
        if not dst or dst in seen:
            continue
        seen.add(dst)
        out.append(edge)
        if len(out) >= int(cfg.get("related_prefetch_limit", 5)):
            break
    return out


def build_related_prefetch_section(body: dict, unlocks: list[tuple[str, Path]], cfg: dict) -> str | None:
    task_mode = ((body.get("options") or {}).get("task_mode") or "ask")
    if task_mode not in {"related", "citation-review"} and not unlocks:
        return None
    rows = related_edges_for_request(body, unlocks, cfg)
    if not rows:
        return None
    budget = int(cfg.get("related_prefetch_max_chars", 1200))
    lines = ["已预取的库内相关材料（来自派生relations.jsonl；优先核对后再引用）："]
    remaining = budget
    for edge in rows:
        line = f"- {edge.get('dst')} ({edge.get('rel')}, score={edge.get('score')}, evidence={edge.get('evidence')})"
        if len(line) > remaining:
            line = line[: max(0, remaining - 3)].rstrip() + "..."
        if not line.strip("."):
            break
        lines.append(line)
        remaining -= len(line)
        if remaining <= 0:
            break
    return "\n".join(lines) if len(lines) > 1 else None












# Single atomic (key, cards) slot. Reassigning a dict value is atomic under the
# GIL, so the threaded server reads either the old or new tuple, never a torn one.


















































def request_requires_agent_tools(
    body: dict,
    unlocks: list[tuple[str, Path]],
    model_selection: dict | None = None,
) -> bool:
    """True when a chat-only model would be the wrong route for this request."""
    if bool(unlocks) or request_needs_investigation_budget(body):
        return True
    if request_has_archive_attachments(body):
        return True
    if request_has_image_attachments(body) and not model_selection_supports_images(model_selection):
        return True
    return request_has_file_attachments(body) and not model_selection_supports_file_extract(model_selection)


def enforce_agent_tool_model(
    body: dict,
    unlocks: list[tuple[str, Path]],
    model_selection: dict | None,
    model_warning: str | None,
) -> tuple[dict | None, str | None]:
    if (
        model_selection
        and model_selection.get("protocol") == "openai-chat"
        and request_requires_agent_tools(body, unlocks, model_selection)
    ):
        label = f"{model_selection.get('provider_id')}/{model_selection.get('model_id')}"
        warning = f"本轮需要读取本地资料/文件工具，{label} 是 chat-only，已回退默认工具模型。"
        return None, f"{model_warning}；{warning}" if model_warning else warning
    return model_selection, model_warning


def allowed_tools(cfg: dict) -> str:
    if full_access_enabled(cfg):
        tools = list(FULL_ACCESS_ALLOWED_TOOLS)
        extra = cfg.get("extra_allowed_tools") or ""
        if isinstance(extra, str):
            tools.extend(item.strip() for item in extra.split(",") if item.strip())
        elif isinstance(extra, list):
            tools.extend(str(item).strip() for item in extra if str(item).strip())
        deduped: list[str] = []
        for item in tools:
            if item and item not in deduped:
                deduped.append(item)
        return ",".join(deduped)
    index_cmd = (
        f"Bash(PYTHONPATH={cfg['index_pythonpath']} python3 -m brain_library "
        f"index query {cfg['index_db']} *)"
    )
    tools = ["Read", "Glob", "Grep", index_cmd]
    if bool(cfg.get("web_tools_enabled", True)):
        tools.extend(DEFAULT_WEB_ALLOWED_TOOLS)
    extra = cfg.get("extra_allowed_tools") or ""
    if isinstance(extra, str):
        tools.extend(item.strip() for item in extra.split(",") if item.strip())
    elif isinstance(extra, list):
        tools.extend(str(item).strip() for item in extra if str(item).strip())
    deduped: list[str] = []
    for item in tools:
        if item and item not in deduped:
            deduped.append(item)
    return ",".join(deduped)
