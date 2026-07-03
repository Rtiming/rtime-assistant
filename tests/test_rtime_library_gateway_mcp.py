# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"

# The Anthropic API rejects any tool name that does not match this pattern
# (dots are illegal). Advertised wire names must satisfy it.
WIRE_NAME_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")

# Canonical dotted method ids (the internal source of truth).
EXPECTED_TOOLS = {
    "lib.doctor",
    "lib.status",
    "lib.search",
    "lib.courses",
    "lib.get",
    "lib.read",
    "lib.tree",
    "lib.stat",
    "lib.recent",
    "lib.freshness",
    "lib.policy",
    "lib.audit",
    "lib.preview",
    "lib.list",
    "lib.meta",
    "lib.docpack",
    "lib.citation",
    "lib.hub",
    "lib.context",
    "lib.profile",
    "lib.review",
    "lib.automation",
    "lib.runtime",
    "lib.settings.context_source_list",
    "lib.settings.context_source_check",
    "lib.settings.context_source_add",
    "lib.settings.context_source_deactivate",
    "lib.settings.memory_candidate_add",
    "lib.settings.reminder_register",
    "lib.settings.reminder_list",
    "lib.settings.reminder_cancel",
    "lib.annotate",
    "lib.edit",
    "lib.revert",
    "lib.revisions",
    "lib.move",
    "lib.retire",
    "lib.restore",
    "lib.contribute",
    "lib.finalize",
    "lib.course-intake",
    "lib.jobs.submit",
    "lib.jobs.get",
    "lib.jobs.list",
}


def _load_mcp():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.mcp_server")


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
    return response["result"]


def test_initialize_echoes_client_protocol_version():
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

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
    assert response["result"]["serverInfo"]["name"] == "rtime-library-gateway"


def test_tools_list_returns_curated_lib_namespace():
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    names = {tool["name"] for tool in response["result"]["tools"]}
    # tools/list advertises the API-valid wire form (dots -> underscores) of the
    # canonical ids; the dotted form is never sent on the wire.
    assert names == {n.replace(".", "_") for n in EXPECTED_TOOLS}


def test_tools_list_names_satisfy_anthropic_pattern():
    # Regression guard for the connector bug: every advertised name MUST match
    # ^[a-zA-Z0-9_-]{1,64}$ or claude.ai / Claude Desktop reject the whole request.
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    response = server.handle_message({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})

    for tool in response["result"]["tools"]:
        name = tool["name"]
        assert WIRE_NAME_RE.match(name), f"invalid tool name: {name!r}"
        assert "." not in name, f"dotted tool name leaked to the wire: {name!r}"


def test_tools_call_accepts_wire_and_dotted_names():
    # The wire name (lib_doctor) must dispatch to the canonical method, and the
    # dotted name (lib.doctor) must keep working for backward compatibility.
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    wire = _call_tool(server, "lib_doctor", {})
    assert wire["isError"] is False
    assert wire["structuredContent"]["method"] == "lib.doctor"

    dotted = _call_tool(server, "lib.doctor", {})
    assert dotted["isError"] is False
    assert dotted["structuredContent"]["method"] == "lib.doctor"

    # a settings (write-tier) tool also round-trips by wire name
    wire_settings = _call_tool(server, "lib_settings_reminder_list", {})
    assert wire_settings["structuredContent"].get("method") == "lib.settings.reminder_list"

    # ...and the dotted settings name keeps working (back-compat)
    dotted_settings = _call_tool(server, "lib.settings.reminder_list", {})
    assert dotted_settings["structuredContent"].get("method") == "lib.settings.reminder_list"

    # lib.course-intake is the only method whose wire name keeps a hyphen
    # (lib_course-intake): exercise that the reverse map handles dots-only
    # replacement and round-trips it back to the canonical id.
    assert mcp._canonical_method("lib_course-intake") == "lib.course-intake"
    assert mcp._wire_name("lib.course-intake") == "lib_course-intake"


def test_preview_and_audit_accept_wire_method_names():
    # lib.preview's target `method` and lib.audit's `filter_method` accept the
    # wire form too, so an agent that only knows the underscore names still works.
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    prev = server.invoke("lib.preview", {"method": "lib_read", "arguments": {"path": "knowledge/x.md"}})
    assert prev["tier"] == "read"
    assert prev["target_method"] == "lib.read"
    assert "unknown method" not in (prev.get("reason") or "")

    prev_write = server.invoke("lib.preview", {"method": "lib_finalize", "arguments": {}})
    assert prev_write["tier"] == "write"


def test_status_ok_when_surfaces_responsive_even_if_degraded(monkeypatch):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    def fake_invoke(method, args, *, client_id="default"):
        # hub responds but reports "not configured" (ok:false); everything else ok.
        if method == "lib.hub":
            return {"ok": False, "errors": ["not configured"]}
        return {"ok": True}

    monkeypatch.setattr(server, "invoke", fake_invoke)
    st = server._status("default")
    # responsive everywhere => healthy; an ok:false surface is degraded, not a failure.
    assert st["ok"] is True
    assert st["degraded"] == ["hub"]
    assert st["broken"] == []
    assert st["surfaces"]["hub"]["responsive"] is True


