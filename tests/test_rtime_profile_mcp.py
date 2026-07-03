# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-profile" / "src"


def _load_mcp():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_profile.mcp_server")


def _make_repo_fixture(root: Path) -> tuple[Path, Path]:
    from test_rtime_profile_cli import _make_repo_fixture as make

    return make(root)


def _call_tool(server, name: str, arguments: dict) -> dict:
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


def test_initialize_echoes_client_protocol_version():
    mcp = _load_mcp()
    server = mcp.RtimeProfileMCP()

    response = server.handle_message(
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

    assert response is not None
    assert response["result"]["protocolVersion"] == "2024-11-05"


def test_tools_list_exposes_profile_tools():
    mcp = _load_mcp()
    server = mcp.RtimeProfileMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {"profile_doctor", "profile_scan", "profile_panel", "profile_plan"}


def test_call_plan_returns_structured_content_and_logs_metadata(tmp_path, monkeypatch):
    mcp = _load_mcp()
    repo, brain = _make_repo_fixture(tmp_path)
    log_path = tmp_path / "profile-mcp.jsonl"
    monkeypatch.setenv("RTIME_PROFILE_MCP_RUN_LOG", str(log_path))
    server = mcp.RtimeProfileMCP()

    data = _call_tool(
        server,
        "profile.plan",
        {
            "repo_root": str(repo),
            "brain_root": str(brain),
            "request": "调整助手人格和模型策略",
        },
    )

    assert data["ok"] is True
    assert data["write_enabled"] is False
    categories = {item["category"] for item in data["recommended_changes"]}
    assert {"persona", "model"} <= categories

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "profile.plan"
    assert records[0]["permission_tier"] == "read_only"
    assert records[0]["input_paths"] == [str(repo), str(brain)]
    assert "request" not in records[0]
    assert records[0]["request_length"] > 0


def test_wrapper_lists_tools():
    wrapper = ROOT / "plugins" / "rtime-profile" / "scripts" / "rtime-profile-mcp.sh"
    if not wrapper.exists():
        return
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{env.get('PYTHONPATH', '')}"
    completed = subprocess.run(
        [str(wrapper)],
        input='{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n',
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        check=True,
    )

    data = json.loads(completed.stdout)
    names = {tool["name"] for tool in data["result"]["tools"]}
    assert "profile_panel" in names
