# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared CLI model runner: parse stream-json from a stub CLI, stream deltas, capture session."""

import asyncio
import json

from rtime_chat_runtime.model_runner import run_claude

_LINES = [
    {"type": "system", "session_id": "sess-x"},
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "你好"},
        },
    },
    {"type": "result", "session_id": "sess-x", "result": "你好，已答"},
]


def _stub_cli(tmp_path):
    """A fake claude CLI: consumes stdin, prints the stream-json lines, exits 0."""
    body = "import sys\nsys.stdin.read()\n"
    for ln in _LINES:
        body += f"print({json.dumps(json.dumps(ln, ensure_ascii=False))})\n"
    p = tmp_path / "stub_cli.py"
    p.write_text("#!/usr/bin/env python3\n" + body, encoding="utf-8")
    p.chmod(0o755)
    return str(p)


def test_run_claude_parses_streamjson(tmp_path):
    cli = _stub_cli(tmp_path)
    chunks: list[str] = []

    async def on_chunk(c):
        chunks.append(c)

    text, sid, used_fresh = asyncio.run(
        run_claude("hi", cli=cli, on_text_chunk=on_chunk)
    )
    assert text == "你好，已答"  # result event is authoritative
    assert sid == "sess-x"
    assert used_fresh is False
    assert "你好" in "".join(chunks)  # delta streamed to the callback


def test_run_claude_accepts_mcp_config(tmp_path):
    # mcp_config (strict, empty) must not break the runner; the stub ignores the flag.
    cli = _stub_cli(tmp_path)
    text, sid, _ = asyncio.run(
        run_claude("hi", cli=cli, mcp_config='{"mcpServers": {}}')
    )
    assert text == "你好，已答"


def test_run_claude_raises_on_bad_cli(tmp_path):
    err = None
    try:
        asyncio.run(run_claude("hi", cli=str(tmp_path / "does-not-exist")))
    except Exception as exc:  # FileNotFoundError from exec
        err = exc
    assert err is not None


def _hang_cli(tmp_path):
    """A fake CLI that reads stdin then hangs forever with no output and no children."""
    p = tmp_path / "hang_cli.py"
    p.write_text(
        "#!/usr/bin/env python3\nimport sys, time\nsys.stdin.read()\ntime.sleep(120)\n",
        encoding="utf-8",
    )
    p.chmod(0o755)
    return str(p)


def test_run_claude_wall_clock_timeout_kills_hung_run(tmp_path):
    # max_seconds bounds a run that produces no output and spawns no children (the case
    # IDLE_TIMEOUT alone would still eventually catch, but max_seconds is the hard cap).
    cli = _hang_cli(tmp_path)
    err = None
    try:
        asyncio.run(run_claude("hi", cli=cli, max_seconds=2))
    except RuntimeError as exc:
        err = exc
    assert err is not None and "墙钟" in str(err)