def test_status_broken_only_when_a_surface_raises(monkeypatch):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    def fake_invoke(method, args, *, client_id="default"):
        if method == "lib.docpack":
            raise RuntimeError("crash")
        return {"ok": True}

    monkeypatch.setattr(server, "invoke", fake_invoke)
    st = server._status("default")
    assert st["ok"] is False
    assert "docpack" in st["broken"]
    assert st["surfaces"]["docpack"]["responsive"] is False


def test_status_quick_probes_only_library_surface(monkeypatch):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    calls = []

    def fake_invoke(method, args, *, client_id="default"):
        calls.append(method)
        return {"ok": True}

    monkeypatch.setattr(server, "invoke", fake_invoke)
    st = server._status("default", quick=True)
    # quick mode probes only the index/library surface (lib.list), not all 9.
    assert calls == ["lib.list"]
    assert st["quick"] is True
    assert set(st["surfaces"]) == {"library"}
    assert st["ok"] is True


def test_status_quick_routed_through_invoke(monkeypatch):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    seen = {}

    def fake_status(client_id, quick=False):
        seen["quick"] = quick
        return {"ok": True, "quick": quick}

    monkeypatch.setattr(server, "_status", fake_status)
    server.invoke("lib.status", {"quick": True})
    assert seen["quick"] is True


def test_prewarm_disabled_env_skips(monkeypatch):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", "0")
    mcp._maybe_prewarm(server)
    assert server._search_lock is None


def test_prewarm_enabled_warms_in_background(monkeypatch):
    import threading as _t

    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    called = _t.Event()
    seen = {}

    def fake_search(args):
        seen["args"] = args
        called.set()
        return {"ok": True}, 0

    monkeypatch.setattr(server, "_search_inprocess", fake_search)
    monkeypatch.delenv("RTIME_LIBRARY_GATEWAY_PREWARM", raising=False)
    mcp._maybe_prewarm(server)
    assert server._search_lock is not None  # lock created for warm/query serialization
    assert called.wait(5), "prewarm thread never invoked _search_inprocess"
    assert "query" in seen["args"]


def test_search_in_process_real_index(tmp_path, monkeypatch):
    # Build a tiny BM25 index and search it through the server's in-process fast path.
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "stellarator.md").write_text(
        "# 仿星器\n仿星器 HTS 线圈 与等离子体物理研究。\n", encoding="utf-8"
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]

    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    data = server.invoke("lib.search", {"query": "仿星器", "index": str(idx)})
    assert data["ok"] is True
    assert data["result_count"] >= 1
    assert data["served_inprocess"] is True
    # warm path: the indexer module is cached for the session
    assert server._indexer_mod is not None
    data2 = server.invoke("lib.search", {"query": "线圈", "index": str(idx)})
    assert data2["ok"] is True


def test_courses_query_through_gateway(tmp_path, monkeypatch):
    # lib.courses dispatches through the subprocess CLI to the structured courses table.
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "academics").mkdir(parents=True)
    (brain / "academics" / "prog.md").write_text(
        "---\ntype: ustc-program\ndept: 203物理学院\ngrade: 2023\n---\n\n"
        "# 应用物理学\n\n"
        "| 模块 | 编号 | 课程 | 学分 | 学时 | 必修 | 建议学期 | 开课院系 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 数学通修 | MATH1009 | 线性代数 | 4.0 | 80 | 必 | 1春 | 数学科学学院 |\n",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]

    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    data = server.invoke("lib.courses", {"code": "MATH1009", "index": str(idx)})
    assert data["ok"] is True
    assert data["count"] == 1
    assert data["courses"][0]["program_name"].startswith("应用物理学")
    assert data["courses"][0]["required"] is True


def test_search_in_process_redacts_results(tmp_path, monkeypatch):
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    mcp = _load_mcp()
    import rtime_library_gateway.gate as gate
    # The deployed single-owner policy turns redaction OFF; pin it ON here to prove the
    # redaction mechanism still masks secrets when a deployment enables it.
    policy = gate.load_policy()
    policy["redact_sensitive"] = True
    monkeypatch.setattr(gate, "load_policy", lambda: policy)
    server = mcp.RtimeLibraryGatewayMCP()

    def fake_search(arguments):
        return (
            {
                "ok": True,
                "result_count": 1,
                "results": [{"path": "a.md", "snippet": "token api_key=SECRET123 more"}],
            },
            0,
        )

    monkeypatch.setattr(server, "_search_inprocess", fake_search)
    data = server.invoke("lib.search", {"query": "x"})
    assert data["ok"] is True
    # the secret line is redacted on the way out (mirrors the dispatch redaction path)
    assert "SECRET123" not in json.dumps(data, ensure_ascii=False)
    assert data.get("redacted_line_count", 0) >= 1


