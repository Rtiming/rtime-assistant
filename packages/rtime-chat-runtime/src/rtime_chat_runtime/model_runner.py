# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared CLI model runner — the single source for spawning the local ``claude`` CLI /
``claude-rtime`` wrapper and streaming its stream-json output.

This is the unified ``CliModelRunner`` mechanism behind the channel-unification plan
(docs/channel-unification-plan.zh-CN.md): Feishu / QQ / Obsidian-default all spawn the
same CLI; this module is the canonical implementation they converge on. The wrapper
routes by ``--model`` (empty => kimi-code) and reaches brain via the CLI's
``~/.claude.json`` MCP servers; no API key here. ``cli`` and ``permission_mode`` are
explicit args (not a channel-specific config import), and optional ``--resume`` /
``--append-system-prompt`` cover the bridges' session continuity and the gateway's
one-shot use.

A future ``ProviderModelRunner`` (urllib chat-completions) will live alongside this and
emit the same callback shape, so callers don't care which mechanism runs.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess as sp
import time
from collections.abc import Callable, Sequence

log = logging.getLogger("rtime_chat_runtime.model")

IDLE_TIMEOUT = 300  # 5 min with no output and no child process => treat as hung
_CHECK_INTERVAL = 30


def _has_children(pid: int) -> bool:
    try:
        result = sp.run(["pgrep", "-P", str(pid)], capture_output=True, timeout=5)
        return result.returncode == 0
    except Exception:
        return False


def _extract_text_content(value) -> str:
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for item in value:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
        return "".join(parts)
    return ""


async def _fire_callback(cb, *args):
    if cb is None:
        return
    if asyncio.iscoroutinefunction(cb):
        await cb(*args)
    else:
        cb(*args)


