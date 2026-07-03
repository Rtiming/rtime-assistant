# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "brain-citation" / "src"


def _load_mcp():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("brain_citation.mcp_server")


def _make_brain_fixture(root: Path) -> Path:
    brain = root / "brain"
    (brain / ".obsidian").mkdir(parents=True)
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "note.md").write_text(
        "[[Concept]] @smith2024 @missing2026 zotero://select/items/ABC\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "refs.bib").write_text(
        "@article{smith2024,title={Example}}\n",
        encoding="utf-8",
    )
    return brain


def test_tools_list_exposes_citation_tools():
    mcp = _load_mcp()
    server = mcp.BrainCitationMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    assert names == {"citation_doctor", "citation_scan", "citation_panel"}


def test_initialize_echoes_client_protocol_version():
    mcp = _load_mcp()
    server = mcp.BrainCitationMCP()

    response = server.handle_message(
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

    assert response is not None
    assert response["result"]["protocolVersion"] == "2024-11-05"


def test_call_panel_returns_structured_content_and_logs_metadata(tmp_path, monkeypatch):
    mcp = _load_mcp()
    brain = _make_brain_fixture(tmp_path)
    log_path = tmp_path / "citation-mcp.jsonl"
    monkeypatch.setenv("BRAIN_CITATION_MCP_RUN_LOG", str(log_path))
    server = mcp.BrainCitationMCP()

    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "citation.panel",
                "arguments": {"root": str(brain), "sample_limit": 3},
            },
        }
    )

    result = response["result"]
    panel = result["structuredContent"]
    assert result["isError"] is True
    assert panel["ok"] is False
    assert panel["panels"]["crosswalk"]["missing_bib_keys"] == ["missing2026"]

    records = [json.loads(line) for line in log_path.read_text(encoding="utf-8").splitlines()]
    assert records[0]["tool"] == "citation.panel"
    assert records[0]["permission_tier"] == "read_only"
    assert records[0]["input_paths"] == [str(brain)]


def test_wrapper_lists_tools():
    wrapper = ROOT / "plugins" / "brain-citation" / "scripts" / "brain-citation-mcp.sh"
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
    assert "citation_scan" in names