def test_call_doctor_returns_structured_content_and_writes_one_audit_line(tmp_path, monkeypatch):
    mcp = _load_mcp()
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log_path))
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    server = mcp.RtimeLibraryGatewayMCP()

    result = _call_tool(server, "lib.doctor", {})
    data = result["structuredContent"]

    assert data["ok"] is True
    assert data["server"] == "rtime-library-gateway"
    assert data["dispatch"]["tables_disjoint"] is True

    lines = log_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["method"] == "lib.doctor"
    assert record["tier"] == "read"
    assert record["decision"] == "allow"
    assert "claim" not in record
    assert "message" not in record


def test_call_read_method_routes_through_dispatch_and_redacts(tmp_path, monkeypatch):
    mcp = _load_mcp()
    import rtime_library_gateway.dispatch as dispatch

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log_path))
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    canned = {"ok": True, "items": [{"id": "p1"}], "note": "contact ou_abcdef12345678"}
    canned_text = json.dumps(canned, indent=2)

    def fake_run_cli(target, *, timeout=60):
        return canned, 0, canned_text

    monkeypatch.setattr(dispatch, "run_cli", fake_run_cli)
    server = mcp.RtimeLibraryGatewayMCP()

    result = _call_tool(server, "lib.hub", {"op": "contacts"})
    data = result["structuredContent"]
    assert data["ok"] is True
    assert data["note"] == "[redacted sensitive line]"
    # contacts forces redaction; the rendered text must not leak the open_id
    rendered = result["content"][0]["text"]
    assert "ou_abcdef12345678" not in rendered
    assert "ou_abcdef12345678" not in json.dumps(data, ensure_ascii=False)
    assert data["redacted_line_count"] >= 1

    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["method"] == "lib.hub"
    assert record["redacted_line_count"] >= 1


def test_call_denied_method_records_deny(tmp_path, monkeypatch):
    mcp = _load_mcp()
    import rtime_library_gateway.gate as gate

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log_path))

    policy = gate.load_policy()
    policy["methods"]["lib.search"] = {"tier": "read", "enabled": False}
    monkeypatch.setattr(gate, "load_policy", lambda: policy)

    server = mcp.RtimeLibraryGatewayMCP()
    response = server.handle_message(
        {
            "jsonrpc": "2.0",
            "id": 9,
            "method": "tools/call",
            "params": {"name": "lib.search", "arguments": {"index": "i", "query": "q"}},
        }
    )
    result = response["result"]
    assert result["isError"] is True
    record = json.loads(log_path.read_text(encoding="utf-8").splitlines()[0])
    assert record["method"] == "lib.search"
    assert record["decision"] == "deny"


def test_policy_reports_effective_gate(tmp_path, monkeypatch):
    mcp = _load_mcp()
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = mcp.RtimeLibraryGatewayMCP()

    data = _call_tool(server, "lib.policy", {})["structuredContent"]
    assert data["ok"] is True
    assert data["method_count"] == len(EXPECTED_TOOLS)
    assert set(data["methods"]) == EXPECTED_TOOLS
    # tiers are reported and a known write tool is write, a read tool is read
    assert data["methods"]["lib.finalize"]["tier"] == "write"
    assert data["methods"]["lib.read"]["tier"] == "read"
    assert data["methods"]["lib.read"]["allowed_for_you"] is True
    # Owner opened the single-owner library fully: personal-data is no longer gated
    # and sensitive-line redaction is off. The gate MECHANISM stays covered by the
    # gate unit tests (which still exclude personal-data explicitly).
    assert data["excluded_top_dirs"] == []
    assert data["redact_sensitive"] is False


def test_policy_reports_client_denied(tmp_path, monkeypatch):
    mcp = _load_mcp()
    import rtime_library_gateway.gate as gate

    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    policy = gate.load_policy()
    policy["clients"] = {"locked": {"deny": ["lib.finalize"]}}
    monkeypatch.setattr(gate, "load_policy", lambda: policy)

    server = mcp.RtimeLibraryGatewayMCP()
    data = server.invoke("lib.policy", {}, client_id="locked")
    assert data["methods"]["lib.finalize"]["allowed_for_you"] is False
    assert data["methods"]["lib.search"]["allowed_for_you"] is True


