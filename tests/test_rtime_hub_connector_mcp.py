# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from rtime_hub_connector.mcp_server import PROTOCOL_VERSION, RtimeHubConnectorMCP
from test_rtime_hub_connector_cli import _make_hub_fixture


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: RtimeHubConnectorMCP, name: str, arguments: dict) -> dict:
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
    server = RtimeHubConnectorMCP()

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
    assert {"hub_doctor", "hub_scan", "hub_panel", "hub_contacts"} <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = RtimeHubConnectorMCP()

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


def test_mcp_scan_panel_contacts_and_run_log(tmp_path, monkeypatch):
    hub = _make_hub_fixture(tmp_path)
    log_path = tmp_path / "hub-mcp.jsonl"
    monkeypatch.setenv("RTIME_HUB_MCP_RUN_LOG", str(log_path))
    server = RtimeHubConnectorMCP()

    scan = _call_tool(server, "hub.scan", {"root": str(hub), "sample_limit": 5})
    panel = _call_tool(server, "hub.panel", {"root": str(hub), "sample_limit": 5})
    contacts = _call_tool(server, "hub.contacts", {"root": str(hub), "sample_limit": 5})

    assert scan["ok"] is True
    assert panel["counts"]["projects"] >= 2
    assert contacts["count"] == 1
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "hub.scan"
    assert records[0]["permission_tier"] == "read_only"
    assert "structuredContent" not in records[0]


def test_mcp_tool_error_for_missing_root(tmp_path):
    server = RtimeHubConnectorMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "hub.scan", "arguments": {"root": str(tmp_path / "missing")}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "not a directory" in response["result"]["structuredContent"]["error"]


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    hub = _make_hub_fixture(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{ROOT / 'packages' / 'rtime-hub-connector' / 'src'}"
        f"{os.pathsep}{env.get('PYTHONPATH', '')}"
    )
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
                "name": "hub.panel",
                "arguments": {"root": str(hub), "sample_limit": 5},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "rtime_hub_connector.mcp_server"],
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
    hub = _make_hub_fixture(tmp_path)
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    wrapper = ROOT / "plugins" / "rtime-hub-connector" / "scripts" / "rtime-hub-mcp.sh"
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
                        "name": "hub.contacts",
                        "arguments": {"root": str(hub), "sample_limit": 5},
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
    assert responses[-1]["result"]["structuredContent"]["count"] == 1


@pytest.mark.parametrize("method", ["unknown/method", "resources/list"])
def test_mcp_unknown_request_returns_jsonrpc_error(method):
    server = RtimeHubConnectorMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
