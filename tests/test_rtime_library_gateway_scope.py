# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Subset read scope (``allowed_path_prefixes``) — the library gateway's
"块4 子集只读门".

A second consumer gets its OWN gateway process + policy file + port; that policy
sets ``allowed_path_prefixes`` and the gate confines every read to those brain
subtrees. These tests cover: full backward compatibility when the scope is empty,
in-scope reads passing, out-of-scope reads denied, forced narrowing (injection)
of enumerable methods, LIKE-boundary pinning, writes staying scope-free, and the
shipped studentunion policy file denying every write.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"

# Generic two-prefix fixture scope for gate-behavior tests (multi-prefix branches).
SCOPE = ["knowledge/institutions/ustc", "knowledge/activities"]
# The SHIPPED studentunion policy scope. 2026-07-02 真机核实(lib.search):活动内容在
# knowledge/institutions/ustc/activities/ 下,不存在顶层 knowledge/activities,单一
# ustc 前缀即全覆盖(单前缀 => 网关对可枚举读注入收窄,而非要求显式 path_prefix)。
SHIPPED_SCOPE = ["knowledge/institutions/ustc"]


def _load_gate():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.gate")


def _load_mcp():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.mcp_server")


def _make_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge" / "institutions" / "ustc" / "jw").mkdir(parents=True)
    (brain / "knowledge" / "activities").mkdir(parents=True)
    (brain / "knowledge" / "activities-2026").mkdir(parents=True)  # LIKE-sibling trap
    (brain / "knowledge" / "other").mkdir(parents=True)
    (brain / "personal-data").mkdir(parents=True)
    (brain / "knowledge" / "institutions" / "ustc" / "doc.md").write_text(
        "校历: 秋季学期开学时间", encoding="utf-8"
    )
    (brain / "knowledge" / "institutions" / "ustc" / "jw" / "rules.md").write_text(
        "教务处选课规则", encoding="utf-8"
    )
    (brain / "knowledge" / "activities" / "act.md").write_text("社团活动安排", encoding="utf-8")
    (brain / "knowledge" / "activities-2026" / "leak.md").write_text(
        "sibling dir sharing the name prefix", encoding="utf-8"
    )
    (brain / "knowledge" / "other" / "leak.md").write_text("out of scope", encoding="utf-8")
    (brain / "personal-data" / "secret.md").write_text("secret", encoding="utf-8")
    return brain


def _scoped_policy(gate, prefixes=tuple(SCOPE)):
    policy = gate.load_policy()
    policy["allowed_path_prefixes"] = list(prefixes)
    return policy


# ---------------------------------------------------------------------------
# backward compatibility: empty / missing scope changes NOTHING
# ---------------------------------------------------------------------------