def test_preview_allows_valid_write_and_denies_personal_data(tmp_path, monkeypatch):
    mcp = _load_mcp()
    import rtime_library_gateway.gate as gate
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    # Deployed single-owner policy opens personal-data; pin it gated here to prove
    # preview still denies a personal-data path when a deployment excludes it.
    policy = gate.load_policy()
    policy["excluded_top_dirs"] = ["personal-data"]
    monkeypatch.setattr(gate, "load_policy", lambda: policy)
    server = mcp.RtimeLibraryGatewayMCP()

    ok = server.invoke(
        "lib.preview",
        {"method": "lib.finalize", "arguments": {"op": "plan", "inbox": "_inbox/agent/x.md", "dest": "knowledge/notes/x"}},
    )
    assert ok["ok"] is True
    assert ok["target_method"] == "lib.finalize"
    assert ok["tier"] == "write"
    assert ok["decision"] == "allow"
    assert ok["gate_allows"] is True
    # write target: gate_allows must not be read as "will succeed" — note flags the
    # owner-approval step the gate does not check
    assert ok["note"] and "approval" in ok["note"]
    # the shape never echoes argument VALUES, only the executable + flag names
    shape = ok["target"]
    assert shape["executable"] is not None
    assert all(f.startswith("-") for f in shape["flags"])
    blob = json.dumps(ok, ensure_ascii=False)
    assert "_inbox/agent/x.md" not in blob

    # a personal-data path is denied at preview time (gate dry-run), nothing runs
    denied = server.invoke("lib.preview", {"method": "lib.read", "arguments": {"path": "personal-data/x.md"}})
    assert denied["decision"] == "deny"
    assert "personal-data" in denied["reason"]
    assert denied["gate_allows"] is False

    # an unknown method is reported, not raised — and uses the SAME field schema
    # (gate_allows, not the old would_execute) as every other preview path
    unknown = server.invoke("lib.preview", {"method": "lib.nope", "arguments": {}})
    assert unknown["decision"] == "deny"
    assert "unknown method" in unknown["reason"]
    assert unknown["gate_allows"] is False
    assert "would_execute" not in unknown
    assert unknown["target"] is None

    # an allowed in-process / lib.search target builds NO command shape (target=None)
    # yet still reports gate_allows=True — exercises the build_target skip branch
    for inproc in ("lib.doctor", "lib.policy", "lib.search"):
        prev = server.invoke("lib.preview", {"method": inproc, "arguments": {}})
        assert prev["decision"] == "allow", inproc
        assert prev["tier"] == "read", inproc
        assert prev["gate_allows"] is True, inproc
        assert prev["target"] is None, inproc
        assert prev["note"] is None, inproc


def test_audit_summarizes_metadata_log(tmp_path, monkeypatch):
    mcp = _load_mcp()
    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log_path))
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    server = mcp.RtimeLibraryGatewayMCP()

    # generate a couple of audited calls, then read the summary back
    server.invoke("lib.doctor", {})
    server.invoke("lib.policy", {})
    data = server.invoke("lib.audit", {"limit": 10})
    assert data["ok"] is True
    assert data["total_matched"] >= 2
    assert data["by_method"].get("lib.doctor", 0) >= 1
    assert "allow" in data["by_decision"]
    assert all({"ts", "method", "decision"} <= set(row) for row in data["recent"])

    # filter by method narrows the rows
    only = server.invoke("lib.audit", {"filter_method": "lib.doctor"})
    assert set(only["by_method"]) == {"lib.doctor"}

    # the wire-form filter (lib_doctor) narrows identically — the audit log stores
    # canonical dotted ids, so filter_method must be canonicalized before matching
    wire_only = server.invoke("lib.audit", {"filter_method": "lib_doctor"})
    assert set(wire_only["by_method"]) == {"lib.doctor"}


def test_search_in_process_filters_through_invoke(tmp_path, monkeypatch):
    # The filters (suffix/path_prefix/title_only) must work through the IN-PROCESS
    # fast path agents actually hit (_search_inprocess), not just the CLI dispatch.
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge" / "papers").mkdir(parents=True)
    (brain / "other").mkdir(parents=True)
    (brain / "knowledge" / "papers" / "coil.md").write_text("# Coil Paper\nstellarator coil winding", encoding="utf-8")
    (brain / "knowledge" / "papers" / "refs.txt").write_text("coil references list", encoding="utf-8")
    (brain / "other" / "sketch.md").write_text("# Sketch\ncoil sketch diagram", encoding="utf-8")
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]

    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    md = server.invoke("lib.search", {"query": "coil", "index": str(idx), "suffix": "md"})
    assert md["served_inprocess"] is True
    assert md["result_count"] >= 1
    assert all(r["suffix"] == "md" for r in md["results"])
    assert md["filters"]["suffix"] == "md"

    pref = server.invoke("lib.search", {"query": "coil", "index": str(idx), "path_prefix": "knowledge/papers"})
    assert pref["result_count"] >= 1
    assert all(r["path"].startswith("knowledge/papers") for r in pref["results"])

    title = server.invoke("lib.search", {"query": "Coil", "index": str(idx), "title_only": True})
    assert "title_index" in title["fts_query"]
    assert title["result_count"] >= 1  # guard: an empty result would make the all() vacuous
    assert all("coil" in r["title"].lower() for r in title["results"])
    # title_only must match on the title-bearing doc, not body-only hits
    assert any(r["path"].endswith("coil.md") for r in title["results"])


