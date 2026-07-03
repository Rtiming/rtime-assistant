# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-automation" / "src"


def _load_mcp():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_automation.mcp_server")


def _make_repo_fixture(root: Path) -> Path:
    from test_rtime_automation_cli import _make_repo_fixture as make

    return make(root)


def _make_reminders(path: Path) -> Path:
    from test_rtime_automation_cli import _make_reminders as make

    return make(path)


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
    server = mcp.RtimeAutomationMCP()

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


def test_tools_list_exposes_automation_tools():
    mcp = _load_mcp()
    server = mcp.RtimeAutomationMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {
        "automation_doctor",
        "automation_reminders",
        "automation_health",
        "automation_panel",
        "automation_plan",
    }


def test_call_plan_returns_structured_content_and_logs_metadata(tmp_path, monkeypatch):
    mcp = _load_mcp()
    repo = _make_repo_fixture(tmp_path)
    log_path = tmp_path / "automation-mcp.jsonl"
    monkeypatch.setenv("RTIME_AUTOMATION_MCP_RUN_LOG", str(log_path))
    server = mcp.RtimeAutomationMCP()

    data = _call_tool(
        server,
        "automation.plan",
        {"repo_root": str(repo), "request": "规划飞书提醒和定时任务"},
    )

    assert data["ok"] is True
    assert data["write_enabled"] is False
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "automation.plan"
    assert records[0]["permission_tier"] == "read_only"
    assert records[0]["input_paths"] == [str(repo)]
    assert "request" not in records[0]
    assert records[0]["request_length"] > 0


def test_call_reminders_does_not_return_message_or_target(tmp_path):
    mcp = _load_mcp()
    reminders = _make_reminders(tmp_path / "reminders.jsonl")
    server = mcp.RtimeAutomationMCP()

    data = _call_tool(server, "automation.reminders", {"path": str(reminders)})

    assert data["ok"] is True
    assert data["privacy"]["message_text_returned"] is False
    sample = data["due_samples"][0] if data["due_samples"] else data["risk_samples"][0]
    assert "message" not in sample
    assert "target" not in sample


def test_call_health_surfaces_failed_without_private_body(tmp_path):
    from test_rtime_automation_cli import _make_failed_reminders

    mcp = _load_mcp()
    reminders = _make_failed_reminders(tmp_path / "reminders.jsonl")
    server = mcp.RtimeAutomationMCP()

    data = _call_tool(server, "automation.health", {"path": str(reminders)})

    assert data["ok"] is False
    assert data["failed_count"] == 1
    assert "failed_reminders" in data["risks"]
    sample = data["failed_samples"][0]
    assert sample["id"] == "rtime-daily-digest"
    assert "message" not in sample
    assert "target" not in sample
    assert "last_error" not in sample
    assert data["privacy"]["last_error_message_returned"] is False


def test_wrapper_lists_tools():
    wrapper = ROOT / "plugins" / "rtime-automation" / "scripts" / "rtime-automation-mcp.sh"
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
    assert "automation_panel" in names