def test_empty_scope_is_fully_backward_compatible(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    for policy in (gate.load_policy(), {**gate.load_policy(), "allowed_path_prefixes": []}):
        # any in-brain read path passes, nothing is injected, bare wide methods pass
        gate.enforce("lib.read", {"path": "knowledge/other/leak.md"}, "default", policy=policy, brain_root=brain)
        args: dict = {"query": "q"}
        gate.enforce("lib.search", args, "default", policy=policy, brain_root=brain)
        assert "path_prefix" not in args
        tree_args: dict = {}
        gate.enforce("lib.tree", tree_args, "default", policy=policy, brain_root=brain)
        assert "path" not in tree_args
        gate.enforce("lib.meta", {}, "default", policy=policy, brain_root=brain)
        gate.enforce("lib.courses", {"code": "PHYS1001B"}, "default", policy=policy, brain_root=brain)


def test_allowed_path_prefixes_parsing():
    gate = _load_gate()
    assert gate._allowed_path_prefixes({}) == []
    assert gate._allowed_path_prefixes({"allowed_path_prefixes": "nope"}) == []
    assert gate._allowed_path_prefixes({"allowed_path_prefixes": []}) == []
    assert gate._allowed_path_prefixes(
        {"allowed_path_prefixes": ["/knowledge/x/", "a\\b"]}
    ) == ["knowledge/x", "a/b"]
    # junk entries are normalized but KEPT (non-empty malformed list fails closed)
    assert gate._allowed_path_prefixes({"allowed_path_prefixes": [""]}) == [""]


# ---------------------------------------------------------------------------
# path-taking reads: inside allowed, outside denied
# ---------------------------------------------------------------------------


def test_scope_read_inside_is_allowed_outside_denied(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)

    for ok in (
        "knowledge/institutions/ustc/doc.md",
        "knowledge/institutions/ustc/jw/rules.md",
        "knowledge/activities/act.md",
        str(brain / "knowledge" / "activities" / "act.md"),  # absolute, inside scope
    ):
        gate.enforce("lib.read", {"path": ok}, "default", policy=policy, brain_root=brain)
        gate.enforce("lib.stat", {"path": ok}, "default", policy=policy, brain_root=brain)

    for bad in (
        "knowledge/other/leak.md",  # in brain, outside scope
        "knowledge/activities-2026/leak.md",  # sibling sharing the name prefix
        "knowledge",  # ancestor of scope, not inside it
        "personal-data/secret.md",
        str(brain / "knowledge" / "other" / "leak.md"),  # absolute outside scope
        "../outside.md",  # escape
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce("lib.read", {"path": bad}, "default", policy=policy, brain_root=brain)


def test_scope_error_message_names_the_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)
    with pytest.raises(gate.PolicyDenied) as exc:
        gate.enforce("lib.read", {"path": "knowledge/other/leak.md"}, "default", policy=policy, brain_root=brain)
    message = str(exc.value)
    assert "scope" in message
    for prefix in SCOPE:
        assert prefix in message


# ---------------------------------------------------------------------------
# search/recent: forced narrowing of the LIKE prefix
# ---------------------------------------------------------------------------


def test_search_without_prefix_is_injected_under_single_prefix_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    for method in ("lib.search", "lib.recent"):
        args: dict = {"query": "q"} if method == "lib.search" else {}
        gate.enforce(method, args, "default", policy=policy, brain_root=brain)
        # trailing slash pins LIKE 'p%' to the subtree (no sibling-name matches)
        assert args["path_prefix"] == "knowledge/institutions/ustc/"


def test_search_without_prefix_is_denied_under_multi_prefix_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)
    for method, args in (("lib.search", {"query": "q"}), ("lib.recent", {})):
        with pytest.raises(gate.PolicyDenied) as exc:
            gate.enforce(method, dict(args), "default", policy=policy, brain_root=brain)
        message = str(exc.value)
        assert "path_prefix" in message
        for prefix in SCOPE:
            assert prefix in message


