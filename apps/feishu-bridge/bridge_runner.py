# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Claude run orchestration and Feishu display policy."""

from __future__ import annotations

import asyncio
import contextlib
import os
import time
import traceback
from collections.abc import Awaitable, Callable
from typing import Any

from latency_trace import mark, mark_once
from latex_unicode import render_math_for_feishu
from output_policy import extract_options, format_tool, split_markdown_for_feishu_post
from rtime_chat_runtime.attachment_directives import (
    extract_attachment_directives,
    validate_attachment_path,
)
from rtime_chat_runtime.output_render import RenderPolicy, render
from rtime_chat_runtime.run_log import (
    append_run_event,
    hash_value,
    new_run_id,
    summarize_text,
)

RunClaudeFunc = Callable[..., Awaitable[tuple[str, str, bool]]]


async def run_and_display(
    *,
    user_id: str,
    chat_id: str,
    is_group: bool,
    text: str,
    card_msg_id: str,
    session: Any,
    notify_msg_id: str,
    feishu: Any,
    store: Any,
    active_runs: Any,
    run_claude_func: RunClaudeFunc,
    stream_chunk_size: int,
    segmented_output: bool,
    show_tool_calls: bool,
    allowed_tools: list[str] | None = None,
    disallowed_tools: list[str] | None = None,
    latency_trace: dict | None = None,
    status_heartbeat_seconds: float = 0.0,
) -> None:
    """Run Claude, stream user-facing output, update state, and write run logs."""
    run_id = new_run_id("feishu")
    active_run = active_runs.start_run(user_id, card_msg_id)

    accumulated = ""
    flushed_text_len = 0
    tool_history: list[str] = []
    tool_count = 0
    ask_options: list[tuple[str, str]] = []
    plan_exited = False
    last_push_time = 0.0
    last_push_len = 0
    push_failures = 0
    push_interval = 0.4
    max_stream_display = 2500
    max_attachment_bytes = int(os.getenv("FEISHU_OUTBOUND_ATTACHMENT_MAX_BYTES", str(30 * 1024 * 1024)))
    first_output_event = asyncio.Event()
    heartbeat_task: asyncio.Task | None = None

    append_run_event(
        "run_started",
        run_id=run_id,
        entry="feishu",
        actor_hash=hash_value(user_id),
        chat_hash=hash_value(chat_id),
        session_hash=hash_value(session.session_id),
        is_group=is_group,
        workspace=session.cwd,
        model=session.model or "wrapper-default",
        permission_mode=session.permission_mode,
        allowed_tools=allowed_tools or [],
        disallowed_tools=disallowed_tools or [],
        message_chars=len(text),
        message_preview=summarize_text(text),
    )
    mark(
        latency_trace,
        "run_started",
        run_id=run_id,
        model=session.model or "wrapper-default",
        permission_mode=session.permission_mode,
    )

    async def send_attachment(kind: str, raw_path: str) -> None:
        validation = validate_attachment_path(
            raw_path,
            base_dir=session.cwd,
            kind=kind,
            max_bytes=max_attachment_bytes,
        )
        if not validation.ok:
            reason = f"⚠️ 附件未发送：{validation.reason}"
            if is_group and notify_msg_id:
                await feishu.reply_text(notify_msg_id, reason)
            else:
                await feishu.send_text_to_user(user_id, reason)
            append_run_event(
                "attachment_send_rejected",
                run_id=run_id,
                entry="feishu",
                actor_hash=hash_value(user_id),
                kind=kind,
                path_hash=hash_value(raw_path),
                reason=validation.reason,
            )
            return

        started = time.monotonic()
        try:
            if kind == "image":
                if is_group and notify_msg_id:
                    await feishu.reply_image(notify_msg_id, validation.path)
                    operation = "reply_image"
                else:
                    await feishu.send_image_to_user(user_id, validation.path)
                    operation = "send_image_to_user"
            else:
                if is_group and notify_msg_id:
                    await feishu.reply_file(notify_msg_id, validation.path)
                    operation = "reply_file"
                else:
                    await feishu.send_file_to_user(user_id, validation.path)
                    operation = "send_file_to_user"
            dur_ms = int((time.monotonic() - started) * 1000)
            mark(latency_trace, "attachment_sent", operation=operation, kind=kind, dur_ms=dur_ms)
            append_run_event(
                "attachment_sent",
                run_id=run_id,
                entry="feishu",
                actor_hash=hash_value(user_id),
                kind=kind,
                operation=operation,
                file_name=os.path.basename(validation.path),
                file_size=validation.size,
                path_hash=hash_value(validation.path),
                dur_ms=dur_ms,
            )
        except Exception as exc:
            error_text = f"⚠️ 附件发送失败：{type(exc).__name__}: {exc}"
            if is_group and notify_msg_id:
                await feishu.reply_text(notify_msg_id, error_text)
            else:
                await feishu.send_text_to_user(user_id, error_text)
            append_run_event(
                "attachment_send_failed",
                run_id=run_id,
                entry="feishu",
                actor_hash=hash_value(user_id),
                kind=kind,
                file_name=os.path.basename(validation.path),
                file_size=validation.size,
                path_hash=hash_value(validation.path),
                error_type=type(exc).__name__,
                error_preview=summarize_text(str(exc)),
            )

    async def send_markdown_chunk(markdown: str) -> tuple[str, int]:
        started = time.monotonic()
        try:
            if is_group and notify_msg_id:
                await feishu.reply_markdown(notify_msg_id, markdown)
                operation = "reply_markdown"
            else:
                await feishu.send_markdown_to_user(user_id, markdown)
                operation = "send_markdown_to_user"
        except Exception as markdown_err:
            append_run_event(
                "feishu_render_fallback",
                run_id=run_id,
                entry="feishu",
                actor_hash=hash_value(user_id),
                from_type="interactive_markdown",
                to_type="text",
                error_type=type(markdown_err).__name__,
                error_preview=summarize_text(str(markdown_err)),
            )
            print(
                f"[warn] Markdown卡片发送失败，回退纯文本: {type(markdown_err).__name__}: {markdown_err}",
                flush=True,
            )
            if is_group and notify_msg_id:
                await feishu.reply_text(notify_msg_id, markdown)
                operation = "reply_text_fallback"
            else:
                await feishu.send_text_to_user(user_id, markdown)
                operation = "send_text_to_user_fallback"
        dur_ms = int((time.monotonic() - started) * 1000)
        return operation, dur_ms

    async def send_segment(content: str):
        segment_text, directives = extract_attachment_directives(content)
        if not segment_text:
            for directive in directives:
                await send_attachment(directive.kind, directive.path)
            return
        # Feishu = rich renderer. profile.output.render feeds this policy (T2/T5b);
        # Feishu's default stays rich (LaTeX->Unicode) so behavior is unchanged.
        segment_text = render(
            segment_text, RenderPolicy.RICH, rich_renderer=render_math_for_feishu
        )
        for chunk in split_markdown_for_feishu_post(segment_text):
            operation, dur_ms = await send_markdown_chunk(chunk)
            mark_once(latency_trace, "first_text_update", "first_text_update", operation=operation, dur_ms=dur_ms)
            mark(latency_trace, "text_update", operation=operation, dur_ms=dur_ms)
        for directive in directives:
            await send_attachment(directive.kind, directive.path)

    async def flush_new_text_segment():
        nonlocal flushed_text_len
        segment = accumulated[flushed_text_len:]
        if segment.strip():
            await send_segment(segment)
            flushed_text_len = len(accumulated)

    async def push(content: str):
        nonlocal push_failures
        if push_failures >= 3:
            return
        started = time.monotonic()
        try:
            await feishu.update_card(card_msg_id, content)
            push_failures = 0
            dur_ms = int((time.monotonic() - started) * 1000)
            mark_once(latency_trace, "first_card_update", "first_card_update", operation="update_card", dur_ms=dur_ms)
            mark(latency_trace, "card_update", operation="update_card", dur_ms=dur_ms)
        except Exception as push_err:
            push_failures += 1
            print(f"[warn] push 失败 ({push_failures}/3): {push_err}", flush=True)
            mark(latency_trace, "card_update_failed", operation="update_card", error_type=type(push_err).__name__)

    def build_display() -> str:
        parts = []
        if show_tool_calls and tool_history:
            parts.append("\n".join(tool_history[-5:]))
        if accumulated:
            if parts:
                parts.append("")
            display = accumulated
            if len(display) > max_stream_display:
                display = "...\n\n" + display[-max_stream_display:]
            parts.append(display)
        return "\n".join(parts) if parts else "⏳ 思考中..."

    async def on_tool_use(name: str, inp: dict):
        nonlocal accumulated, last_push_time, last_push_len, plan_exited, tool_count
        normalized = name.lower()
        if segmented_output:
            await flush_new_text_segment()
        if not inp:
            tool_count += 1
        if normalized == "exitplanmode":
            plan_exited = True
            return
        if normalized == "enterplanmode":
            if session.permission_mode != "plan":
                print("[Plan] EnterPlanMode 检测到，切换为 plan", flush=True)
                await store.set_permission_mode(user_id, chat_id, "plan")
            return
        if normalized == "enterworktree" and inp:
            worktree_name = inp.get("name", "")
            if worktree_name:
                print(f"[Worktree] 进入 worktree: {worktree_name}", flush=True)
            return
        if normalized == "exitworktree":
            print("[Worktree] 退出 worktree", flush=True)
            return
        if normalized == "askuserquestion":
            question = inp.get("question", inp.get("text", ""))
            if question:
                accumulated += f"\n\n❓ **等待回复：**\n{question}"
                detected = extract_options(question)
                if detected:
                    ask_options.clear()
                    ask_options.extend(detected)
                await push(build_display())
                last_push_time = time.time()
                last_push_len = len(accumulated)
                return
        if show_tool_calls:
            tool_line = format_tool(name, inp)
            if inp and tool_history:
                tool_history[-1] = tool_line
            else:
                tool_history.append(tool_line)
            await push(build_display())
            last_push_time = time.time()
            last_push_len = len(accumulated)

    async def on_text_chunk(chunk: str):
        nonlocal accumulated, last_push_time, last_push_len
        accumulated += chunk
        first_output_event.set()
        mark_once(latency_trace, "first_stdout", "first_stdout", output_chars=len(accumulated))
        now = time.time()
        enough_text = (
            stream_chunk_size > 0
            and len(accumulated) - last_push_len >= stream_chunk_size
        )
        if enough_text or now - last_push_time >= push_interval:
            if not segmented_output:
                await push(build_display())
            last_push_time = now
            last_push_len = len(accumulated)

    async def heartbeat_until_output(model_started: float) -> None:
        interval = max(0.0, float(status_heartbeat_seconds or 0))
        if interval <= 0:
            return
        while not first_output_event.is_set():
            await asyncio.sleep(interval)
            if first_output_event.is_set():
                return
            waited = int(time.time() - model_started)
            await push(f"⏳ 模型仍在处理中… 已等待 {waited}s")
            mark(latency_trace, "status_heartbeat", waited_seconds=waited)

    try:
        print("[run_claude] 开始调用...", flush=True)
        model_started = time.time()
        mark(latency_trace, "model_spawn_start")
        heartbeat_task = asyncio.create_task(heartbeat_until_output(model_started))

        def on_process_start(proc) -> None:
            active_runs.attach_process(user_id, proc)
            mark(latency_trace, "model_spawned")

        full_text, new_session_id, used_fresh_session_fallback = await run_claude_func(
            message=text,
            session_id=session.session_id,
            model=session.model,
            cwd=session.cwd,
            permission_mode=session.permission_mode,
            allowed_tools=allowed_tools,
            disallowed_tools=disallowed_tools,
            on_text_chunk=on_text_chunk,
            on_tool_use=on_tool_use,
            on_process_start=on_process_start,
        )
        first_output_event.set()
        print(f"[run_claude] 完成, session={new_session_id}", flush=True)
    except Exception as exc:
        if active_run.stop_requested:
            append_run_event(
                "run_stopped",
                run_id=run_id,
                entry="feishu",
                actor_hash=hash_value(user_id),
                chat_hash=hash_value(chat_id),
                tool_count=tool_count,
            )
            return
        print(f"[error] Claude 运行失败: {type(exc).__name__}: {exc}", flush=True)
        append_run_event(
            "run_failed",
            run_id=run_id,
            entry="feishu",
            actor_hash=hash_value(user_id),
            chat_hash=hash_value(chat_id),
            error_type=type(exc).__name__,
            error_preview=summarize_text(str(exc)),
            tool_count=tool_count,
        )
        traceback.print_exc()
        try:
            await feishu.update_card(card_msg_id, f"❌ Claude 执行出错：{type(exc).__name__}: {exc}")
        except Exception:
            pass
        return
    finally:
        first_output_event.set()
        if heartbeat_task is not None:
            heartbeat_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await heartbeat_task
        active_runs.clear_run(user_id, active_run)

    final = full_text or accumulated or "（无输出）"
    if used_fresh_session_fallback:
        warning = "⚠️ 检测到工作目录已变化，旧会话无法继续。本次已自动切换到新 session。"
        if segmented_output:
            await send_segment(warning)
        else:
            final = warning + "\n\n" + final

    final_directives = []
    if not segmented_output:
        final, final_directives = extract_attachment_directives(final)
        if not final.strip() and final_directives:
            final = "附件已准备好。"

    options = extract_options(final) or ask_options
    if segmented_output:
        if not accumulated.strip() and final.strip():
            accumulated = final
        await flush_new_text_segment()
        try:
            if options:
                buttons = [
                    {"text": display, "value": {"reply": value, "cid": chat_id}}
                    for display, value in options
                ]
                short = all(len(button["text"]) <= 10 for button in buttons)
                await feishu.update_card_with_buttons(
                    card_msg_id,
                    "请选择：",
                    buttons,
                    flow=short,
                )
            else:
                started = time.monotonic()
                await feishu.update_card(card_msg_id, "✅ 已完成")
                mark(latency_trace, "done_card_update", dur_ms=int((time.monotonic() - started) * 1000))
        except Exception:
            pass
    else:
        try:
            if options:
                buttons = [
                    {"text": display, "value": {"reply": value, "cid": chat_id}}
                    for display, value in options
                ]
                short = all(len(button["text"]) <= 10 for button in buttons)
                await feishu.update_card_with_buttons(card_msg_id, final, buttons, flow=short)
            else:
                started = time.monotonic()
                await feishu.update_card(card_msg_id, final)
                mark(latency_trace, "done_card_update", dur_ms=int((time.monotonic() - started) * 1000))
        except Exception as update_err:
            print(f"[error] 卡片更新失败，回退发文本: {update_err}", flush=True)
            try:
                if is_group and notify_msg_id:
                    await feishu.reply_card(notify_msg_id, content=final, loading=False)
                else:
                    await feishu.send_text_to_user(user_id, final)
            except Exception as fallback_err:
                print(f"[error] 文本回退也失败: {fallback_err}", flush=True)
        for directive in final_directives:
            await send_attachment(directive.kind, directive.path)

    if new_session_id:
        await store.on_claude_response(user_id, chat_id, new_session_id, text)

    append_run_event(
        "run_completed",
        run_id=run_id,
        entry="feishu",
        actor_hash=hash_value(user_id),
        chat_hash=hash_value(chat_id),
        session_hash=hash_value(new_session_id),
        output_chars=len(final),
        tool_count=tool_count,
        options_count=len(options),
        used_fresh_session_fallback=used_fresh_session_fallback,
        plan_exited=plan_exited,
        memory_candidate_count=0,
    )
    mark(
        latency_trace,
        "done",
        run_id=run_id,
        output_chars=len(final),
        tool_count=tool_count,
        options_count=len(options),
    )

    if plan_exited and session.permission_mode == "plan":
        print("[Plan] ExitPlanMode 检测到，切换为 bypassPermissions", flush=True)
        await store.set_permission_mode(user_id, chat_id, "bypassPermissions")
        try:
            notice = "🚀 已退出规划模式，发送任意消息开始执行。"
            if is_group and notify_msg_id:
                await feishu.reply_text(notify_msg_id, notice)
            else:
                await feishu.send_text_to_user(user_id, notice)
        except Exception:
            pass
