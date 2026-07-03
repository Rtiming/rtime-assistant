# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import io
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "assistant-gateway"))

import rtime_chat  # noqa: E402


def test_build_body_minimal():
    body = rtime_chat.build_body("能带是什么")
    assert body["schema_version"] == 1
    assert body["message"] == "能带是什么"
    assert body["options"]["task_mode"] == "ask"
    assert body["entry"] == "rtime-chat"
    assert body["context"] == {}
    assert "stream" not in body


def test_build_body_pdf_page_stream():
    body = rtime_chat.build_body("这页讲什么", pdf="lesson2-main.pdf", page=5, stream=True)
    assert body["context"]["active_file"]["path"] == "lesson2-main.pdf"
    assert body["context"]["pdf"] == {"page": 5}
    assert body["stream"] is True


def test_build_body_note_truncation():
    body = rtime_chat.build_body(
        "总结", note_path="a.md", note_text="x" * 300, max_note_chars=100
    )
    note = body["context"]["note"]
    assert len(note["text"]) == 100
    assert note["truncated"] is True
    assert body["context"]["active_file"]["path"] == "a.md"


def test_build_body_selection_task():
    body = rtime_chat.build_body(None, selection="德拜模型", task_mode="explain")
    assert body["message"] == ""
    assert body["context"]["selection"]["text"] == "德拜模型"
    assert body["options"]["task_mode"] == "explain"


def test_build_body_conversation_and_history():
    history = [
        {"role": "user", "content": "什么是德拜模型？"},
        {"role": "assistant", "content": "德拜模型是……"},
        {"role": "system", "content": "丢弃"},
        {"role": "user", "content": "  "},
        "junk",
    ]
    body = rtime_chat.build_body(
        "它的低温极限呢", conversation_id="conv-7", history=history
    )
    assert body["conversation_id"] == "conv-7"
    assert body["context"]["history"] == [
        {"role": "user", "content": "什么是德拜模型？"},
        {"role": "assistant", "content": "德拜模型是……"},
    ]


def test_build_body_without_session_fields_unchanged():
    body = rtime_chat.build_body("问题")
    assert "conversation_id" not in body
    assert "history" not in body["context"]


def make_stream(*events) -> io.BytesIO:
    payload = b"".join(
        b"data: " + json.dumps(event, ensure_ascii=False).encode("utf-8") + b"\n\n"
        for event in events
    )
    return io.BytesIO(payload)


def test_iter_sse_events_roundtrip():
    events = [
        {"type": "status", "text": "正在使用Read…"},
        {"type": "delta", "text": "能带"},
        {"type": "done", "answer": "能带……", "sources": [{"path": "a.pdf", "page": 3}]},
    ]
    parsed = list(rtime_chat.iter_sse_events(make_stream(*events)))
    assert parsed == events


def test_iter_sse_events_trailing_and_junk():
    stream = io.BytesIO(
        b": ping\n\n"  # 注释帧
        b"data: not-json\n\n"  # 坏载荷
        b'data: {"type": "done", "answer": "x"}'  # 结尾缺空行
    )
    parsed = list(rtime_chat.iter_sse_events(stream))
    assert parsed == [{"type": "done", "answer": "x"}]


def test_consume_stream_collects_done_json(capsys):
    stream = make_stream(
        {"type": "status", "text": "正在使用Read…"},
        {"type": "delta", "text": "能带"},
        {"type": "done", "answer": "能带……", "sources": [{"path": "a.pdf", "page": 3}]},
    )
    code = rtime_chat.consume_stream(stream, as_json=True)
    captured = capsys.readouterr()
    assert code == 0
    payload = json.loads(captured.out.strip())
    assert payload == {"answer": "能带……", "sources": [{"path": "a.pdf", "page": 3}]}
    assert "正在使用Read…" in captured.err


def test_consume_stream_error_event(capsys):
    stream = make_stream({"type": "error", "message": "模型响应超时"})
    code = rtime_chat.consume_stream(stream, as_json=False)
    captured = capsys.readouterr()
    assert code == 1
    assert "模型响应超时" in captured.err


def test_consume_stream_missing_done(capsys):
    stream = make_stream({"type": "delta", "text": "半截"})
    code = rtime_chat.consume_stream(stream, as_json=False)
    captured = capsys.readouterr()
    assert code == 1
    assert "半截" in captured.out
    assert "done" in captured.err


def test_render_final_with_sources():
    out = rtime_chat.render_final(
        {"answer": "答案", "sources": [{"path": "a.pdf", "page": 3}, {"path": "b.md"}]}
    )
    assert "答案" in out
    assert "- a.pdf#page=3" in out
    assert "- b.md" in out


def test_main_requires_input():
    with pytest.raises(SystemExit) as excinfo:
        rtime_chat.main([])
    assert excinfo.value.code == 2
