# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ-side output rendering.

QQ has no markdown / cards / in-place edit, so the model's answer is sent as plain
text, split into chunks under a safe per-message size. LaTeX-unicode downgrade and
an options→numbered-menu are deferred (M3/M4); this is the M2 text-first renderer.

Models routinely ignore a "no markdown" system-prompt instruction and emit ``**bold**``,
``## headings``, ``> quotes``, ``[label](url)`` links etc., which QQ shows verbatim as
literal symbol noise. The markdown->plain-text downgrade is the ``plain_text`` policy of
the shared per-channel renderer (``rtime_chat_runtime.output_render``); it lives in the
runtime choke point so every plain-text channel shares it. ``strip_markdown_for_qq`` is
kept here as a thin alias for backward-compatible imports.
"""

from __future__ import annotations

from rtime_chat_runtime.output_render import strip_markdown_plain_text

QQ_MSG_SAFE_MAX_CHARS = 2500  # comfortably under QQ single-message limits

# Backward-compatible alias: the implementation now lives in the runtime renderer
# (rtime_chat_runtime.output_render.strip_markdown_plain_text) as the plain_text policy.
strip_markdown_for_qq = strip_markdown_plain_text


def split_for_qq(text: str, *, max_chars: int = QQ_MSG_SAFE_MAX_CHARS) -> list[str]:
    """Split ``text`` into QQ-sendable chunks, preferring paragraph boundaries."""
    text = (text or "").strip()
    if not text:
        return []
    if len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    current = ""
    for para in text.split("\n\n"):
        candidate = para if not current else current + "\n\n" + para
        if len(candidate) <= max_chars:
            current = candidate
            continue
        if current:
            chunks.append(current)
            current = ""
        while len(para) > max_chars:
            chunks.append(para[:max_chars])
            para = para[max_chars:]
        current = para
    if current:
        chunks.append(current)
    return chunks
