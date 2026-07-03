# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from rtime_assistant_runtime.mcp_server import PROTOCOL_VERSION, RtimeRuntimeMCP


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: RtimeRuntimeMCP, name: str, arguments: dict) -> dict:
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


def test_runtime_mcp_initialize_and_tools_list():
    server = RtimeRuntimeMCP()

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
    tools = server.handle_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})

    assert initialize is not None
    assert initialize["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {
        "runtime_doctor",
        "runtime_templates_check",
        "runtime_docker_prod_check",
        "runtime_run_log_summary",
        "runtime_run_log_tail",
    } <= names


def test_runtime_mcp_initialize_echoes_client_protocol_version():
    server = RtimeRuntimeMCP()

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


def test_runtime_mcp_doctor_templates_and_run_log_redaction(tmp_path, monkeypatch):
    log_path = tmp_path / "mcp-runs.jsonl"
    run_log = tmp_path / "run-log.jsonl"
    env_file = tmp_path / "docker.env"
    run_log.write_text(
        json.dumps(
            {
                "event": "run_started",
                "timestamp": "2026-06-10T00:00:00Z",
                "run_id": "run-1",
                "entry": "feishu",
                "api_key": "secret",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.write_text(
        "\n".join(
            [
                "ALLOWED_USERS=ou_secret_user",
                "BRAIN_ROOT=/mnt/brain",
                "CALLBACK_BIND=127.0.0.1",
                "CLAUDE_CLI_PATH=/usr/local/bin/claude-kimi",
                "CLAUDE_CONFIG_JSON=/etc/rtime-assistant/.claude.json",
                "CLAUDE_KIMI_KEYFILE=/run/secrets/claude-kimi-key",
                "CLAUDE_STATE_ROOT=/var/lib/rtime-assistant/claude",
                "FEISHU_CONFIG_JSON=/etc/rtime-assistant/feishu.json",
                "INSTALL_CLAUDE_CODE=1",
                "MESSAGE_DEBOUNCE_SECONDS=2.0",
                "MESSAGE_DEBOUNCE_MAX_MESSAGES=20",
                "MESSAGE_DEBOUNCE_MAX_CHARS=12000",
                "RTIME_ASSISTANT_ROOT=/srv/rtime-assistant",
                "RTIME_ASSISTANT_STATE_DIR=/var/lib/rtime-assistant",
                "ANTHROPIC_AUTH_TOKEN=secret-token-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)
    monkeypatch.setenv("RTIME_RUNTIME_MCP_RUN_LOG", str(log_path))
    server = RtimeRuntimeMCP()

    doctor = _call_tool(server, "runtime.doctor", {"repo_root": str(ROOT)})
    templates = _call_tool(server, "runtime.templates_check", {"repo_root": str(ROOT)})
    docker_prod = _call_tool(
        server,
        "runtime.docker_prod_check",
        {"repo_root": str(ROOT), "env_file": str(env_file)},
    )
    summary = _call_tool(server, "runtime.run_log_summary", {"path": str(run_log)})
    tail = _call_tool(server, "runtime.run_log_tail", {"path": str(run_log), "limit": 1})

    assert doctor["ok"] is True
    assert templates["ok"] is True
    assert docker_prod["ok"] is True
    assert docker_prod["env_file"]["missing_keys"] == []
    assert docker_prod["dockerignore"]["checks"]["excludes_env_files"] is True
    assert docker_prod["dockerfile"]["checks"]["copies_rtime_qq_code"] is True
    assert docker_prod["bridge"]["checks"]["simulation_has_access_override_flag"] is True
    assert docker_prod["bridge"]["checks"]["simulation_uses_runner_monkeypatch"] is True
    assert docker_prod["helper"]["checks"]["has_one_shot_smoke"] is True
    assert "secret-token-value" not in json.dumps(docker_prod, ensure_ascii=False)
    assert summary["ok"] is True
    assert summary["record_count"] == 1
    assert tail["records"][0]["api_key"] == "[REDACTED]"

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "runtime.doctor"
    assert records[0]["permission_tier"] == "read_only"
    assert any(record["tool"] == "runtime.docker_prod_check" for record in records)


def test_runtime_mcp_stdio_subprocess_roundtrip(tmp_path):
    run_log = tmp_path / "run-log.jsonl"
    run_log.write_text(
        json.dumps({"event": "run_completed", "timestamp": "2026-06-10T00:00:00Z"})
        + "\n",
        encoding="utf-8",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = (
        f"{ROOT / 'packages' / 'rtime-assistant-runtime' / 'src'}"
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
                "name": "runtime.run_log_summary",
                "arguments": {"path": str(run_log)},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "rtime_assistant_runtime.mcp_server"],
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
    assert responses[-1]["result"]["structuredContent"]["ok"] is True


def test_runtime_plugin_mcp_wrapper_roundtrip(tmp_path):
    run_log = tmp_path / "run-log.jsonl"
    run_log.write_text(json.dumps({"event": "run_started"}) + "\n", encoding="utf-8")
    wrapper = ROOT / "plugins" / "rtime-assistant-runtime" / "scripts" / "rtime-runtime-mcp.sh"
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    payload = json.dumps(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "runtime.run_log_tail",
                "arguments": {"path": str(run_log), "limit": 1},
            },
        }
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
    response = json.loads(completed.stdout.strip())
    assert response["result"]["structuredContent"]["records"][0]["event"] == "run_started"