def test_search_prefix_in_scope_allowed_out_of_scope_denied(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)

    # deeper prefixes inside the scope pass through unchanged
    args = {"query": "q", "path_prefix": "knowledge/institutions/ustc/jw"}
    gate.enforce("lib.search", args, "default", policy=policy, brain_root=brain)
    assert args["path_prefix"] == "knowledge/institutions/ustc/jw"

    for bad in (
        "knowledge",  # ancestor: LIKE would match the whole knowledge tree
        "knowledge/other",
        "knowledge/institutions/ust",  # string-prefix of the scope, crosses boundary
        "knowledge/activities-2026",  # sibling sharing the name prefix
        "personal-data",
        "/knowledge/institutions/ustc",  # absolute-style prefix is rejected outright
        "../knowledge/institutions/ustc",
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(
                "lib.search",
                {"query": "q", "path_prefix": bad},
                "default",
                policy=policy,
                brain_root=brain,
            )


def test_search_prefix_at_scope_boundary_is_pinned_with_trailing_slash(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)
    # LIKE 'knowledge/activities%' would also match knowledge/activities-2026/...;
    # the gate rewrites a boundary-equal prefix to 'knowledge/activities/'.
    args = {"query": "q", "path_prefix": "knowledge/activities"}
    gate.enforce("lib.search", args, "default", policy=policy, brain_root=brain)
    assert args["path_prefix"] == "knowledge/activities/"


# ---------------------------------------------------------------------------
# tree / list: inject-or-require for their subtree argument
# ---------------------------------------------------------------------------


def test_tree_injection_and_validation(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)

    single = _scoped_policy(gate, prefixes=("knowledge/activities",))
    args: dict = {}
    gate.enforce("lib.tree", args, "default", policy=single, brain_root=brain)
    assert args["path"] == "knowledge/activities"

    multi = _scoped_policy(gate)
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.tree", {}, "default", policy=multi, brain_root=brain)
    gate.enforce(
        "lib.tree", {"path": "knowledge/institutions/ustc/jw"}, "default", policy=multi, brain_root=brain
    )
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.tree", {"path": "knowledge"}, "default", policy=multi, brain_root=brain)


def test_list_root_injection_is_absolute_under_brain_root(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    single = _scoped_policy(gate, prefixes=("knowledge/activities",))
    args: dict = {"op": "docpacks"}
    gate.enforce("lib.list", args, "default", policy=single, brain_root=brain)
    # root is a filesystem path for the backend (relative would resolve against
    # the subprocess CWD) -> injected absolute under the brain root
    assert Path(args["root"]).is_absolute()
    assert args["root"] == str(brain / "knowledge/activities")

    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.list", {"op": "scan"}, "default", policy=_scoped_policy(gate), brain_root=brain)
    with pytest.raises(gate.PolicyDenied):
        gate.enforce(
            "lib.list",
            {"op": "scan", "root": str(brain)},
            "default",
            policy=single,
            brain_root=brain,
        )


# ---------------------------------------------------------------------------
# other reads: no path argument = implicit full-library default = denied;
# in-process/self-describing surfaces stay available
# ---------------------------------------------------------------------------


def test_bare_full_library_reads_are_denied_under_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)
    for method, args in (
        ("lib.meta", {}),
        ("lib.courses", {"code": "PHYS1001B"}),
        ("lib.freshness", {}),
        ("lib.docpack", {"op": "doctor"}),
        ("lib.hub", {"op": "panel"}),
        ("lib.jobs.list", {}),
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(method, dict(args), "default", policy=policy, brain_root=brain)


def test_exempt_methods_stay_available_under_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)
    for method in ("lib.doctor", "lib.policy", "lib.status", "lib.audit", "lib.get"):
        gate.enforce(method, {}, "default", policy=policy, brain_root=brain)
    gate.enforce(
        "lib.preview", {"method": "lib.read", "arguments": {}}, "default", policy=policy, brain_root=brain
    )


def test_junk_only_scope_fails_closed(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate, prefixes=("",))  # non-empty list, no usable prefix
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.read", {"path": "knowledge/activities/act.md"}, "default", policy=policy, brain_root=brain)
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.search", {"query": "q"}, "default", policy=policy, brain_root=brain)


# ---------------------------------------------------------------------------
# writes: scope does NOT apply (a scoped deployment denies writes via policy)
# ---------------------------------------------------------------------------


def test_write_methods_are_not_scope_checked(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate)  # repo default policy keeps writes allowed
    # a write whose source_path is inside the brain but OUTSIDE the read scope
    # passes the gate: scope is a READ confinement; writes are governed by
    # default_write / client deny rules instead.
    gate.enforce(
        "lib.settings.context_source_add",
        {"id": "x", "kind": "note", "title": "t", "source_path": "knowledge/other/leak.md"},
        "default",
        policy=policy,
        brain_root=brain,
    )
    gate.enforce(
        "lib.contribute", {"title": "t", "text": "body"}, "default", policy=policy, brain_root=brain
    )


# ---------------------------------------------------------------------------
# the shipped studentunion policy file
# ---------------------------------------------------------------------------


def _studentunion_policy():
    path = SRC.parent / "policy" / "studentunion-policy.json"
    return json.loads(path.read_text(encoding="utf-8")), path


def test_studentunion_policy_denies_every_write_method(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy, _ = _studentunion_policy()
    write_methods = [m for m, tier in gate.METHOD_TIERS.items() if tier == "write"]
    assert write_methods  # sanity
    for method in write_methods:
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(method, {}, "default", policy=policy, brain_root=brain)
        # the self-reported client name gives no escape: unknown names fall back
        # to the same "default" rule
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(method, {}, "owner", policy=policy, brain_root=brain)


def test_studentunion_policy_denies_non_allowed_reads(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy, _ = _studentunion_policy()
    for method, args in (
        ("lib.jobs.get", {"id": "x"}),
        ("lib.jobs.list", {}),
        ("lib.meta", {}),
        ("lib.hub", {"op": "contacts"}),
        ("lib.courses", {"code": "PHYS1001B"}),
        ("lib.audit", {}),
        ("lib.get", {}),
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(method, dict(args), "default", policy=policy, brain_root=brain)


def test_studentunion_policy_scoped_reads(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy, _ = _studentunion_policy()

    gate.enforce(
        "lib.read", {"path": "knowledge/institutions/ustc/doc.md"}, "default", policy=policy, brain_root=brain
    )
    gate.enforce(
        "lib.search",
        {"query": "选课", "path_prefix": "knowledge/institutions/ustc/jw"},
        "default",
        policy=policy,
        brain_root=brain,
    )
    # single-prefix shipped scope: bare enumerable reads get the narrowing INJECTED
    search_args: dict = {"query": "q"}
    gate.enforce("lib.search", search_args, "default", policy=policy, brain_root=brain)
    assert search_args["path_prefix"] == "knowledge/institutions/ustc/"
    tree_args: dict = {}
    gate.enforce("lib.tree", tree_args, "default", policy=policy, brain_root=brain)
    assert tree_args["path"] == "knowledge/institutions/ustc"
    for bad_call in (
        ("lib.read", {"path": "knowledge/other/leak.md"}),
        ("lib.read", {"path": "personal-data/secret.md"}),
        ("lib.search", {"query": "q", "path_prefix": "knowledge"}),  # ancestor escape
        ("lib.tree", {"path": "knowledge"}),
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(bad_call[0], dict(bad_call[1]), "default", policy=policy, brain_root=brain)


def test_studentunion_policy_switches(tmp_path):
    gate = _load_gate()
    policy, _ = _studentunion_policy()
    assert policy["default_write"] == "deny"
    assert policy["redact_sensitive"] is True
    assert policy["hide_excluded_in_results"] is True
    assert set(policy["excluded_top_dirs"]) == {"personal-data", "profile"}
    assert gate._allowed_path_prefixes(policy) == SHIPPED_SCOPE
    # its audit stream must not interleave with the owner instance's
    assert policy["audit_log"] != gate._DEFAULT_POLICY["audit_log"]


# ---------------------------------------------------------------------------
# end to end through RtimeLibraryGatewayMCP.invoke (gate -> dispatch -> backend)
# ---------------------------------------------------------------------------


@pytest.fixture()
def e2e_env(tmp_path, monkeypatch):
    brain = _make_brain(tmp_path)
    _, policy_path = _studentunion_policy()
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(policy_path))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(tmp_path / "audit.jsonl"))
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(tmp_path / "idx" / "brain-library.sqlite"))
    return brain, tmp_path


def test_invoke_in_scope_read_succeeds(e2e_env):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    data = server.invoke("lib.read", {"path": "knowledge/institutions/ustc/doc.md"})
    assert data.get("ok") is True
    assert "校历" in json.dumps(data, ensure_ascii=False)


def test_invoke_out_of_scope_read_and_write_are_denied_and_audited(e2e_env):
    brain, tmp_path = e2e_env
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()

    with pytest.raises(mcp.ToolError, match="scope"):
        server.invoke("lib.read", {"path": "knowledge/other/leak.md"})
    with pytest.raises(mcp.ToolError):
        server.invoke("lib.contribute", {"title": "t", "text": "x"})
    with pytest.raises(mcp.ToolError, match="scope"):
        # bare search would get the single prefix injected; an ANCESTOR prefix
        # (would LIKE-match the whole knowledge tree) must be denied.
        server.invoke("lib.search", {"query": "q", "path_prefix": "knowledge"})

    audit_lines = [
        json.loads(line)
        for line in (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    denies = [rec for rec in audit_lines if rec["decision"] == "deny"]
    assert len(denies) == 3


def test_invoke_search_results_are_confined_to_scope(e2e_env, monkeypatch):
    """Full-stack narrowing proof: a real BM25 index over the whole brain, queried
    through the gateway under the scoped policy, returns only in-scope rows."""
    brain, tmp_path = e2e_env
    src = str(ROOT / "packages" / "brain-library" / "src")
    if src not in sys.path:
        sys.path.insert(0, src)
    from brain_library import indexer

    index_path = tmp_path / "idx" / "brain-library.sqlite"
    built = indexer.build_index(brain, index_path, embed=False)
    assert built.get("ok") is True

    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    # single-prefix scope so the gate INJECTS the narrowing (strongest e2e claim)
    policy_path = tmp_path / "scoped-single.json"
    scoped, _ = _studentunion_policy()
    scoped["allowed_path_prefixes"] = ["knowledge/institutions/ustc"]
    policy_path.write_text(json.dumps(scoped, ensure_ascii=False), encoding="utf-8")
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(policy_path))

    data = server.invoke("lib.search", {"query": "选课 规则", "mode": "bm25"})
    assert data.get("ok") is True
    results = data.get("results") or []
    assert results, "expected at least one in-scope hit"
    for row in results:
        assert str(row["path"]).startswith("knowledge/institutions/ustc/")

    # and out-of-scope content is not reachable even by direct query terms
    data2 = server.invoke("lib.search", {"query": "sibling prefix", "mode": "bm25"})
    for row in data2.get("results") or []:
        assert str(row["path"]).startswith("knowledge/institutions/ustc/")


# ---------------------------------------------------------------------------
# P5 阶段0 硬化: index-reject (H1), scope result filter (defense-in-depth),
# lib.get scope-trim (H2), compose stop-bind portability assertion
# ---------------------------------------------------------------------------


def test_index_reject_under_scope(tmp_path):
    """H1: a scoped caller may NOT name the index. The path_prefix pushdown is the
    only scope backstop and it follows whichever index the query names; ``index``
    is exempt from PATH_LIKE_KEYS, so re-pointing it at a full-library file would
    bypass path_prefix. The gate forces its own server-side default index."""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    other_index = str(tmp_path / "full-library.sqlite")
    # every method that accepts ``index`` (search / get / courses / stat / recent /
    # freshness) is rejected — including the SCOPE_EXEMPT lib.get (H2 same source).
    for method, extra in (
        ("lib.search", {"query": "q"}),
        ("lib.get", {}),
        ("lib.courses", {"code": "PHYS1001B"}),
        ("lib.stat", {"path": "knowledge/institutions/ustc/doc.md"}),
        ("lib.recent", {}),
        ("lib.freshness", {}),
    ):
        with pytest.raises(gate.PolicyDenied, match="index"):
            gate.enforce(
                method, {**extra, "index": other_index}, "default", policy=policy, brain_root=brain
            )
    # an empty / whitespace index string is not "naming an index" -> not rejected
    # on that ground (it falls through to the method's normal scope handling).
    ok_args = {"query": "q", "index": "  "}
    gate.enforce("lib.search", ok_args, "default", policy=policy, brain_root=brain)
    assert ok_args["path_prefix"] == "knowledge/institutions/ustc/"


def test_index_reject_is_noop_without_scope(tmp_path):
    """Full-library (empty scope) callers keep naming the index (owner instance)."""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = gate.load_policy()  # repo default: empty scope
    gate.enforce(
        "lib.search",
        {"query": "q", "index": str(tmp_path / "any.sqlite")},
        "default",
        policy=policy,
        brain_root=brain,
    )


def test_index_reject_e2e_through_invoke(e2e_env):
    """End to end: the shipped scoped policy rejects a caller-supplied index on an
    allow-listed method (lib.search). (lib.get is denied outright by the shipped
    client allow-list before the scope step even runs — a stronger cut; the
    index-reject on lib.get is covered at the unit level in test_index_reject_
    under_scope.)"""
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    with pytest.raises(mcp.ToolError, match="index"):
        server.invoke("lib.search", {"query": "q", "index": "/tmp/full.sqlite"})


def test_scope_result_filter_drops_out_of_scope_row(tmp_path):
    """Defense-in-depth: a row outside allowed_path_prefixes is dropped from the
    result set EVEN when hide_excluded_in_results would not touch it (the row is
    under an allowed top dir like knowledge/, just outside the subtree scope)."""
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    gate = _load_gate()
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    policy["hide_excluded_in_results"] = False  # isolate the scope filter
    # a backend that (hypothetically) returned an out-of-scope knowledge/ row:
    data = {
        "ok": True,
        "results": [
            {"path": "knowledge/institutions/ustc/doc.md", "title": "in"},
            {"path": "knowledge/other/leak.md", "title": "out-of-scope, not excluded"},
            {"path": "knowledge/activities/act.md", "title": "out-of-scope sibling"},
        ],
        "result_count": 3,
    }
    removed = server._scope_filter_results("lib.search", data, policy)
    assert removed == 2
    assert [r["path"] for r in data["results"]] == ["knowledge/institutions/ustc/doc.md"]
    assert data["result_count"] == 1
    assert data["scope_filtered_count"] == 2


def test_scope_result_filter_noop_without_scope(tmp_path):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    gate = _load_gate()
    data = {"results": [{"path": "knowledge/other/leak.md"}], "result_count": 1}
    assert server._scope_filter_results("lib.search", data, gate.load_policy()) == 0
    assert len(data["results"]) == 1


def test_scope_result_filter_fails_closed_on_junk_scope(tmp_path):
    """A non-empty scope with no usable prefix drops ALL rows (fail closed)."""
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    gate = _load_gate()
    policy = _scoped_policy(gate, prefixes=("",))
    data = {"results": [{"path": "knowledge/institutions/ustc/doc.md"}], "result_count": 1}
    assert server._scope_filter_results("lib.search", data, policy) == 1
    assert data["results"] == []


def test_lib_get_scope_trim(tmp_path):
    """H2: under a scope, lib.get keeps liveness/config fields but drops the brain
    root path and full-library aggregate counts (+ the raw meta blob)."""
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    gate = _load_gate()
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    data = {
        "ok": True,
        "index": "/state/brain-library.sqlite",
        "schema_version": 4,
        "tokenizer": "jieba",
        "root": "/mnt/brain",  # brain root path — must be dropped
        "created_at": "2026-07-02",
        "document_count": 30426,  # full-library count — must be dropped
        "fts_count": 30426,
        "embed_model": "bge-small",
        "embed_dim": 512,
        "vector_count": 30426,
        "has_vectors": True,
        "meta": {"root": "/mnt/brain", "document_count": 30426},
    }
    removed = server._scope_trim_get("lib.get", data, policy)
    assert removed == 5
    # sensitive fields gone
    for gone in ("root", "document_count", "fts_count", "vector_count", "meta"):
        assert gone not in data
    # liveness / config fields kept
    for kept in ("ok", "index", "schema_version", "tokenizer", "embed_model", "has_vectors"):
        assert kept in data
    assert data["scope_trimmed"] is True


def test_lib_get_scope_trim_noop_without_scope(tmp_path):
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    gate = _load_gate()
    data = {"ok": True, "root": "/mnt/brain", "document_count": 100}
    assert server._scope_trim_get("lib.get", data, gate.load_policy()) == 0
    assert data["root"] == "/mnt/brain"  # owner keeps the full aggregate


def test_scoped_consumer_does_not_bind_full_index_in_compose():
    """Portability / defense-in-depth assertion (P5 阶段0): no scoped consumer
    (qq-bridge) may bind the full-library index into its container — it reaches the
    library only through its scope gateway. Binding the sqlite (even read-only) lets
    any in-container process open it directly and read personal-data, bypassing
    allowed_path_prefixes."""
    import yaml  # provided transitively by rtime-config (pyyaml)

    compose = yaml.safe_load((ROOT / "compose.prod.yml").read_text(encoding="utf-8"))
    qq = compose["services"]["qq-bridge"]
    for vol in qq.get("volumes", []):
        target = vol["target"] if isinstance(vol, dict) else str(vol).split(":")[1]
        assert target != "/brain-index", (
            "qq-bridge is a scoped consumer and must NOT bind the full-library index; "
            "library access goes through the 8781 scope gateway only"
        )


# ---------------------------------------------------------------------------
# 对抗审查 HIGH #2: load_policy fail-closed for an EXPLICITLY named policy.
# A broken named policy (scoped 8781 points at studentunion-policy.json) must
# NEVER silently degrade to the wider owner default (empty allowed_path_prefixes
# = full library incl personal-data). A load failure there is fatal.
# ---------------------------------------------------------------------------


def test_named_policy_load_failure_fails_closed_bad_json(tmp_path, monkeypatch):
    gate = _load_gate()
    bad = tmp_path / "broken-policy.json"
    bad.write_text('{"allowed_path_prefixes": ["knowledge/x"],}', encoding="utf-8")  # trailing comma
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(bad))
    with pytest.raises(gate.GateError, match="not valid JSON"):
        gate.load_policy()


def test_named_policy_load_failure_fails_closed_unreadable(tmp_path, monkeypatch):
    gate = _load_gate()
    missing = tmp_path / "does-not-exist.json"  # never created
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(missing))
    with pytest.raises(gate.GateError, match="cannot be read"):
        gate.load_policy()


def test_named_policy_not_object_fails_closed(tmp_path, monkeypatch):
    gate = _load_gate()
    weird = tmp_path / "list-policy.json"
    weird.write_text("[1, 2, 3]", encoding="utf-8")  # valid JSON, not an object
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(weird))
    with pytest.raises(gate.GateError, match="not a JSON object"):
        gate.load_policy()


def test_named_policy_load_failure_never_returns_full_library(tmp_path, monkeypatch):
    """The whole point: a broken NAMED scoped policy must not resolve to a policy
    with an empty allowed_path_prefixes (= full library). It must raise instead."""
    gate = _load_gate()
    bad = tmp_path / "broken.json"
    bad.write_text("not json at all", encoding="utf-8")
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(bad))
    try:
        policy = gate.load_policy()
    except gate.GateError:
        return  # correct: fail-closed
    # If it somehow returned, it must NOT be the wide-open owner default.
    assert gate._allowed_path_prefixes(policy), (
        "broken named scoped policy silently degraded to a full-library policy"
    )


