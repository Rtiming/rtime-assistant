# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""飞书桥的模型调用入口 —— 收敛为对共享核心 ``rtime_chat_runtime.model_runner`` 的薄包装。

历史上飞书桥有一份自己的 stream-json runner 实现;channel-unification P3 把它和 QQ 桥
统一到 packages/rtime-chat-runtime 的单一真相源(``run_claude``)。本文件只保留飞书的**调用
签名兼容层**:``message`` 为位置参数、无需显式传 ``cli``(默认 ``CLAUDE_CLI``)、
``permission_mode`` 回落 ``PERMISSION_MODE``、``mcp_config`` 默认 ``FEISHU_MCP_CONFIG``
(默认 None → 不加 --strict-mcp-config,沿用 ~/.claude.json + /mnt/brain,行为与旧版一致)。
复用 ~/.claude/ 的 Max 订阅登录凭证,无需额外 API Key。
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from typing import Callable, Optional

from bot_config import CLAUDE_CLI, FEISHU_MCP_CONFIG, PERMISSION_MODE
from rtime_chat_runtime.model_runner import IDLE_TIMEOUT
from rtime_chat_runtime.model_runner import run_claude as _run_claude

__all__ = ["run_claude", "IDLE_TIMEOUT"]


async def run_claude(
    message: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
    permission_mode: Optional[str] = None,
    allowed_tools: Optional[Sequence[str]] = None,
    disallowed_tools: Optional[Sequence[str]] = None,
    on_text_chunk: Optional[Callable[[str], None]] = None,
    on_tool_use: Optional[Callable[[str, dict], None]] = None,
    on_process_start: Optional[Callable[[asyncio.subprocess.Process], None]] = None,
    *,
    cli: str = CLAUDE_CLI,
    system_prompt: Optional[str] = None,
    mcp_config: Optional[str] = FEISHU_MCP_CONFIG,
) -> tuple[str, Optional[str], bool]:
    """调用 claude CLI 并流式解析输出,委托共享核心。

    Returns: (full_response_text, new_session_id, used_fresh_session_fallback)
    """
    return await _run_claude(
        message,
        cli=cli,
        permission_mode=permission_mode or PERMISSION_MODE,
        session_id=session_id,
        model=model,
        cwd=cwd,
        system_prompt=system_prompt,
        mcp_config=mcp_config,
        allowed_tools=allowed_tools,
        disallowed_tools=disallowed_tools,
        on_text_chunk=on_text_chunk,
        on_tool_use=on_tool_use,
        on_process_start=on_process_start,
    )
