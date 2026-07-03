# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""User-visible output policy helpers for bridge rendering."""

from __future__ import annotations

import re


FEISHU_POST_SAFE_MAX_BYTES = 24 * 1024


def segmented_output_enabled(output_style: str) -> bool:
    return output_style == "segmented"


def show_tool_calls(enabled: bool) -> bool:
    return bool(enabled)


def _looks_like_gfm_table_line(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("|") and stripped.endswith("|") and stripped.count("|") >= 2


def _looks_like_fence(line: str) -> bool:
    stripped = line.strip()
    return stripped.startswith("```") or stripped.startswith("~~~")


def normalize_markdown_for_feishu_post(text: str) -> str:
    """Normalize Markdown details that Feishu Markdown renderers parse strictly."""
    lines = (text or "").split("\n")
    result: list[str] = []
    in_table = False
    in_fence = False

    for line in lines:
        is_fence = _looks_like_fence(line)
        is_table = False if in_fence else _looks_like_gfm_table_line(line)

        if is_table and not in_table:
            if result and result[-1].strip():
                result.append("")
            in_table = True
        elif not is_table and in_table:
            if line.strip() and result and result[-1].strip():
                result.append("")
            in_table = False

        result.append(line)

        if is_fence:
            in_fence = not in_fence

    return "\n".join(result).strip()


def _split_long_text_by_bytes(text: str, max_bytes: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    current_bytes = 0
    for char in text:
        char_bytes = len(char.encode("utf-8"))
        if current and current_bytes + char_bytes > max_bytes:
            chunks.append(current)
            current = ""
            current_bytes = 0
        current += char
        current_bytes += char_bytes
    if current:
        chunks.append(current)
    return chunks


def split_markdown_for_feishu_post(
    text: str,
    *,
    max_bytes: int = FEISHU_POST_SAFE_MAX_BYTES,
) -> list[str]:
    """Return Markdown chunks below the Feishu body-message safety ceiling."""
    normalized = normalize_markdown_for_feishu_post(text)
    if not normalized:
        return []
    if len(normalized.encode("utf-8")) <= max_bytes:
        return [normalized]

    chunks: list[str] = []
    current_lines: list[str] = []
    current_bytes = 0

    def flush_current() -> None:
        nonlocal current_lines, current_bytes
        chunk = "".join(current_lines).strip()
        if chunk:
            chunks.append(chunk)
        current_lines = []
        current_bytes = 0

    for line in normalized.splitlines(keepends=True):
        line_bytes = len(line.encode("utf-8"))
        if line_bytes > max_bytes:
            flush_current()
            chunks.extend(part.strip() for part in _split_long_text_by_bytes(line, max_bytes) if part.strip())
            continue
        if current_lines and current_bytes + line_bytes > max_bytes:
            flush_current()
        current_lines.append(line)
        current_bytes += line_bytes

    flush_current()
    return chunks


def extract_options(text: str) -> list[tuple[str, str]]:
    """Extract Claude Code option prompts as button labels and reply values."""
    lines = text.strip().split("\n")

    option_lines = []
    for line in reversed(lines):
        line = line.strip()
        if not line:
            if option_lines:
                break
            continue
        match = re.match(r"^(\d+|[a-zA-Z])[.）\)、]\s*(.+)", line)
        if match:
            option_lines.append((match.group(1), match.group(2).strip()))
        elif option_lines:
            break
        else:
            break
    option_lines.reverse()
    if len(option_lines) >= 2:
        return [
            (f"{key}. {desc}" if len(desc) <= 18 else f"{key}. {desc[:16]}..", key)
            for key, desc in option_lines
        ]

    tail = "\n".join(lines[-3:]) if len(lines) >= 3 else text
    if re.search(r"\by\b.*\bn\b|Y/N|yes.*no|是/否|确认/取消", tail, re.IGNORECASE):
        return [("Yes", "yes"), ("No", "no")]

    return []


def format_tool(name: str, inp: dict) -> str:
    """Format a hidden-or-debug tool call for optional status-card display."""
    normalized = name.lower()
    if normalized == "bash":
        command = inp.get("command", "")
        if len(command) > 80:
            command = command[:77] + "..."
        return f"🔧 **执行命令：** `{command}`" if command else "🔧 **执行命令...**"
    if normalized in ("read_file", "read"):
        return f"📄 **读取：** `{inp.get('file_path', inp.get('path', ''))}`"
    if normalized in ("write_file", "write"):
        return f"✏️ **写入：** `{inp.get('file_path', inp.get('path', ''))}`"
    if normalized in ("edit_file", "edit"):
        return f"✂️ **编辑：** `{inp.get('file_path', inp.get('path', ''))}`"
    if normalized == "glob":
        return f"🔍 **搜索文件：** `{inp.get('pattern', '')}`"
    if normalized == "grep":
        return f"🔎 **搜索内容：** `{inp.get('pattern', '')}`"
    if normalized == "task":
        return f"🤖 **子任务：** {inp.get('description', inp.get('prompt', '')[:40])}"
    if normalized == "webfetch":
        return "🌐 **抓取网页...**"
    if normalized == "websearch":
        return f"🔍 **搜索：** {inp.get('query', '')}"
    return f"⚙️ **{name}**"
