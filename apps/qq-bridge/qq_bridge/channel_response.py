# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ implementation of the shared ``ChannelResponse`` output port.

Renders one assistant turn to QQ (OneBot): plaintext segments (no card editing), a single
generic tool ping unless ``show_tool_calls``, and outbound media as base64 OneBot segments.
The streamed text is segmented by the shared ``StreamingTextBuffer`` (same flusher Feishu
uses). Attachment directives (``[[rtime-send-image/file]]``) are stripped from the visible
text and collected, then sent on ``finalize``. Tracks ``output_chars`` / ``attachments_sent``
for the run log.
"""

from __future__ import annotations

import logging
import os

from rtime_chat_runtime.attachment_directives import (
    extract_attachment_directives,
    validate_attachment_path,
)
from rtime_chat_runtime.channel_display import StreamingTextBuffer
from rtime_chat_runtime.output_render import RenderPolicy, render

from .media import to_base64_uri
from .onebot.protocol import file_upload_action, image_send_action
from .output_qq import split_for_qq

log = logging.getLogger("qq_bridge.response")


def tool_status_line(name: str, inp: dict) -> str:
    """A short, user-facing status line for a model tool call (when show_tool_calls)."""
    n = (name or "").lower()
    if "lib_" in n or "library" in n or n.startswith("mcp__"):
        return "📚 查阅 brain…"
    if n in ("read", "read_file"):
        return f"📄 读取 {os.path.basename(str(inp.get('file_path', inp.get('path', ''))))}"
    if n == "grep":
        return f"🔎 检索「{str(inp.get('pattern', ''))[:24]}」"
    if n == "glob":
        return f"🔍 查找 {str(inp.get('pattern', ''))[:24]}"
    if n == "bash":
        return f"🔧 执行 {str(inp.get('command', ''))[:36]}"
    if n in ("webfetch", "websearch"):
        return "🌐 联网查询…"
    return f"⚙️ {name}"


class QQChannelResponse:
    """A ``ChannelResponse`` for one QQ turn. ``reply`` sends text; ``send_action`` is the
    raw OneBot sender for outbound media (None in unit tests => media skipped)."""

    def __init__(self, reply, send_action, msg, config) -> None:
        self._reply = reply
        self._send_action = send_action
        self._msg = msg
        self._config = config
        self._buf = StreamingTextBuffer()
        self._directives: list = []
        self._pinged = False
        self.output_chars = 0
        self.attachments_sent = 0

    async def progress(self, text: str) -> None:
        await self._reply(text)

    async def _emit(self, text: str) -> None:
        clean, directives = extract_attachment_directives(text)
        self._directives.extend(directives)
        # QQ = plain_text renderer. profile.output.render feeds this policy (T2/T5b);
        # QQ's default stays plain_text so behavior is identical to the T0 strip.
        clean = render(clean, RenderPolicy.PLAIN_TEXT).strip()
        if clean:
            for piece in split_for_qq(clean):
                await self._reply(piece)
            self.output_chars += len(clean)

    async def segment(self, text: str) -> None:
        for seg in self._buf.feed(text):
            await self._emit(seg)

    async def tool(self, name: str, tool_input: dict) -> None:
        if not tool_input:
            return
        log.debug("tool: %s %s", name, str(tool_input)[:240])
        if self._config.show_tool_calls:
            await self._reply(tool_status_line(name, tool_input))
        elif not self._pinged:  # one generic ping, never the actual command/code
            self._pinged = True
            await self._reply("🔍 正在查阅资料…")

    async def attachment(self, kind: str, path: str) -> None:
        await self._send_one(kind, path)

    async def finalize(self, text: str = "") -> None:
        # Flush the streamed tail, or (non-streaming) the whole final text passed in.
        tail = self._buf.drain() if self._buf.full_text else text
        await self._emit(tail or "")
        if self.output_chars == 0 and not self._directives:
            await self._reply("（无输出）")
        for directive in self._directives:
            await self._send_one(directive.kind, directive.path)

    async def error(self, text: str) -> None:
        await self._reply(text if text.startswith("⚠️") else f"⚠️ {text}")

    # -- outbound media -----------------------------------------------------
    async def _send_one(self, kind: str, path: str) -> None:
        if not self._send_action or not self._config.send_media:
            return
        kind = "image" if kind == "image" else "file"
        v = validate_attachment_path(
            path,
            base_dir=self._config.default_cwd or None,
            kind=kind,
            max_bytes=self._config.max_download_bytes,
        )
        if not v.ok:
            await self._reply(f"⚠️ 附件未发送：{v.reason}")
            return
        try:
            uri = to_base64_uri(v.path)
        except OSError as exc:
            await self._reply(f"⚠️ 附件读取失败：{exc}")
            return
        if kind == "image":
            action, params = image_send_action(self._msg, uri)
        else:
            action, params = file_upload_action(
                self._msg, uri, os.path.basename(v.path)
            )
        try:
            await self._send_action(action, params)
            self.attachments_sent += 1
        except Exception as exc:  # noqa: BLE001
            log.warning("outbound %s send failed: %s", kind, exc)
            await self._reply(f"⚠️ 附件发送失败：{type(exc).__name__}")
