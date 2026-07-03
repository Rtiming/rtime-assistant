# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared JSON-over-SSE frame helpers (server-side write + client-side parse).

The SSE chat protocol (typed events ``status | delta | done | error``, one JSON
object per ``data:`` frame) was proven by apps/assistant-gateway and is reused
verbatim by apps/web-chat (T5a, docs/design/mainline-profiles-and-entries-2026-07
§5.1). This module is the extracted single source for writing/parsing those
frames so new HTTP channels don't re-implement them.

NOTE (unification deferred): apps/assistant-gateway still carries its own inline
``_sse`` / ``_start_sse`` / ``iter_sse_events`` — byte-for-byte the same wire
format. It is deliberately NOT switched over this round (do not destabilize the
production gateway from a sibling worktree); fold it in as a later chore.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator

#: Response headers that start an SSE stream (paired with a 200 status).
#: CORS is wide open — same as assistant-gateway — because browser pages use
#: fetch()+ReadableStream over POST (EventSource is GET-only) and the bind
#: address is the actual access gate (127.0.0.1 / tailnet by deployment).
SSE_RESPONSE_HEADERS: tuple[tuple[str, str], ...] = (
    ("Content-Type", "text/event-stream; charset=utf-8"),
    ("Cache-Control", "no-cache"),
    ("Connection", "close"),
)

CORS_HEADERS: tuple[tuple[str, str], ...] = (
    ("Access-Control-Allow-Origin", "*"),
    ("Access-Control-Allow-Methods", "GET, POST, OPTIONS"),
    ("Access-Control-Allow-Headers", "Content-Type, Accept"),
)


def format_sse_frame(obj: dict) -> bytes:
    """One event object -> one wire frame: ``data: <json>\\n\\n`` (UTF-8, 中文原样)."""
    return f"data: {json.dumps(obj, ensure_ascii=False)}\n\n".encode()


def write_sse_frame(wfile, obj: dict) -> None:
    """Write + flush one frame to a socket-backed file object (raises OSError if gone)."""
    wfile.write(format_sse_frame(obj))
    wfile.flush()


def start_sse(handler, *, cors: bool = True) -> None:
    """Send SSE response headers once on a ``BaseHTTPRequestHandler``.

    Idempotent via ``handler._sse_started`` (same contract as assistant-gateway:
    queued/slow work may try to start the stream more than once)."""
    if getattr(handler, "_sse_started", False):
        return
    handler.send_response(200)
    for name, value in SSE_RESPONSE_HEADERS:
        handler.send_header(name, value)
    if cors:
        for name, value in CORS_HEADERS:
            handler.send_header(name, value)
    handler.end_headers()
    handler._sse_started = True


def iter_sse_events(fp: Iterable) -> Iterator[dict]:
    """Parse an SSE byte/text stream: blank line ends a frame, ``data:`` lines carry JSON.

    Mirrors the reference client parser (apps/assistant-gateway/rtime_chat.py):
    a trailing frame without the final blank line is still yielded; comment lines
    and non-JSON payloads are skipped, never raised."""

    def flush(lines: list[str]) -> Iterator[dict]:
        if not lines:
            return
        try:
            yield json.loads("\n".join(lines))
        except json.JSONDecodeError:
            return

    data_lines: list[str] = []
    for raw in fp:
        line = raw.decode("utf-8", "replace") if isinstance(raw, (bytes, bytearray)) else raw
        line = line.rstrip("\r\n")
        if not line:
            yield from flush(data_lines)
            data_lines = []
        elif line.startswith("data:"):
            data_lines.append(line[5:].lstrip())
    yield from flush(data_lines)
