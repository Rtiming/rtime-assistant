# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""FakeModelRunner — in-process test double for the shared CLI model runner.

Mirrors the call signature of :func:`rtime_chat_runtime.model_runner.run_claude`
(same positional ``message``, same keyword-only parameters, same
``(full_text, new_session_id, used_fresh_session_fallback)`` return shape) so a
bridge can be pointed at it via its explicit model-runner injection point (e.g.
``qq_bridge.app.build_model_handler(config, model_runner=FakeModelRunner(...))``)
and the whole processing chain runs without a network hop or subprocess. ``cli`` is
accepted but optional so it also drops into the Feishu bridge, whose wrapper
(``feishu-bridge/claude_runner.run_claude``) defaults ``cli`` internally and is
called as ``run_claude_func(message=..., ...)`` without it.

It records EVERYTHING the bridge hands the runner — model, system prompt,
allowed/disallowed tools, permission mode, MCP config, cwd, session id — as
:class:`RecordedModelCall` entries, which is the assertion surface for the
模型选择 / 提示词 / 库scope faces of the simulation harness (design doc §3.2).

Replies are scripted per test via :class:`ScriptedReply` (or plain strings):
plain text, markdown-laden text for renderer assertions, ``[[rtime-send-*]]``
media directives, streamed chunks and tool-call callbacks are all expressible.

``on_process_start`` is never fired (there is no subprocess); callers that rely
on it for stop-support must be exercised against the real runner.
"""

from __future__ import annotations

import inspect
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any


async def _fire(cb, *args) -> None:
    """Invoke a sync-or-async callback (same tolerance as the real runner)."""
    if cb is None:
        return
    result = cb(*args)
    if inspect.isawaitable(result):
        await result


@dataclass(frozen=True)
class ScriptedReply:
    """One scripted model turn.

    ``chunks`` — when the caller wires ``on_text_chunk`` (streaming), these are fed
    in order; default (None) streams ``text`` as a single chunk. ``tool_calls`` are
    fired via ``on_tool_use`` before any text, mirroring the usual CLI event order.
    """

    text: str = "OK"
    session_id: str | None = "sess-fake"
    used_fresh_session_fallback: bool = False
    chunks: tuple[str, ...] | None = None
    tool_calls: tuple[tuple[str, dict], ...] = ()


@dataclass(frozen=True)
class RecordedModelCall:
    """Everything one runner invocation received (the harness assertion surface)."""

    prompt: str
    cli: str | None
    permission_mode: str | None
    session_id: str | None
    model: str | None
    cwd: str | None
    system_prompt: str | None
    mcp_config: str | None
    allowed_tools: list[str] | None
    disallowed_tools: list[str] | None
    max_seconds: float | None
    streaming: bool  # whether on_text_chunk / on_tool_use callbacks were wired
    extra_kwargs: dict[str, Any] = field(default_factory=dict)


class FakeModelRunner:
    """Awaitable double for ``run_claude``: records calls, plays scripted replies.

    ``FakeModelRunner("答案")`` returns 答案 on every call; pass several replies
    (strings or :class:`ScriptedReply`) to script consecutive turns — the last
    entry repeats once the script is exhausted.
    """

    def __init__(self, *replies: str | ScriptedReply) -> None:
        script = [
            r if isinstance(r, ScriptedReply) else ScriptedReply(text=r)
            for r in replies
        ]
        self._script: list[ScriptedReply] = script or [ScriptedReply()]
        self._cursor = 0
        self.calls: list[RecordedModelCall] = []

    @property
    def last(self) -> RecordedModelCall:
        if not self.calls:
            raise AssertionError("FakeModelRunner was never called")
        return self.calls[-1]

    def _next_reply(self) -> ScriptedReply:
        reply = self._script[min(self._cursor, len(self._script) - 1)]
        self._cursor += 1
        return reply

    async def __call__(
        self,
        message: str,
        *,
        cli: str
        | None = None,  # QQ passes it; the Feishu wrapper defaults it internally
        permission_mode: str | None = "default",
        session_id: str | None = None,
        model: str | None = None,
        cwd: str | None = None,
        system_prompt: str | None = None,
        mcp_config: str | None = None,
        allowed_tools: Sequence[str] | None = None,
        disallowed_tools: Sequence[str] | None = None,
        on_text_chunk=None,
        on_tool_use=None,
        on_process_start=None,  # never fired: no subprocess exists
        max_seconds: float | None = None,
        **extra_kwargs: Any,
    ) -> tuple[str, str | None, bool]:
        self.calls.append(
            RecordedModelCall(
                prompt=message,
                cli=cli,
                permission_mode=permission_mode,
                session_id=session_id,
                model=model,
                cwd=cwd,
                system_prompt=system_prompt,
                mcp_config=mcp_config,
                allowed_tools=list(allowed_tools)
                if allowed_tools is not None
                else None,
                disallowed_tools=(
                    list(disallowed_tools) if disallowed_tools is not None else None
                ),
                max_seconds=max_seconds,
                streaming=(on_text_chunk is not None or on_tool_use is not None),
                extra_kwargs=dict(extra_kwargs),
            )
        )
        reply = self._next_reply()
        for name, tool_input in reply.tool_calls:
            await _fire(on_tool_use, name, tool_input)
        if on_text_chunk is not None:
            chunks = reply.chunks if reply.chunks is not None else (reply.text,)
            for chunk in chunks:
                if chunk:
                    await _fire(on_text_chunk, chunk)
        return reply.text, reply.session_id, reply.used_fresh_session_fallback


__all__ = ["FakeModelRunner", "RecordedModelCall", "ScriptedReply"]
