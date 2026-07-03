# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Cross-server regression guard for the MCP tool-name wire encoding.

claude.ai / Claude Desktop reject any connector tool whose name fails
``^[a-zA-Z0-9_-]{1,64}$`` (dots are illegal), failing the WHOLE completion.
Every rtime MCP server must therefore advertise wire (underscore) names while
still accepting the canonical dotted name on tools/call (back-compat).
"""
from __future__ import annotations

import importlib
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
for _src in sorted((ROOT / "packages").glob("*/src")):
    if str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

PATTERN = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

MODULES = [
    "brain_citation", "brain_docpack", "brain_library",
    "rtime_agent_control", "rtime_assistant_runtime", "rtime_automation",
    "rtime_context", "rtime_hub_connector", "rtime_profile", "rtime_review",
    "rtime_library_gateway",
]


def _server(mod: str):
    m = importlib.import_module(f"{mod}.mcp_server")
    for _, obj in inspect.getmembers(m, inspect.isclass):
        if obj.__module__ == m.__name__ and hasattr(obj, "handle_message") and hasattr(obj, "tools"):
            return obj()
    raise AssertionError(f"no MCP server class found in {mod}.mcp_server")


def _tool_names(server) -> list[str]:
    resp = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    return [t["name"] for t in resp["result"]["tools"]]


@pytest.mark.parametrize("mod", MODULES)
def test_advertised_names_are_api_valid(mod):
    names = _tool_names(_server(mod))
    assert names, f"{mod}: no tools advertised"
    # wire encoding (dots -> underscores) must stay injective: two canonical ids
    # collapsing to one wire name would silently advertise a duplicate / mis-route.
    assert len(names) == len(set(names)), f"{mod}: duplicate wire tool names: {sorted(names)}"
    for n in names:
        assert PATTERN.match(n), f"{mod}: tool name {n!r} fails ^[a-zA-Z0-9_-]{{1,64}}$"
        assert "." not in n, f"{mod}: dotted name {n!r} leaked onto the wire"


@pytest.mark.parametrize("mod", MODULES)
def test_doctor_dispatches_by_wire_and_dotted_name(mod):
    server = _server(mod)
    names = _tool_names(server)
    doctor = next((n for n in names if n.endswith("_doctor")), None)
    assert doctor, f"{mod}: no *_doctor tool to round-trip"
    dotted = doctor.replace("_", ".", 1)  # prefix_doctor -> prefix.doctor
    for nm in (doctor, dotted):
        resp = server.handle_message(
            {"jsonrpc": "2.0", "id": 2, "method": "tools/call",
             "params": {"name": nm, "arguments": {}}}
        )
        blob = json.dumps(resp, ensure_ascii=False)
        assert "unknown tool" not in blob and "unknown method" not in blob, (
            f"{mod}: tools/call {nm!r} did not dispatch (got: {blob[:160]})"
        )