def test_no_explicit_policy_still_falls_back(tmp_path, monkeypatch):
    """Only the zero-config (no env) case may fall back through repo default ->
    builtin. Unset env -> a valid policy, no raise (single-owner backward compat)."""
    gate = _load_gate()
    monkeypatch.delenv("RTIME_LIBRARY_GATEWAY_POLICY", raising=False)
    policy = gate.load_policy()
    assert isinstance(policy, dict)
    assert "methods" in policy or "default_read" in policy


def test_e2e_broken_named_policy_denies_instead_of_serving_full_library(tmp_path, monkeypatch):
    """End to end through invoke: a broken named policy makes every call fail
    closed (GateError surfaced), never serves the full library."""
    brain = _make_brain(tmp_path)
    bad = tmp_path / "broken.json"
    bad.write_text('{"oops",}', encoding="utf-8")
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(bad))
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    mcp = _load_mcp()
    server = mcp.RtimeLibraryGatewayMCP()
    with pytest.raises(mcp.ToolError):
        server.invoke("lib.read", {"path": "personal-data/secret.md"})


# ---------------------------------------------------------------------------
# 对抗审查 MEDIUM #1: a decoy in-scope path must NOT let a full-library aggregate
# method (its builder ignores the path) run under scope. Such methods are DENIED —
# the scope layer confines every read on its own, not relying on the client
# allow-list. owner (empty scope) is unaffected.
# ---------------------------------------------------------------------------


