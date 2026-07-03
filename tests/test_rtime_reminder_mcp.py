# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MCP = ROOT / "deploy" / "bin" / "rtime-reminder-mcp"
REGISTER = ROOT / "deploy" / "bin" / "rtime-reminder-register"


def _call_mcp(messages: list[dict], env: dict[str, str]) -> list[dict]:
    proc = subprocess.run(
        [sys.executable, str(MCP)],
        input="\n".join(json.dumps(message) for message in messages) + "\n",
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )
    return [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]


def test_mcp_lists_tools_and_adds_reminder(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    env = os.environ.copy()
    env["RTIME_REMINDER_REGISTER"] = str(REGISTER)
    env["RTIME_REMINDER_DEFAULT_TARGET"] = "ou_test_user"

    responses = _call_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": "init",
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "pytest", "version": "0"},
                },
            },
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {
                    "name": "reminder.add",
                    "arguments": {
                        "path": str(reminders),
                        "due": "2026-06-11T09:30:00+08:00",
                        "message": "private body",
                    },
                },
            },
        ],
        env,
    )

    assert responses[0]["result"]["protocolVersion"] == "2024-11-05"
    assert "reminder.wake" in responses[0]["result"]["instructions"]
    tools = {tool["name"] for tool in responses[1]["result"]["tools"]}
    assert {"reminder.add", "reminder.notify", "reminder.wake", "reminder.list", "reminder.cancel"} <= tools
    wake_tool = next(tool for tool in responses[1]["result"]["tools"] if tool["name"] == "reminder.wake")
    assert "context-dependent" in wake_tool["description"]
    list_tool = next(tool for tool in responses[1]["result"]["tools"] if tool["name"] == "reminder.list")
    assert "failed" in list_tool["inputSchema"]["properties"]["status"]["enum"]
    result = responses[2]["result"]["structuredContent"]
    assert result["ok"] is True
    assert result["mode"] == "notify"
    assert result["message_chars"] == len("private body")
    assert reminders.exists()


def test_mcp_wake_tool_adds_wake_reminder(tmp_path):
    reminders = tmp_path / "reminders.jsonl"
    env = os.environ.copy()
    env["RTIME_REMINDER_REGISTER"] = str(REGISTER)
    env["RTIME_REMINDER_DEFAULT_TARGET"] = "ou_test_user"

    responses = _call_mcp(
        [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "reminder.wake",
                    "arguments": {
                        "path": str(reminders),
                        "due": "2026-06-11T09:30:00+08:00",
                        "message": "wake title",
                        "prompt": "private wake prompt",
                        "cwd": "/mnt/brain",
                    },
                },
            }
        ],
        env,
    )

    result = responses[0]["result"]["structuredContent"]
    assert result["ok"] is True
    assert result["mode"] == "wake"
    assert result["prompt_chars"] == len("private wake prompt")
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["mode"] == "wake"
    assert stored[0]["prompt"] == "private wake prompt"
