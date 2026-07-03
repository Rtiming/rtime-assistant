# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

from brain_docpack.mcp_server import BrainDocpackMCP, PROTOCOL_VERSION
from test_docpack_builder import _write_minimal_pdf
from test_docpack_samples import _make_fixture_tree
from test_docpack_validator import _make_docpack


ROOT = Path(__file__).resolve().parents[1]


def _call_tool(server: BrainDocpackMCP, name: str, arguments: dict) -> dict:
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
    server = BrainDocpackMCP()

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
    assert "tools" in initialize["result"]["capabilities"]

    tools = server.handle_message({"jsonrpc": "2.0", "id": "tools", "method": "tools/list"})
    assert tools is not None
    names = {tool["name"] for tool in tools["result"]["tools"]}
    assert {
        "docpack_doctor",
        "docpack_audit",
        "docpack_course_intake_plan",
        "docpack_select_samples",
        "docpack_validate",
        "docpack_status",
    } <= names


def test_mcp_initialize_echoes_client_protocol_version():
    server = BrainDocpackMCP()

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


def test_mcp_doctor_and_run_log(tmp_path, monkeypatch):
    log_path = tmp_path / "runs.jsonl"
    monkeypatch.setenv("BRAIN_DOCPACK_MCP_RUN_LOG", str(log_path))
    server = BrainDocpackMCP()

    data = _call_tool(server, "docpack.doctor", {"repo_root": str(ROOT)})

    assert data["ok"] is True
    assert data["commands"]["validate_script"] == "ok"
    assert data["permission_tier"] if "permission_tier" in data else True
    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "docpack.doctor"
    assert records[0]["permission_tier"] == "read_only"
    assert records[0]["status"] == "ok"


def test_mcp_validate_and_status(tmp_path):
    server = BrainDocpackMCP()
    docpack = _make_docpack(tmp_path)

    validate = _call_tool(server, "docpack.validate", {"repo_root": str(ROOT), "docpack": str(docpack)})
    status = _call_tool(server, "docpack.status", {"docpack": str(docpack)})

    assert validate["ok"] is True
    assert validate["errors"] == []
    assert status["ok"] is True
    assert status["status"] == "ok"
    assert status["page_count"] == 1


def test_mcp_select_samples(tmp_path):
    server = BrainDocpackMCP()
    _make_fixture_tree(tmp_path)

    samples = _call_tool(
        server,
        "docpack.select_samples",
        {"repo_root": str(ROOT), "root": str(tmp_path), "limit_per_category": 1},
    )

    assert samples["ok"] is True
    assert samples["sample_count"] >= 10


@pytest.mark.skipif(
    os.name == "nt",
    reason="docpack.audit runs audit-knowledge-materials.sh, a POSIX shell entrypoint",
)
def test_mcp_audit(tmp_path):
    server = BrainDocpackMCP()
    _make_fixture_tree(tmp_path)

    audit = _call_tool(server, "docpack.audit", {"repo_root": str(ROOT), "root": str(tmp_path)})

    assert audit["ok"] is True
    assert audit["root"] == str(tmp_path)
    assert audit["file_types"]["pdf"] >= 2


def test_mcp_course_intake_plan_is_read_only_and_reports_confirmation_questions(tmp_path):
    server = BrainDocpackMCP()
    source = tmp_path / "source"
    source.mkdir()
    _write_minimal_pdf(source / "第一章-课程介绍.pdf", "Course intro text layer.")
    brain = tmp_path / "brain"

    data = _call_tool(
        server,
        "docpack.course_intake_plan",
        {
            "repo_root": str(ROOT),
            "source_root": str(source),
            "brain_root": str(brain),
            "course_id": "new-course",
            "course_title": "新课程",
            "include_all": True,
        },
    )

    assert data["ok"] is True
    assert data["permission_tier"] == "read_only"
    assert data["writes"] == []
    assert data["summary"]["files"] == 1
    assert data["requires_user_confirmation"] is True
    assert any(question["id"] == "new-course-root" for question in data["confirmation_questions"])
    assert not (brain / "knowledge" / "courses" / "new-course").exists()


def test_mcp_tool_error_for_missing_docpack_argument():
    server = BrainDocpackMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "docpack.validate", "arguments": {}},
        }
    )

    assert response is not None
    assert response["result"]["isError"] is True
    assert response["result"]["structuredContent"]["ok"] is False
    assert "missing required argument" in response["result"]["structuredContent"]["error"]


def test_mcp_stdio_subprocess_roundtrip(tmp_path):
    docpack = _make_docpack(tmp_path)
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'brain-docpack' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
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
                "name": "docpack.status",
                "arguments": {"docpack": str(docpack)},
            },
        },
    ]
    payload = "\n".join(json.dumps(message) for message in messages) + "\n"

    completed = subprocess.run(
        [sys.executable, "-m", "brain_docpack.mcp_server"],
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


@pytest.mark.skipif(
    os.name == "nt",
    reason="brain-docpack-mcp.sh is a POSIX shell entrypoint not executable on native Windows",
)
def test_plugin_mcp_wrapper_roundtrip(tmp_path):
    source = tmp_path / "wrapper.pdf"
    _write_minimal_pdf(source, "Wrapper smoke text.")
    docpack = tmp_path / "wrapper.docpack"
    subprocess.run(
        [
            sys.executable,
            str(ROOT / "scripts" / "build-docpack.py"),
            str(source),
            "--out",
            str(docpack),
            "--docpack-id",
            "wrapper-smoke",
        ],
        cwd=ROOT,
        check=True,
        timeout=30,
    )

    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    wrapper = ROOT / "plugins" / "brain-docpack" / "scripts" / "brain-docpack-mcp.sh"
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
                        "name": "docpack.validate",
                        "arguments": {"docpack": str(docpack)},
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
    assert responses[-1]["result"]["structuredContent"]["ok"] is True


@pytest.mark.parametrize("method", ["unknown/method", "resources/list"])
def test_mcp_unknown_request_returns_jsonrpc_error(method):
    server = BrainDocpackMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 9, "method": method})

    assert response is not None
    assert response["error"]["code"] == -32601
