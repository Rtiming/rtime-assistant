# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared output port: StreamingTextBuffer segmentation + ChannelResponse contract."""

from rtime_chat_runtime.channel_display import ChannelResponse, StreamingTextBuffer


def test_buffer_flushes_on_paragraph_break():
    buf = StreamingTextBuffer()
    assert buf.feed("第一段。") == []  # no break yet, below flush length
    out = buf.feed("\n\n第二段开头")
    assert out == ["第一段。\n\n"]  # paragraph boundary emitted, rest pending
    assert buf.drain() == "第二段开头"
    assert buf.full_text == "第一段。\n\n第二段开头"


def test_buffer_flushes_on_length_at_sentence_end():
    buf = StreamingTextBuffer(flush_chars=20, min_sentence=5)
    out = buf.feed("这是一段比较长的文字需要在这里断句。后面还有")
    assert out and out[0].endswith("。")  # cut at the sentence end once over flush_chars
    assert "".join(out) + buf.drain() == "这是一段比较长的文字需要在这里断句。后面还有"


def test_buffer_no_segment_until_threshold():
    buf = StreamingTextBuffer(flush_chars=1000)
    assert buf.feed("short") == []
    assert buf.feed(" more") == []
    assert buf.drain() == "short more"


def test_channel_response_protocol_runtime_checkable():
    class Dummy:
        async def progress(self, text): ...
        async def segment(self, text): ...
        async def tool(self, name, tool_input): ...
        async def attachment(self, kind, path): ...
        async def finalize(self, text=""): ...
        async def error(self, text): ...

    assert isinstance(Dummy(), ChannelResponse)
    assert not isinstance(object(), ChannelResponse)