def test_full_library_aggregate_methods_denied_under_scope_even_with_decoy_path(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    decoy = "knowledge/institutions/ustc"  # a perfectly in-scope path
    # these builders IGNORE path -> a decoy must not satisfy scope
    for method, args in (
        ("lib.freshness", {"path": decoy}),
        ("lib.courses", {"path": decoy, "code": "PHYS1001B"}),
        ("lib.jobs.list", {"path": decoy}),
        ("lib.jobs.get", {"path": decoy, "id": "x"}),
        ("lib.context", {"path": decoy, "op": "doctor"}),
        ("lib.profile", {"path": decoy, "op": "panel"}),
        ("lib.automation", {"path": decoy, "op": "panel"}),
    ):
        with pytest.raises(gate.PolicyDenied, match="cannot be confined"):
            gate.enforce(method, dict(args), "default", policy=policy, brain_root=brain)


def test_constrainable_methods_still_work_with_in_scope_path(tmp_path):
    """Methods whose builder DOES consume a path stay usable with an in-scope value
    (must not be over-denied)."""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _scoped_policy(gate, prefixes=("knowledge/institutions/ustc",))
    gate.enforce(
        "lib.read", {"path": "knowledge/institutions/ustc/doc.md"}, "default",
        policy=policy, brain_root=brain,
    )
    gate.enforce(
        "lib.stat", {"path": "knowledge/institutions/ustc/doc.md"}, "default",
        policy=policy, brain_root=brain,
    )
    # a constrainable method with NO constraining key present -> denied (needs one)
    with pytest.raises(gate.PolicyDenied, match="needs an in-scope"):
        gate.enforce("lib.read", {}, "default", policy=policy, brain_root=brain)


def test_full_library_aggregate_methods_unaffected_for_owner(tmp_path):
    """owner (empty scope) keeps calling the aggregate methods normally."""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = gate.load_policy()  # repo default: empty scope
    for method, args in (
        ("lib.freshness", {}),
        ("lib.courses", {"code": "PHYS1001B"}),
        ("lib.jobs.list", {}),
        ("lib.context", {"op": "doctor"}),
    ):
        gate.enforce(method, dict(args), "default", policy=policy, brain_root=brain)


def test_scope_constrainable_keys_match_dispatch_builders():
    """Static consistency: every key in SCOPE_CONSTRAINABLE_KEYS is (a) in
    PATH_LIKE_KEYS (so the downstream scope check actually validates it), and (b)
    actually consumed by the method's dispatch builder. And every non-inject,
    non-exempt READ method is either constrainable or intentionally deniable —
    no read method is silently un-classified."""
    import ast

    gate = _load_gate()
    dispatch_src = (SRC / "rtime_library_gateway" / "dispatch.py").read_text(encoding="utf-8")
    tree = ast.parse(dispatch_src)
    funcs = {n.name: n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    method_to_builder = {
        "lib.read": "_build_read", "lib.stat": "_build_stat", "lib.docpack": "_build_docpack",
        "lib.review": "_build_review", "lib.runtime": "_build_runtime", "lib.meta": "_build_meta",
        "lib.citation": "_build_citation",
    }

    def _consumed(fn_name: str) -> set[str]:
        node = funcs[fn_name]
        out: set[str] = set()
        for sub in ast.walk(node):
            if isinstance(sub, ast.Call):
                for a in sub.args:
                    if isinstance(a, ast.Constant) and isinstance(a.value, str):
                        out.add(a.value)
        return out

    # (a) every constrainable key must be in PATH_LIKE_KEYS
    for method, keys in gate.SCOPE_CONSTRAINABLE_KEYS.items():
        for k in keys:
            assert k in gate.PATH_LIKE_KEYS, f"{method}: {k} not scope-validated"
        # (b) the builder actually consumes the declared key(s)
        consumed = _consumed(method_to_builder[method])
        assert any(k in consumed for k in keys), f"{method}: builder ignores {keys}"

    # every READ method is classified (inject / exempt / constrainable / denied)
    read_methods = {m for m, t in gate.METHOD_TIERS.items() if t == "read"}
    classified = (
        set(gate.SCOPE_INJECT_KEYS)
        | set(gate.SCOPE_EXEMPT_METHODS)
        | set(gate.SCOPE_CONSTRAINABLE_KEYS)
    )
    # the remainder are the intentional full-library-aggregate deny set
    deny_set = read_methods - classified
    assert deny_set == {
        "lib.freshness", "lib.courses", "lib.context", "lib.profile",
        "lib.automation", "lib.hub", "lib.jobs.get", "lib.jobs.list",
        # H M2:修订历史=owner 内省,scoped 下恒拒(与 annotate/edit/revert 一致)
        "lib.revisions",
    }, f"unexpected read-method classification: {sorted(deny_set)}"


# ---------------------------------------------------------------------------
# H M1/M2: direct brain writes (annotate/edit/revert) — DENIED under any non-empty scope
# ---------------------------------------------------------------------------
def test_annotate_denied_under_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    scoped = _scoped_policy(gate)
    with pytest.raises(gate.PolicyDenied):
        gate.enforce(
            "lib.annotate",
            {"op": "apply", "path": "knowledge/activities/act.md", "changes": {"status": "active"}},
            "default",
            policy=scoped,
            brain_root=brain,
        )


def test_edit_and_revert_denied_under_scope(tmp_path):
    """H M2/M3:改正文/回滚/移动/软删/恢复都是直接写,scoped 实例即便策略被误配也恒拒
    (gate.SCOPE_DENIED_WRITE_METHODS 在 default_write 之外硬拦,fail-closed)。"""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    scoped = _scoped_policy(gate)
    for method, args in (
        ("lib.edit", {"op": "apply", "path": "knowledge/activities/act.md", "new_body": "x"}),
        ("lib.revert", {"op": "apply", "path": "knowledge/activities/act.md", "snapshot": "000001.md"}),
        # H M3 维护写动词(移动/软删/恢复)——同 edit,scoped 恒拒。
        ("lib.move", {"op": "apply", "from_path": "knowledge/activities/act.md",
                      "to_path": "knowledge/activities/act2.md"}),
        ("lib.retire", {"op": "apply", "path": "knowledge/activities/act.md"}),
        ("lib.restore", {"op": "apply", "path": "knowledge/activities/act.md"}),
        # lib.revisions 是读,但修订历史=owner 内省,未分类=scoped 下恒拒(整个 M2 维护面 owner-only)
        ("lib.revisions", {"path": "knowledge/institutions/ustc/jw/doc.md"}),
    ):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce(method, args, "default", policy=scoped, brain_root=brain)


def test_revisions_allowed_under_empty_scope(tmp_path):
    """空 scope(超管)下 lib.revisions 放行到 in-process handler。"""
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    dec = gate.enforce(
        "lib.revisions",
        {"path": "knowledge/activities/act.md"},
        "default", policy=gate.load_policy(), brain_root=brain,
    )
    assert dec["tier"] == "read"


def test_annotate_allowed_under_empty_scope(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    # empty scope (super-admin owner/dev instance): the gate lets it through to dispatch.
    decision = gate.enforce(
        "lib.annotate",
        {"op": "plan", "path": "knowledge/activities/act.md", "changes": {"status": "active"}},
        "default",
        policy=gate.load_policy(),
        brain_root=brain,
    )
    assert decision["tier"] == "write"
