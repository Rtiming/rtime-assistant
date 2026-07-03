# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from brain_library.mcp_server import PROTOCOL_VERSION, BrainLibraryMCP
from test_brain_library_cli import _make_brain_fixture

ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: BrainLibraryMCP, name: str, arguments: dict) -> dict:
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
    server = BrainLibraryMCP()

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
        "library_doctor",
        "library_scan",
        "library_docpacks",
        "library_index_status",
        "library_index_query",
    } <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = BrainLibraryMCP()

    initialize = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": "init",
            "method": "initialize",
            "params": {
                "protocolVersion": "2024-11-05",
                "capabilities": {},
                "clientInfo": {"name": "legacy-client", "version": "0"},
            },
        }
    )

    assert initialize is not None
    assert initialize["result"]["protocolVersion"] == "2024-11-05"


def test_mcp_scan_docpacks_and_run_log(tmp_path, monkeypatch):
    brain = _make_brain_fixture(tmp_path)
    log_path = tmp_path / "library-mcp.jsonl"
    monkeypatch.setenv("BRAIN_LIBRARY_MCP_RUN_LOG", str(log_path))
    server = BrainLibraryMCP()

    scan = _call_tool(server, "library.scan", {"root": str(brain), "sample_limit": 5})
    docpacks = _call_tool(server, "library.docpacks", {"root": str(brain), "sample_limit": 5})

    assert scan["ok"] is True
    assert scan["obsidian"]["wikilinks"] == 1
    assert docpacks["count"] == 2
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "library.scan"
    assert records[0]["permission_tier"] == "read_only"
    assert records[0]["status"] == "ok"


def test_mcp_tool_error_for_missing_root(tmp_path):
    server = BrainLibraryMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "library.scan", "arguments": {"root": str(tmp_path / "missing")}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "not a directory" in response["result"]["structuredContent"]["error"]


def test_mcp_index_status_and_query(tmp_path):
    from brain_library.indexer import build_index

    brain = _make_brain_fixture(tmp_path)
    index = tmp_path / "brain.sqlite"
    build = build_index(brain, index)
    assert build["ok"] is True
    server = BrainLibraryMCP()

    status = _call_tool(server, "library.index_status", {"index": str(index)})
    query = _call_tool(
        server,
        "library.index_query",
        {"index": str(index), "query": "stellarator coil", "limit": 2},
    )
    zh_query = _call_tool(
        server,
        "library.index_query",
        {"index": str(index), "query": "布里渊区", "limit": 5},
    )

    assert status["ok"] is True
    assert status["document_count"] >= 2
    assert query["ok"] is True
    assert query["result_count"] >= 1
    assert "stellarator" in query["results"][0]["snippet"].lower()
    assert zh_query["ok"] is True
    assert any(
        result["path"] == "knowledge/courses/solid-state-physics/slides/17布里渊区.md"
        for result in zh_query["results"]
    )


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    brain = _make_brain_fixture(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'brain-library' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
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
                "name": "library.scan",
                "arguments": {"root": str(brain), "sample_limit": 5},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "brain_library.mcp_server"],
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
    brain = _make_brain_fixture(tmp_path)
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    env["PYTHON"] = sys.executable
    wrapper = ROOT / "plugins" / "brain-library" / "scripts" / "brain-library-mcp.sh"
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
                        "name": "library.docpacks",
                        "arguments": {"root": str(brain), "sample_limit": 5},
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
    assert responses[-1]["result"]["structuredContent"]["count"] == 2


@pytest.mark.parametrize("method", ["unknown/method", "resources/list"])
def test_mcp_unknown_request_returns_jsonrpc_error(method):
    server = BrainLibraryMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
