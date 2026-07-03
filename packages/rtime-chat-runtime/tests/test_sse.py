# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""SSE helper round-trip: format_sse_frame / write_sse_frame / start_sse / iter_sse_events."""

from __future__ import annotations

import io

from rtime_chat_runtime.sse import (
    CORS_HEADERS,
    SSE_RESPONSE_HEADERS,
    format_sse_frame,
    iter_sse_events,
    start_sse,
    write_sse_frame,
)


def test_frame_wire_format():
    frame = format_sse_frame({"type": "delta", "text": "你好"})
    assert frame.startswith(b"data: ")
    assert frame.endswith(b"\n\n")
    assert "你好".encode() in frame  # ensure_ascii=False: CJK stays readable


def test_roundtrip_single_frame():
    obj = {"type": "done", "answer": "答案\n第二行", "session_id": "s1"}
    assert list(iter_sse_events(io.BytesIO(format_sse_frame(obj)))) == [obj]


def test_roundtrip_many_frames():
    objs = [
        {"type": "status", "text": "已接收请求…"},
        {"type": "delta", "text": "第一段 with spaces  "},
        {"type": "delta", "text": 'quotes " and data: colons'},
        {"type": "done", "answer": "全文", "sources": [{"path": "a/b.md", "page": 3}]},
    ]
    stream = io.BytesIO(b"".join(format_sse_frame(o) for o in objs))
    assert list(iter_sse_events(stream)) == objs


def test_parser_handles_missing_trailing_blank_line():
    obj = {"type": "done", "answer": "x"}
    truncated = format_sse_frame(obj).rstrip(b"\n")  # no final blank line
    assert list(iter_sse_events(io.BytesIO(truncated))) == [obj]


def test_parser_skips_comments_and_garbage():
    stream = io.BytesIO(
        b": keepalive comment\n\n"
        b"data: {not json}\n\n"
        b"event: named\n"
        b'data: {"type": "delta", "text": "ok"}\n\n'
    )
    assert list(iter_sse_events(stream)) == [{"type": "delta", "text": "ok"}]


def test_parser_accepts_text_lines_and_crlf():
    lines = ['data: {"type": "status",\r\n', 'data: "text": "跨行"}\r\n', "\r\n"]
    assert list(iter_sse_events(lines)) == [{"type": "status", "text": "跨行"}]


def test_write_sse_frame_writes_and_flushes():
    class Sink(io.BytesIO):
        flushed = False

        def flush(self):
            self.flushed = True
            super().flush()

    sink = Sink()
    write_sse_frame(sink, {"type": "delta", "text": "x"})
    assert sink.getvalue() == format_sse_frame({"type": "delta", "text": "x"})
    assert sink.flushed


class FakeHandler:
    """Duck-typed BaseHTTPRequestHandler recording the response head."""

    def __init__(self):
        self.status = None
        self.headers: list[tuple[str, str]] = []
        self.ended = False

    def send_response(self, status):
        self.status = status

    def send_header(self, name, value):
        self.headers.append((name, value))

    def end_headers(self):
        self.ended = True


def test_start_sse_sends_headers_once():
    handler = FakeHandler()
    start_sse(handler)
    assert handler.status == 200
    assert handler.ended
    for pair in SSE_RESPONSE_HEADERS:
        assert pair in handler.headers
    for pair in CORS_HEADERS:
        assert pair in handler.headers
    sent = len(handler.headers)
    start_sse(handler)  # idempotent: second call is a no-op
    assert len(handler.headers) == sent


def test_start_sse_without_cors():
    handler = FakeHandler()
    start_sse(handler, cors=False)
    assert ("Content-Type", "text/event-stream; charset=utf-8") in handler.headers
    assert not any(name.startswith("Access-Control") for name, _ in handler.headers)
