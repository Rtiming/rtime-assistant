# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_mcp_module(monkeypatch, tmp_path):
    monkeypatch.setenv("RTIME_MCP_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    spec = importlib.util.spec_from_file_location("rtime_tools_mcp", ROOT / "scripts" / "rtime-tools-mcp.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def build_fixture_roots(tmp_path: Path):
    brain = tmp_path / "brain"
    vault = tmp_path / "vault"
    pdf = brain / "knowledge" / "courses" / "solid-state" / "lesson1.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF")
    pdf.with_suffix(".md").write_text("md", encoding="utf-8")
    (pdf.parent / "images" / pdf.stem).mkdir(parents=True)
    (brain / "_indexes").mkdir()
    (brain / "_indexes" / "pdf-manifest.jsonl").write_text(
        json.dumps({"brain_path": "knowledge/courses/solid-state/lesson1.pdf"}) + "\n",
        encoding="utf-8",
    )
    (vault / "courses").mkdir(parents=True)
    (vault / "courses" / "lesson1.pdf").write_bytes(b"%PDF")
    zotero = tmp_path / "zotero.json"
    zotero.write_text(
        json.dumps({"items": [{"citekey": "lesson1", "title": "Lesson", "attachments": [str(pdf)]}]}),
        encoding="utf-8",
    )
    return brain, vault, zotero


def test_tools_list_and_call_handlers(monkeypatch, tmp_path):
    mcp = load_mcp_module(monkeypatch, tmp_path)
    brain, vault, zotero = build_fixture_roots(tmp_path)

    names = {tool["name"] for tool in mcp.list_tools()}
    assert names == {"assistant_chat", "vault_resolve", "vault_list", "zotero_citekey", "zotero_search"}

    assert mcp.call_tool("assistant_chat", {"message": "hi", "dry_run": True})["request_body"]["entry"] == "rtime-chat"
    assert mcp.call_tool("vault_resolve", {"query": "lesson1", "brain_root": str(brain)})["match_count"] == 1
    assert mcp.call_tool("vault_list", {"presentation_dir": "courses", "vault_root": str(vault)})["entry_count"] == 1
    assert mcp.call_tool("zotero_citekey", {"citekey": "lesson1", "fixture": str(zotero)})["match_count"] == 1
    assert mcp.call_tool("zotero_search", {"query": "Lesson", "fixture": str(zotero)})["match_count"] == 1

    audit = Path(os.environ["RTIME_MCP_AUDIT_LOG"])
    assert audit.exists()
    assert len(audit.read_text(encoding="utf-8").splitlines()) == 5


def test_stdio_json_lines_smoke(tmp_path):
    brain, vault, zotero = build_fixture_roots(tmp_path)
    audit = tmp_path / "audit.jsonl"
    env = {**os.environ, "RTIME_MCP_AUDIT_LOG": str(audit)}
    requests = [
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {"name": "vault_resolve", "arguments": {"query": "lesson1", "brain_root": str(brain)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "vault_list", "arguments": {"presentation_dir": "courses", "vault_root": str(vault)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "zotero_citekey", "arguments": {"citekey": "lesson1", "fixture": str(zotero)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 5,
            "method": "tools/call",
            "params": {"name": "zotero_search", "arguments": {"query": "Lesson", "fixture": str(zotero)}},
        },
        {
            "jsonrpc": "2.0",
            "id": 6,
            "method": "tools/call",
            "params": {"name": "assistant_chat", "arguments": {"message": "hi", "dry_run": True}},
        },
    ]
    completed = subprocess.run(
        [sys.executable, str(ROOT / "scripts" / "rtime-tools-mcp.py")],
        input="\n".join(json.dumps(req) for req in requests) + "\n",
        text=True,
        capture_output=True,
        env=env,
        check=True,
    )

    responses = [json.loads(line) for line in completed.stdout.splitlines()]
    assert len(responses) == 6
    assert responses[0]["result"]["tools"][0]["name"]
    assert all("error" not in response for response in responses)
    assert audit.exists()