async def run_claude(
    message: str,
    *,
    cli: str,
    permission_mode: str = "default",
    session_id: str | None = None,
    model: str | None = None,
    cwd: str | None = None,
    system_prompt: str | None = None,
    mcp_config: str | None = None,
    allowed_tools: Sequence[str] | None = None,
    disallowed_tools: Sequence[str] | None = None,
    on_text_chunk: Callable[[str], None] | None = None,
    on_tool_use: Callable[[str, dict], None] | None = None,
    on_process_start: Callable[[asyncio.subprocess.Process], None] | None = None,
    max_seconds: float | None = None,
) -> tuple[str, str | None, bool]:
    """Run the CLI and stream output. Returns (full_text, new_session_id, used_fresh_session_fallback).

    ``max_seconds`` is a hard wall-clock ceiling: when exceeded the child process is killed
    and a RuntimeError is raised. Unlike IDLE_TIMEOUT (which never fires while the CLI has a
    live child, e.g. a slow Read), this bounds a pathologically long run so one message can't
    wedge a bridge. Default None keeps the original unbounded behavior (Feishu unchanged)."""

    async def _run_once(
        active_session_id: str | None,
    ) -> tuple[str, str | None, int, str]:
        cmd = [
            cli,
            "--print",
            "--output-format",
            "stream-json",
            "--verbose",
            "--include-partial-messages",
            "--permission-mode",
            permission_mode,
        ]
        if system_prompt:
            cmd += ["--append-system-prompt", system_prompt]
        if mcp_config:
            # Only the given MCP config (inline JSON or path); ignore ~/.claude.json.
            # An empty {"mcpServers":{}} skips MCP load entirely — big cold-start win
            # for channels that don't need MCP (brain reached via the filesystem mount).
            cmd += ["--strict-mcp-config", "--mcp-config", mcp_config]
        if allowed_tools:
            cmd += ["--allowedTools", ",".join(allowed_tools)]
        if disallowed_tools:
            cmd += ["--disallowedTools", ",".join(disallowed_tools)]
        if active_session_id:
            cmd += ["--resume", active_session_id]
        if model:
            cmd += ["--model", model]

        env = os.environ.copy()
        env.pop("CLAUDECODE", None)

        log.debug(
            "model exec: cli=%s model=%s perm=%s resume=%s allowed=%s disallowed=%s",
            cli,
            model or "(default)",
            permission_mode,
            bool(active_session_id),
            list(allowed_tools or []),
            list(disallowed_tools or []),
        )
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=cwd or os.path.expanduser("~"),
            env=env,
            limit=10 * 1024 * 1024,
        )
        await _fire_callback(on_process_start, proc)

        proc.stdin.write((message + "\n").encode())
        await proc.stdin.drain()
        proc.stdin.close()

        full_text = ""
        new_session_id = None
        pending_tool_name = ""
        pending_tool_input_json = ""
        idle_seconds = 0
        started = time.monotonic()
        # Poll no longer than the wall-clock cap so it's honored promptly (not +30s late).
        poll = (
            _CHECK_INTERVAL
            if max_seconds is None
            else max(0.5, min(float(_CHECK_INTERVAL), max_seconds))
        )

        while True:
            if max_seconds is not None and (time.monotonic() - started) > max_seconds:
                proc.kill()
                await proc.wait()
                raise RuntimeError(
                    f"模型执行超过墙钟上限（{int(max_seconds)}秒），已终止进程"
                )
            try:
                raw_line = await asyncio.wait_for(proc.stdout.readline(), timeout=poll)
                idle_seconds = 0
            except asyncio.TimeoutError:
                if _has_children(proc.pid):
                    idle_seconds = 0
                    continue
                idle_seconds += poll
                if idle_seconds >= IDLE_TIMEOUT:
                    proc.kill()
                    await proc.wait()
                    raise RuntimeError(
                        f"模型执行超时（{IDLE_TIMEOUT}秒无输出且无活跃子进程）"
                    )
                continue

            if not raw_line:
                break
            line = raw_line.decode("utf-8", errors="replace").strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = data.get("type")
            if event_type == "system":
                sid = data.get("session_id")
                if sid:
                    new_session_id = sid
            elif event_type == "stream_event":
                evt = data.get("event", {})
                evt_type = evt.get("type")
                if evt_type == "content_block_delta":
                    delta = evt.get("delta", {})
                    if delta.get("type") == "text_delta":
                        chunk = delta.get("text", "")
                        if chunk:
                            full_text += chunk
                            await _fire_callback(on_text_chunk, chunk)
                    elif delta.get("type") == "input_json_delta":
                        pending_tool_input_json += delta.get("partial_json", "")
                elif evt_type == "content_block_start":
                    block = evt.get("content_block", {})
                    if block.get("type") == "tool_use":
                        pending_tool_name = block.get("name", "")
                        pending_tool_input_json = ""
                        await _fire_callback(on_tool_use, pending_tool_name, {})
                elif evt_type == "content_block_stop":
                    if pending_tool_name and pending_tool_input_json:
                        try:
                            inp = json.loads(pending_tool_input_json)
                        except json.JSONDecodeError:
                            inp = {}
                        await _fire_callback(on_tool_use, pending_tool_name, inp)
                    pending_tool_name = ""
                    pending_tool_input_json = ""
            elif event_type == "result":
                sid = data.get("session_id")
                if sid:
                    new_session_id = sid
                final_text = _extract_text_content(data.get("result", ""))
                if final_text:
                    full_text = final_text

        stderr_output = await proc.stderr.read()
        await proc.wait()
        stderr_text = stderr_output.decode("utf-8", errors="replace").strip()
        if proc.returncode:
            log.warning("model exit=%s stderr: %s", proc.returncode, stderr_text[:600])
        return full_text.strip(), new_session_id, proc.returncode, stderr_text

    final_text, new_session_id, returncode, stderr_text = await _run_once(session_id)
    used_fresh_session_fallback = False

    if session_id and returncode != 0 and not stderr_text and not final_text:
        log.info("resume failed without stderr, retrying with a fresh session")
        final_text, new_session_id, returncode, stderr_text = await _run_once(None)
        used_fresh_session_fallback = True

    if returncode != 0:
        detail = stderr_text or "no stderr"
        if final_text:
            return final_text, new_session_id, used_fresh_session_fallback
        raise RuntimeError(f"claude exited with code {returncode}: {detail}")

    return final_text, new_session_id, used_fresh_session_fallback
