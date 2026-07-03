# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtime_context.mcp_server import PROTOCOL_VERSION, RtimeContextMCP
from test_rtime_context_cli import _make_workspace


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: RtimeContextMCP, name: str, arguments: dict) -> dict:
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
    server = RtimeContextMCP()

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
    assert {"context_doctor", "context_plan", "context_pack", "context_explain"} <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = RtimeContextMCP()

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


def test_mcp_plan_pack_explain_and_run_log(tmp_path, monkeypatch):
    workspace = _make_workspace(tmp_path)
    log_path = tmp_path / "context-mcp.jsonl"
    monkeypatch.setenv("RTIME_CONTEXT_MCP_RUN_LOG", str(log_path))
    server = RtimeContextMCP()

    request = "帮我 review Obsidian Zotero citation 状态"
    plan = _call_tool(server, "context.plan", {"request": request, "workspace": str(workspace)})
    pack = _call_tool(server, "context.pack", {"request": request, "workspace": str(workspace)})
    explain = _call_tool(server, "context.explain", {"request": request, "workspace": str(workspace)})

    assert plan["ok"] is True
    assert "literature" in plan["task_signals"]["groups"]
    literature_lane = next(lane for lane in plan["lanes"] if lane["lane"] == "Brain / Knowledge Store")
    assert "brain-citation panel" in literature_lane["recommended_tools"]
    assert pack["kind"] == "context_pack_skeleton"
    assert explain["risk"] == "literature_or_citation"
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "context.plan"
    assert records[0]["permission_tier"] == "read_only"
    assert "request_preview" not in records[0]


def test_mcp_tool_error_for_missing_request():
    server = RtimeContextMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "context.plan", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "request" in response["result"]["structuredContent"]["error"]


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    workspace = _make_workspace(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'rtime-context' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
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
                "name": "context.pack",
                "arguments": {"request": "检查 rtime-hub 项目状态", "workspace": str(workspace)},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "rtime_context.mcp_server"],
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


def test_plugin_mcp_wrapper_roundtrip(tmp_path):
    workspace = _make_workspace(tmp_path)
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    wrapper = ROOT / "plugins" / "rtime-context" / "scripts" / "rtime-context-mcp.sh"
    payload = "\n".join(
        [
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "initialize",
                    "params": {
                        "protocolVersion": PROTOCOL_VERSION,
                        "capabilities": {},
                        "clientInfo": {"name": "pytest", "version": "0"},
                    },
                }
            ),
            json.dumps(
                {
                    "jsonrpc": "2.0",
                    "id": 2,
                    "method": "tools/call",
                    "params": {
                        "name": "context.explain",
                        "arguments": {"request": "检查 runtime logs", "workspace": str(workspace)},
                    },
                }
            ),
        ]
    )

    completed = subprocess.run(
        [str(wrapper)],
        input=f"{payload}\n",
        text=True,
        capture_output=True,
        env=env,
        check=False,
        timeout=20,
    )

    assert completed.returncode == 0
    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert responses[-1]["result"]["structuredContent"]["risk"] == "runtime_evidence"


@pytest.mark.parametrize("method", ["unknown/method", "resources/list"])
def test_mcp_unknown_request_returns_jsonrpc_error(method):
    server = RtimeContextMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
