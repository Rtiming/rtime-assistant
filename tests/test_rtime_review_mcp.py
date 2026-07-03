# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtime_review.mcp_server import PROTOCOL_VERSION, RtimeReviewMCP
from test_rtime_review_cli import _make_repo_fixture, _make_run_log


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: RtimeReviewMCP, name: str, arguments: dict) -> dict:
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": name, "arguments": arguments},
        }
    )
    assert response is not None
    assert "error" not in response
    return response["result"]["structuredContent"]


def test_mcp_initialize_and_tools_list():
    server = RtimeReviewMCP()

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )
    assert initialize is not None
    assert initialize["result"]["protocolVersion"] == PROTOCOL_VERSION

    tools = server.handle_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {"review_doctor", "review_panel", "review_audits", "review_run_logs", "review_tooling"} <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = RtimeReviewMCP()

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        }
    )

    assert initialize is not None
    assert initialize["result"]["protocolVersion"] == "2024-11-05"


def test_mcp_panel_run_logs_tooling_and_run_log(tmp_path, monkeypatch):
    repo = _make_repo_fixture(tmp_path)
    runtime_log = _make_run_log(tmp_path / "runtime.jsonl")
    context_log = _make_run_log(tmp_path / "context.jsonl")
    review_log = tmp_path / "review-mcp.jsonl"
    monkeypatch.setenv("RTIME_REVIEW_MCP_RUN_LOG", str(review_log))
    server = RtimeReviewMCP()

    panel = _call_tool(
        server,
        "review.panel",
        {
            "repo_root": str(repo),
            "runtime_log": str(runtime_log),
            "context_log": str(context_log),
            "log_limit": 1,
        },
    )
    audits = _call_tool(server, "review.audits", {"repo_root": str(repo)})
    logs = _call_tool(server, "review.run_logs", {"path": str(runtime_log)})
    tooling = _call_tool(server, "review.tooling", {"repo_root": str(repo)})

    assert panel["ok"] is False
    assert audits["count"] == 1
    assert logs["memory_candidate_total"] == 2
    assert tooling["ok"] is True
    records = [json.loads(line) for line in review_log.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "review.panel"
    assert records[0]["permission_tier"] == "read_only"


def test_mcp_tool_error_for_missing_log_path(tmp_path):
    server = RtimeReviewMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "review.run_logs", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "path" in response["result"]["structuredContent"]["error"]


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    repo = _make_repo_fixture(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'rtime-review' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["RTIME_ASSISTANT_ROOT"] = str(repo)
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "pytest", "version": "0"},
            },
        },
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {
                "name": "review.tooling",
                "arguments": {"repo_root": str(repo)},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "rtime_review.mcp_server"],
        input=payload,
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert [response["id"] for response in responses] == [1, 2, 3]
    assert responses[2]["result"]["structuredContent"]["ok"] is True


@pytest.mark.parametrize("method", ["unknown/method", "resources/list"])
def test_mcp_unknown_request_returns_jsonrpc_error(method):
    server = RtimeReviewMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