def test_search_in_process_metadata_filters_and_mode(tmp_path, monkeypatch):
    # Regression: the in-process fast path must thread the schema-3 metadata filters
    # (doc_type/dept/date) AND `mode` through to query_index, not just suffix/path.
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "academics").mkdir(parents=True)
    (brain / "academics" / "prog.md").write_text(
        "---\ntype: ustc-program\ndept: 203物理学院\npublish_date: 2026-06-20\n---\n"
        "# 应用物理学培养方案\n量子力学 电动力学 选课 学分",
        encoding="utf-8",
    )
    (brain / "academics" / "notice.md").write_text(
        "---\ntype: ustc-notice\ndept: 教务处\npublish_date: 2021-05-07\n---\n"
        "# 选课通知\n选课 时间 安排",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True, embed=False)["ok"]

    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    # doc_type filter narrows to the program note (was silently dropped before the fix).
    prog = server.invoke("lib.search", {"query": "选课", "index": str(idx), "doc_type": "ustc-program"})
    assert prog["served_inprocess"] is True
    assert prog["filters"]["doc_type"] == "ustc-program"
    assert prog["result_count"] == 1
    assert prog["results"][0]["path"].endswith("prog.md")

    # dept filter + date range likewise reach query_index.
    dated = server.invoke(
        "lib.search",
        {"query": "选课", "index": str(idx), "dept": "教务处", "date_from": "2020-01-01", "date_to": "2022-01-01"},
    )
    assert dated["result_count"] == 1
    assert dated["results"][0]["path"].endswith("notice.md")

    # mode is threaded: explicit bm25 is honored; an invalid mode is rejected.
    bm = server.invoke("lib.search", {"query": "选课", "index": str(idx), "mode": "bm25"})
    assert bm["filters"]["mode"] == "bm25"
    bad = server.invoke("lib.search", {"query": "选课", "index": str(idx), "mode": "semantic"})
    assert bad["ok"] is False


def test_hide_excluded_in_results_switch(tmp_path, monkeypatch):
    # Reserved content filter: OFF by default the full library (incl. personal-data)
    # stays visible in lib.recent results; flipping hide_excluded_in_results filters
    # personal-data rows out. This proves the switch exists and is off by default.
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    import rtime_library_gateway.gate as gate
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "personal-data" / "health").mkdir(parents=True)
    (brain / "knowledge" / "ok.md").write_text("# 公开\n等离子体\n", encoding="utf-8")
    (brain / "personal-data" / "health" / "diag.md").write_text("# 体检报告\n隐私\n", encoding="utf-8")
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]

    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))

    base = gate.load_policy()
    # Deployed single-owner policy opens personal-data (excluded_top_dirs: []); pin it
    # excluded so the hide-from-results mechanism has something to filter.
    base["excluded_top_dirs"] = ["personal-data"]

    # OFF (default): personal-data rows are present (current single-owner behavior)
    off = dict(base)
    off["hide_excluded_in_results"] = False
    monkeypatch.setattr(gate, "load_policy", lambda: off)
    server = _load_mcp().RtimeLibraryGatewayMCP()
    d_off = server.invoke("lib.recent", {"limit": 10})
    assert any("personal-data" in doc["path"] for doc in d_off["documents"])
    assert "excluded_hidden_count" not in d_off

    # ON: personal-data rows are filtered out of the results
    on = dict(base)
    on["hide_excluded_in_results"] = True
    monkeypatch.setattr(gate, "load_policy", lambda: on)
    d_on = server.invoke("lib.recent", {"limit": 10})
    assert all("personal-data" not in doc["path"] for doc in d_on["documents"])
    assert d_on["excluded_hidden_count"] >= 1
    assert d_on["count"] == len(d_on["documents"])


def test_audit_since_and_decision_filters(tmp_path, monkeypatch):
    mcp = _load_mcp()
    import rtime_library_gateway.gate as gate

    log_path = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log_path))
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    # disable lib.search so invoking it records a deny line
    policy = gate.load_policy()
    policy["methods"]["lib.search"] = {"tier": "read", "enabled": False}
    monkeypatch.setattr(gate, "load_policy", lambda: policy)

    server = mcp.RtimeLibraryGatewayMCP()
    server.invoke("lib.doctor", {})  # allow
    try:
        server.invoke("lib.search", {"query": "x"})  # deny -> raises
    except Exception:
        pass

    denies = server.invoke("lib.audit", {"decision": "deny"})
    assert set(denies["by_decision"]) == {"deny"}
    assert denies["total_matched"] >= 1
    allows = server.invoke("lib.audit", {"decision": "allow"})
    assert "deny" not in allows["by_decision"]

    # since is a lexicographic ISO compare: a far-future bound matches nothing, a
    # far-past bound matches everything.
    future = server.invoke("lib.audit", {"since": "2999-01-01T00:00:00+00:00"})
    assert future["total_matched"] == 0
    past = server.invoke("lib.audit", {"since": "2000-01-01T00:00:00+00:00"})
    assert past["total_matched"] >= 2


