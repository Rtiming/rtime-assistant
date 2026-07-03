# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtime_agent_control.mcp_server import PROTOCOL_VERSION, RtimeAgentControlMCP
from test_rtime_agent_control_cli import _make_repo_fixture


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: RtimeAgentControlMCP, name: str, arguments: dict) -> dict:
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
    server = RtimeAgentControlMCP()

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
    assert {
        "agent_doctor",
        "agent_tooling",
        "agent_config_render",
        "agent_validation_plan",
        "agent_context_plan",
        "agent_runtime_snapshot",
    } <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = RtimeAgentControlMCP()

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


def test_mcp_tools_and_metadata_only_run_log(tmp_path, monkeypatch):
    repo = _make_repo_fixture(tmp_path)
    agent_log = tmp_path / "agent-control-mcp.jsonl"
    monkeypatch.setenv("RTIME_AGENT_CONTROL_MCP_RUN_LOG", str(agent_log))
    server = RtimeAgentControlMCP()

    doctor = _call_tool(server, "agent.doctor", {"repo_root": str(repo)})
    tooling = _call_tool(server, "agent.tooling", {"repo_root": str(repo)})
    rendered = _call_tool(
        server,
        "agent.config_render",
        {"repo_root": str(repo), "tools": ["rtime-agent-control"]},
    )
    context = _call_tool(
        server,
        "agent.context_plan",
        {"repo_root": str(repo), "request": "secret request body should not be logged"},
    )
    snapshot = _call_tool(
        server,
        "agent.runtime_snapshot",
        {"repo_root": str(repo), "run_log": str(tmp_path / "run.jsonl")},
    )

    assert doctor["ok"] is True
    assert tooling["ok"] is True
    assert rendered["write_enabled"] is False
    assert context["request_length"] == len("secret request body should not be logged")
    assert snapshot["live_service_state_checked"] is False

    records = [json.loads(line) for line in agent_log.read_text(encoding="utf-8").splitlines()]
    assert [record["tool"] for record in records] == [
        "agent.doctor",
        "agent.tooling",
        "agent.config_render",
        "agent.context_plan",
        "agent.runtime_snapshot",
    ]
    assert records[0]["permission_tier"] == "read_only"
    assert records[3]["request_length"] == len("secret request body should not be logged")
    assert "secret request body" not in agent_log.read_text(encoding="utf-8")


def test_mcp_validation_plan_and_tool_error(tmp_path):
    repo = _make_repo_fixture(tmp_path)
    server = RtimeAgentControlMCP()

    plan = _call_tool(server, "agent.validation_plan", {"repo_root": str(repo), "module": "agent-control"})
    assert plan["ok"] is True
    assert plan["executed"] is False

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "agent.context_plan", "arguments": {"repo_root": str(repo)}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "request" in response["result"]["structuredContent"]["error"]


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    repo = _make_repo_fixture(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'rtime-agent-control' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
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
                "name": "agent.tooling",
                "arguments": {"repo_root": str(repo)},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "rtime_agent_control.mcp_server"],
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
    server = RtimeAgentControlMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
