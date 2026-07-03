# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared output port for chat channels (channel-unification: the "ChannelDisplay" port).

The bottom→model orchestration is the same across channels; what differs is *rendering* —
Feishu edits a card, QQ sends plaintext segments. ``ChannelResponse`` is the contract both
implement so a turn's output (status / streamed text / tool notices / attachments / final /
error) is expressed once and rendered per-channel. ``StreamingTextBuffer`` is the reusable
chunk→segment flusher (paragraph- and length-bounded) both channels feed their stream into.

This module is pure (no I/O, no channel deps) so it is unit-testable and importable by any
bridge. The per-channel adapters live in each bridge; the orchestration loop stays per-channel
for now (they diverge: QQ media/debounce vs Feishu cards/Ask) — only the output is unified.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

DEFAULT_FLUSH_CHARS = 360  # stream a segment once this much text has no paragraph break
DEFAULT_MIN_SENTENCE = (
    80  # only cut at sentence end if the segment is at least this long
)


@runtime_checkable
class ChannelResponse(Protocol):
    """One assistant reply in progress. A channel implements these to render a turn."""

    async def progress(self, text: str) -> None:
        """Transient status (思考中 / 查阅中). Feishu: update card; QQ: a status line."""

    async def segment(self, text: str) -> None:
        """A chunk of the answer body, ready to show."""

    async def tool(self, name: str, tool_input: dict) -> None:
        """A model tool call happened (surface or summarize per channel policy)."""

    async def attachment(self, kind: str, path: str) -> None:
        """Send a validated local image/file back to the chat."""

    async def finalize(self, text: str = "") -> None:
        """End of turn: flush any remaining text (``text`` = the unsent tail, may be empty)."""

    async def error(self, text: str) -> None:
        """Surface an error/warning to the user."""


class StreamingTextBuffer:
    """Accumulate streamed text and emit display-ready segments at paragraph (``\\n\\n``) or
    length boundaries (cutting on the last sentence end when long). Channel-agnostic — the
    flusher both bridges share so segmentation behaves identically everywhere."""

    _SENTENCE_ENDS = ("。", "！", "？", "\n", "; ")

    def __init__(
        self,
        flush_chars: int = DEFAULT_FLUSH_CHARS,
        min_sentence: int = DEFAULT_MIN_SENTENCE,
    ) -> None:
        self._buf = ""
        self._sent = 0
        self._flush_chars = flush_chars
        self._min_sentence = min_sentence

    def feed(self, chunk: str) -> list[str]:
        """Add a chunk; return any segments now ready to emit (possibly none)."""
        self._buf += chunk
        segments: list[str] = []
        while True:
            pending = self._buf[self._sent :]
            newline = pending.find("\n\n")
            if newline >= 0:
                cut = newline + 2
            elif len(pending) >= self._flush_chars:
                end = max(pending.rfind(p) for p in self._SENTENCE_ENDS)
                cut = (end + 1) if end >= self._min_sentence else len(pending)
            else:
                break
            segments.append(pending[:cut])
            self._sent += cut
        return segments

    def drain(self) -> str:
        """Return the unsent tail (and mark it sent). Call at end of stream."""
        tail = self._buf[self._sent :]
        self._sent = len(self._buf)
        return tail

    @property
    def full_text(self) -> str:
        """Everything fed so far (authoritative final text)."""
        return self._buf