def test_wrapper_lists_tools():
    wrapper = ROOT / "plugins" / "rtime-library-gateway" / "scripts" / "rtime-library-gateway-mcp.sh"
    if not wrapper.exists():
        return
    if os.name == "nt":
        import pytest

        # The POSIX .sh wrapper is not natively executable on Windows (and assumes
        # a `python3` on PATH). Claude registration on Windows bypasses it via
        # `python -m rtime_library_gateway.mcp_server`, which is covered by the
        # in-process tests above. Skip rather than pretend the .sh runs here.
        pytest.skip("POSIX .sh wrapper is not natively runnable on Windows; Claude uses `python -m` directly")
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
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
    assert "lib_doctor" in names
    assert "lib_settings_reminder_register" in names
    assert all(WIRE_NAME_RE.match(n) for n in names)


def test_serve_stdio_idle_timeout_exits_half_open_client():
    """A silently-dropped client (ssh connection killed, never closed) sends no
    EOF, so a plain ``for line in sys.stdin`` loop blocks forever and leaks the
    process — observed as dozens of stale ``sshd@notty`` mcp_server processes.
    The stdin idle guard must make serve_stdio exit on its own. Also exercises
    the newline-buffer split by framing two messages in a single write.
    """
    if os.name == "nt":
        import pytest

        pytest.skip("select()-on-pipe idle guard is POSIX-only; Windows uses blocking iteration")
    env = os.environ.copy()
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    env["PYTHONPATH"] = f"{SRC}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT"] = "0.5"
    proc = subprocess.Popen(
        [sys.executable, "-m", "rtime_library_gateway.mcp_server"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
    )
    payload = (
        '{"jsonrpc":"2.0","id":1,"method":"initialize","params":'
        '{"protocolVersion":"2025-06-18","capabilities":{},'
        '"clientInfo":{"name":"t","version":"1"}}}\n'
        '{"jsonrpc":"2.0","id":2,"method":"ping"}\n'
    )
    proc.stdin.write(payload.encode())
    proc.stdin.flush()
    # Deliberately keep stdin OPEN and send nothing more — a half-open client.
    try:
        returncode = proc.wait(timeout=15)
    except subprocess.TimeoutExpired:
        proc.kill()
        raise AssertionError("serve_stdio ignored the idle timeout and hung (process-leak regression)")
    out = proc.stdout.read().decode("utf-8", "replace")
    answered = {json.loads(line)["id"] for line in out.splitlines() if line.strip()}
    assert {1, 2} <= answered  # both framed messages handled (buffer split works)
    assert returncode == 0  # clean self-exit via the idle guard, not a crash


def test_jobs_submit_worker_get_end_to_end(tmp_path, monkeypatch):
    """The P7 contract through the gateway: lib.jobs.submit enqueues (write tier,
    via deploy/bin/rtime-jobs-submit), a separate worker executes it, and
    lib.jobs.get returns the result — the chat entry never runs the work itself.
    Also asserts the audit stays metadata-only (job params never logged)."""
    jobs_db = tmp_path / "jobs.sqlite"
    audit = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("RTIME_JOBS_DB", str(jobs_db))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(audit))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    # submit (write tier) -> a pending job; params travel on stdin, never argv
    sub = server.invoke("lib.jobs.submit", {"type": "echo", "params": {"k": "SECRET_PARAM"}})
    assert sub["ok"] is True
    assert sub["status"] == "pending"
    assert sub["tier"] == "write"
    job_id = sub["job_id"]

    # before the worker runs, status is pending and list sees it
    pending = server.invoke("lib.jobs.get", {"id": job_id})
    assert pending["status"] == "pending"
    listed = server.invoke("lib.jobs.list", {"status": "pending"})
    assert listed["count"] == 1

    # a separate worker drains the queue (the gateway never runs the work)
    jobs_src = str(ROOT / "packages" / "rtime-jobs" / "src")
    if jobs_src not in sys.path:
        sys.path.insert(0, jobs_src)
    from rtime_jobs.runner import run_pending
    from rtime_jobs.store import JobStore

    run_pending(JobStore(jobs_db))

    done = server.invoke("lib.jobs.get", {"id": job_id})
    assert done["status"] == "succeeded"
    assert done["result"] == {"ok": True, "echo": {"k": "SECRET_PARAM"}}

    # the audit log is metadata-only: the job params never appear in it
    audit_text = audit.read_text(encoding="utf-8")
    assert "SECRET_PARAM" not in audit_text
    methods = {json.loads(line)["method"] for line in audit_text.splitlines() if line.strip()}
    assert {"lib.jobs.submit", "lib.jobs.get", "lib.jobs.list"} <= methods


def test_jobs_submit_unknown_type_is_rejected(tmp_path, monkeypatch):
    """submit validates the type against the registered handlers (fail fast) so a
    bogus type never sits in the queue waiting for a handler that will never run."""
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("RTIME_JOBS_DB", str(tmp_path / "jobs.sqlite"))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    res = server.invoke("lib.jobs.submit", {"type": "no-such-type"})
    assert res["ok"] is False
    assert "unknown job type" in res["error"]


# ---------------------------------------------------------------------------
# prewarm env-bool parse + default (drift fix): the live default is ON ('1'),
# and 0/false/no/off all turn it OFF (the old bare ``== '0'`` check let
# false/no/off silently stay ON — a foot-gun on the memory-tight ARM host).
# ---------------------------------------------------------------------------


def test_env_bool_truthy_and_falsy(monkeypatch):
    mcp = _load_mcp()
    for val in ("1", "true", "TRUE", "yes", "on", "On"):
        monkeypatch.setenv("X_TEST_BOOL", val)
        assert mcp._env_bool("X_TEST_BOOL", "0") is True, val
    for val in ("0", "false", "False", "no", "off", "", "  ", "nope"):
        monkeypatch.setenv("X_TEST_BOOL", val)
        assert mcp._env_bool("X_TEST_BOOL", "1") is False, val
    # unset -> the default string is parsed the same way
    monkeypatch.delenv("X_TEST_BOOL", raising=False)
    assert mcp._env_bool("X_TEST_BOOL", "1") is True
    assert mcp._env_bool("X_TEST_BOOL", "0") is False


def test_prewarm_default_on_and_falsy_values_disable(monkeypatch):
    """_maybe_prewarm defaults ON (unset) and is disabled by any falsy value —
    crucially 'false'/'no'/'off', which the old ``== '0'`` check missed."""
    mcp = _load_mcp()

    started = []

    class _FakeThread:
        def __init__(self, *a, **k):
            started.append(True)

        def start(self):
            pass

    monkeypatch.setattr(mcp.threading, "Thread", _FakeThread)

    def _did_prewarm() -> bool:
        started.clear()
        server = mcp.RtimeLibraryGatewayMCP()
        mcp._maybe_prewarm(server)
        return bool(started)

    monkeypatch.delenv("RTIME_LIBRARY_GATEWAY_PREWARM", raising=False)
    assert _did_prewarm() is True  # default ON

    for off in ("0", "false", "False", "no", "off"):
        monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", off)
        assert _did_prewarm() is False, off

    for on in ("1", "true", "yes", "on"):
        monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", on)
        assert _did_prewarm() is True, on


def test_prewarm_schema_default_matches_runtime_default():
    """No drift: the LibraryGatewayConfig ``prewarm`` default == the runtime default
    (ON). The runtime default is the string '1' passed to _env_bool by _maybe_prewarm;
    parse it the same way and assert the schema field agrees."""
    mcp = _load_mcp()
    admin_src = ROOT / "packages" / "rtime-admin-core" / "src"
    config_src = ROOT / "packages" / "rtime-config" / "src"
    for p in (str(admin_src), str(config_src)):
        if p not in sys.path:
            sys.path.insert(0, p)
    from rtime_admin_core.schemas import LibraryGatewayConfig

    runtime_default = mcp._env_bool("RTIME_LIBRARY_GATEWAY_PREWARM_UNSET_XYZ", "1")
    schema_default = LibraryGatewayConfig.model_fields["prewarm"].default
    assert runtime_default is True
    assert schema_default == runtime_default, "prewarm schema default drifted from runtime"


def test_annotate_plan_apply_and_index_sync(tmp_path, monkeypatch):
    """H M1 端到端:in-process plan→apply→索引元数据列同步(单行 UPDATE,不重建)。"""
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    doc = brain / "knowledge" / "notice.md"
    doc.write_text(
        "---\ntype: ustc-notice\nstatus: draft\nsource: https://x/n\n---\n"
        "# 通知\n这是一条通知正文,足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]

    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    plan = server.invoke("lib.annotate", {"op": "plan", "path": "knowledge/notice.md",
                                          "changes": {"status": "active"}})
    assert plan["ok"] and plan["version"] == 1
    token = plan["confirm_token"]

    applied = server.invoke("lib.annotate", {"op": "apply", "path": "knowledge/notice.md",
                                             "changes": {"status": "active"},
                                             "confirm_token": token})
    assert applied["ok"] and applied["version"] == 1
    assert applied["index_synced"] is True
    # 文件真的改了 frontmatter,正文不动
    text = doc.read_text(encoding="utf-8")
    assert "status: active" in text and "# 通知" in text
    # 修订链有一条改动前快照
    digest = __import__("hashlib").sha256(b"knowledge/notice.md").hexdigest()
    assert (brain / "_revisions" / digest[:2] / digest / "000001.md").exists()


def test_annotate_stale_token_rejected_through_gateway(tmp_path, monkeypatch):
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "d.md").write_text(
        "---\nstatus: draft\nsource: https://x/d\n---\n正文正文正文正文正文正文正文正文正文正文。\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    server = _load_mcp().RtimeLibraryGatewayMCP()
    res = server.invoke("lib.annotate", {"op": "apply", "path": "knowledge/d.md",
                                         "changes": {"status": "active"},
                                         "confirm_token": "bogustoken"})
    assert not res["ok"]
    assert any("stale_token" in e for e in res["errors"])


def test_edit_revisions_revert_through_gateway(tmp_path, monkeypatch):
    """H M2 端到端:lib.edit 改正文 → lib.revisions 列链 → lib.revert 回滚,经网关 in-process。"""
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    doc = brain / "knowledge" / "a.md"
    doc.write_text(
        "---\nstatus: active\nsource: https://x/a\nversion: 1\n---\n"
        "# 原标题\n原始正文,足够长足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    new_body = "# 新标题\n换过的正文,同样足够长足够长足够长足够长足够长足够长足够长。\n"
    plan = server.invoke("lib.edit", {"op": "plan", "path": "knowledge/a.md", "new_body": new_body})
    assert plan["ok"] and plan["version"] == 2 and "新标题" in plan["diff"]
    applied = server.invoke("lib.edit", {"op": "apply", "path": "knowledge/a.md",
                                         "new_body": new_body, "confirm_token": plan["confirm_token"]})
    assert applied["ok"] and applied["version"] == 2
    assert applied["index_embedding_stale"] is True  # 正文变→嵌入过时提示
    assert "换过的正文" in doc.read_text(encoding="utf-8")

    revs = server.invoke("lib.revisions", {"path": "knowledge/a.md"})
    assert revs["ok"] and revs["current_version"] == 2
    snap = revs["revisions"][0]["snapshot"]  # 改动前(v1)快照

    rplan = server.invoke("lib.revert", {"op": "plan", "path": "knowledge/a.md", "snapshot": snap})
    assert rplan["ok"] and rplan["version"] == 3
    rapplied = server.invoke("lib.revert", {"op": "apply", "path": "knowledge/a.md",
                                            "snapshot": snap, "confirm_token": rplan["confirm_token"]})
    assert rapplied["ok"] and rapplied["reverted_to"] == snap
    # 正文回到原始;version 前向到 3;回滚进了链
    text = doc.read_text(encoding="utf-8")
    assert "原始正文" in text and "version: 3" in text
    assert server.invoke("lib.revisions", {"path": "knowledge/a.md"})["revisions"][-1]["verb"] == "revert"


def test_move_and_retire_through_gateway(tmp_path, monkeypatch):
    """H M3 端到端(经网关 in-process):
    - lib.move:改路径→旧路径变墓碑(status: moved)、新路径拿到原文、索引移除旧 path;
    - lib.retire:软删→归档保留、检索不再命中该路径。"""
    import sys

    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "knowledge" / "old.md").write_text(
        "---\nstatus: active\nsource: https://x/o\nversion: 1\n---\n"
        "# 星型磁体\n仿星器线圈的资料,足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "retireme.md").write_text(
        "---\nstatus: active\nsource: https://x/r\nversion: 1\n---\n"
        "# 待退役\n唯一关键词甲骨文占卜术数,足够长足够长足够长足够长足够长足够长。\n",
        encoding="utf-8",
    )
    idx = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, idx, force=True)["ok"]
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))
    server = _load_mcp().RtimeLibraryGatewayMCP()

    # --- move: knowledge/old.md -> knowledge/new.md ---
    mplan = server.invoke("lib.move", {"op": "plan", "from_path": "knowledge/old.md",
                                       "to_path": "knowledge/new.md"})
    assert mplan["ok"] and mplan["verb"] == "move"
    mres = server.invoke("lib.move", {"op": "apply", "from_path": "knowledge/old.md",
                                      "to_path": "knowledge/new.md",
                                      "confirm_token": mplan["confirm_token"]})
    assert mres["ok"] and mres["index_rebuild_needed"] is True
    # 新路径拿到原文;旧路径是 moved 墓碑
    assert "仿星器线圈" in (brain / "knowledge" / "new.md").read_text(encoding="utf-8")
    tomb = (brain / "knowledge" / "old.md").read_text(encoding="utf-8")
    assert "status: moved" in tomb and "moved_to: knowledge/new.md" in tomb
    # 旧路径已从索引移除
    hits = server.invoke("lib.search", {"query": "仿星器线圈"})
    assert all(r["path"] != "knowledge/old.md" for r in hits.get("results", []))

    # --- retire: knowledge/retireme.md ---
    before = server.invoke("lib.search", {"query": "甲骨文占卜术数"})
    assert any(r["path"] == "knowledge/retireme.md" for r in before.get("results", []))
    rplan = server.invoke("lib.retire", {"op": "plan", "path": "knowledge/retireme.md"})
    assert rplan["ok"] and rplan["archived_to"] == "_archive/knowledge/retireme.md"
    rres = server.invoke("lib.retire", {"op": "apply", "path": "knowledge/retireme.md",
                                        "confirm_token": rplan["confirm_token"]})
    assert rres["ok"]
    # 归档完整保留、原路径 retired 墓碑、检索不再命中
    assert (brain / "_archive" / "knowledge" / "retireme.md").exists()
    assert "status: retired" in (brain / "knowledge" / "retireme.md").read_text(encoding="utf-8")
    after = server.invoke("lib.search", {"query": "甲骨文占卜术数"})
    assert all(r["path"] != "knowledge/retireme.md" for r in after.get("results", []))
