# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"


def _load_gate():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.gate")


def _load_dispatch():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.dispatch")


def _make_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    (brain / "personal-data").mkdir(parents=True)
    (brain / "knowledge" / "doc.md").write_text("x", encoding="utf-8")
    (brain / "personal-data" / "secret.md").write_text("x", encoding="utf-8")
    return brain


def _gated_policy(gate):
    """A policy that KEEPS personal-data gated.

    The deployed single-owner policy opens personal-data (``excluded_top_dirs: []``):
    the owner reads their own library through the gateway from every device. These
    tests exercise the exclusion MECHANISM, so they pin a policy that still excludes
    personal-data — proving the gate denies whenever a deployment chooses to gate it.
    """
    policy = gate.load_policy()
    policy["excluded_top_dirs"] = ["personal-data"]
    return policy


def test_excluded_top_dirs_honors_explicit_empty_list():
    # Regression: an explicit empty list means "exclude nothing" (single-owner full
    # open) and must NOT silently fall back to the built-in default. Only a missing or
    # non-list value falls back.
    gate = _load_gate()
    assert gate._excluded_top_dirs({"excluded_top_dirs": []}) == set()
    assert gate._excluded_top_dirs({"excluded_top_dirs": ["personal-data"]}) == {"personal-data"}
    assert gate._excluded_top_dirs({}) == set(gate.EXCLUDED_TOP_DIRS)
    assert gate._excluded_top_dirs({"excluded_top_dirs": "nope"}) == set(gate.EXCLUDED_TOP_DIRS)


def test_personal_data_path_is_denied(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _gated_policy(gate)

    # A normal knowledge path is allowed.
    gate.enforce("lib.docpack", {"path": "knowledge/doc.md"}, "default", policy=policy, brain_root=brain)

    # A path under personal-data/ is denied before any subprocess runs.
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.docpack", {"path": "personal-data/secret.md"}, "default", policy=policy, brain_root=brain)


def test_path_escape_is_denied(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)

    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.docpack", {"path": "../outside.md"}, "default", brain_root=brain)


def test_is_excluded_top_is_case_insensitive():
    # Platform-independent: directly exercise the case-folding helper (the path
    # variant test below relies on FS resolution, which silently no-ops the fold
    # on a case-insensitive dev box — this asserts the fold regardless of OS).
    gate = _load_gate()
    excluded = {"personal-data"}
    assert gate._is_excluded_top("personal-data", excluded) is True
    assert gate._is_excluded_top("Personal-Data", excluded) is True
    assert gate._is_excluded_top("PERSONAL-DATA", excluded) is True
    assert gate._is_excluded_top("personal-DATA", excluded) is True
    assert gate._is_excluded_top("knowledge", excluded) is False


def test_personal_data_path_is_denied_case_insensitively(tmp_path):
    # On case-insensitive filesystems Personal-Data resolves to the same dir as
    # personal-data and must be rejected identically (portability hardening).
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _gated_policy(gate)
    for variant in ("Personal-Data/secret.md", "PERSONAL-DATA/secret.md", "personal-DATA/x.md"):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce("lib.docpack", {"path": variant}, "default", policy=policy, brain_root=brain)


def test_path_prefix_targeting_personal_data_is_denied(tmp_path):
    # path_prefix feeds a LIKE 'prefix%' filter over the full-library index. Because
    # matching is by STRING prefix, every prefix that could match a personal-data row
    # must be rejected — not just the literal dir name, but any prefix of it ("pe",
    # "personal"), case variants, and prefixes pointing inside it.
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _gated_policy(gate)
    for bad in ("personal-data", "personal", "pe", "Personal-Data", "personal-data/health", "/personal-data"):
        with pytest.raises(gate.PolicyDenied):
            gate.enforce("lib.recent", {"path_prefix": bad}, "default", policy=policy, brain_root=brain)
        with pytest.raises(gate.PolicyDenied):
            gate.enforce("lib.search", {"query": "x", "path_prefix": bad}, "default", policy=policy, brain_root=brain)


def test_path_prefix_legitimate_subtrees_are_allowed(tmp_path):
    # Real subtree prefixes — including a different dir that merely SHARES the
    # personal-data name prefix — must still pass; the check must not over-block.
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    policy = _gated_policy(gate)
    for ok in ("knowledge", "knowledge/papers", "personal-database", "courses/optics"):
        gate.enforce("lib.recent", {"path_prefix": ok}, "default", policy=policy, brain_root=brain)
    # path_prefix works even without a brain_root (the prefix check is independent)
    gate.enforce("lib.recent", {"path_prefix": "knowledge"}, "default", policy=policy)


def test_absolute_personal_data_path_is_denied(tmp_path):
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    abs_personal = str(brain / "personal-data" / "secret.md")
    policy = _gated_policy(gate)

    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.docpack", {"path": abs_personal}, "default", policy=policy, brain_root=brain)


def test_absolute_external_index_is_allowed_for_search(tmp_path):
    # Regression: the BM25 index is a derived cache that lives OUTSIDE the brain
    # root by design (~/.local/state/...). The gate used to reject it with
    # "absolute paths are not allowed: index", which broke lib.search entirely.
    gate = _load_gate()
    brain = _make_brain(tmp_path)
    external_index = str(tmp_path / "state" / "brain-library" / "brain-library.sqlite")

    decision = gate.enforce(
        "lib.search",
        {"index": external_index, "query": "q"},
        "default",
        brain_root=brain,
    )
    assert decision["tier"] == "read"

    # lib.get takes the same external index and must also be allowed.
    gate.enforce("lib.get", {"index": external_index}, "default", brain_root=brain)


def test_redact_output_replaces_sensitive_lines():
    gate = _load_gate()
    text = "\n".join(
        [
            "line one is fine",
            "open_id: ou_abcdef12345678",
            "app_secret=swordfish",
            "another safe line",
        ]
    )
    redacted, count = gate.redact_output(text)
    assert count == 2
    assert "ou_abcdef12345678" not in redacted
    assert "app_secret=swordfish" not in redacted
    assert gate.REDACTED_LINE in redacted
    assert "line one is fine" in redacted
    assert "another safe line" in redacted


def test_redact_output_force_overrides_disabled_redaction():
    gate = _load_gate()
    text = "contact ou_abcdef12345678"
    # redact disabled but force on (contacts lane) -> still redacted
    redacted, count = gate.redact_output(text, force=True, redact=False)
    assert count == 1
    assert "ou_abcdef12345678" not in redacted


def test_redact_json_redacts_structured_strings():
    gate = _load_gate()
    data = {
        "ok": True,
        "items": [
            {"title": "safe", "note": "contact ou_abcdef12345678"},
            {"title": "also safe"},
        ],
    }

    redacted, count = gate.redact_json(data)

    assert count == 1
    assert redacted["items"][0]["note"] == gate.REDACTED_LINE
    assert redacted["items"][1]["title"] == "also safe"


def test_disabled_method_is_denied():
    gate = _load_gate()
    policy = gate.load_policy()
    policy["methods"]["lib.search"] = {"tier": "read", "enabled": False}
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.search", {"index": "i", "query": "q"}, "default", policy=policy)


def test_unknown_method_raises_gate_error():
    gate = _load_gate()
    with pytest.raises(gate.GateError):
        gate.enforce("lib.nope", {}, "default")


def test_default_write_deny_blocks_write_methods():
    gate = _load_gate()
    policy = gate.load_policy()
    policy["default_write"] = "deny"
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.settings.reminder_cancel", {"id": "x"}, "default", policy=policy)
    # read still allowed
    gate.enforce("lib.list", {}, "default", policy=policy)


def test_client_deny_rule_blocks_method():
    gate = _load_gate()
    policy = gate.load_policy()
    policy["clients"] = {"default": {"allow": ["*"]}, "kimi": {"deny": ["lib.settings.*"]}}
    with pytest.raises(gate.PolicyDenied):
        gate.enforce("lib.settings.reminder_register", {"due": "d", "message": "m"}, "kimi", policy=policy)
    # other clients unaffected
    gate.enforce("lib.settings.reminder_register", {"due": "d", "message": "m"}, "default", policy=policy)


def test_record_audit_is_metadata_only(tmp_path, monkeypatch):
    gate = _load_gate()
    log = tmp_path / "audit.jsonl"
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", str(log))
    record = gate.record_audit(
        method="lib.settings.memory_candidate_add",
        client_id="default",
        tier="write",
        decision="allow",
        exit_code=0,
        duration_ms=12,
        arguments={"claim": "PRIVATE CLAIM TEXT", "source_path": "knowledge/a/b.md"},
        redacted_line_count=0,
    )
    assert record is not None
    body = log.read_text(encoding="utf-8")
    assert "PRIVATE CLAIM TEXT" not in body
    import json

    line = json.loads(body.splitlines()[0])
    assert set(line) == {
        "audit_id",
        "ts",
        "client_id",
        "method",
        "tier",
        "decision",
        "exit_code",
        "duration_ms",
        "input_path_basenames",
        "redacted_line_count",
    }
    # only basenames, never the full path or claim
    assert line["input_path_basenames"] == ["b.md"]
    assert "claim" not in line


def test_record_audit_expands_default_state_placeholder(tmp_path, monkeypatch):
    gate = _load_gate()
    monkeypatch.delenv("RTIME_LIBRARY_GATEWAY_AUDIT_LOG", raising=False)
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    gate.record_audit(
        method="lib.doctor",
        client_id="default",
        tier="read",
        decision="allow",
        exit_code=0,
        duration_ms=1,
        arguments={},
        redacted_line_count=0,
    )

    log = tmp_path / "state" / "rtime-library-gateway" / "audit.jsonl"
    assert log.is_file()
    record = json.loads(log.read_text(encoding="utf-8").splitlines()[0])
    assert record["method"] == "lib.doctor"


def test_read_and_write_dispatch_tables_are_disjoint():
    dispatch = _load_dispatch()
    read_keys = set(dispatch.READ_DISPATCH)
    write_keys = set(dispatch.WRITE_DISPATCH)
    assert read_keys.isdisjoint(write_keys)


# Methods that the gateway serves in-process (no dispatch builder, handled inside
# mcp_server) — must be kept in sync with mcp_server._INPROCESS.
_INPROCESS_METHODS = {"lib.doctor", "lib.status", "lib.policy", "lib.audit", "lib.preview",
                      "lib.annotate", "lib.edit", "lib.revert", "lib.revisions",
                      "lib.move", "lib.retire", "lib.restore"}


def test_every_method_tier_is_dispatchable_or_inprocess():
    """Every method declared in METHOD_TIERS must be reachable: either it has a
    dispatch builder (READ/WRITE_DISPATCH) or it is served in-process. A method in
    the tier table with no builder and no in-process handler is a dead method that
    would raise at call time — this guard catches forgetting to register a builder."""
    gate = _load_gate()
    dispatch = _load_dispatch()
    dispatchable = set(dispatch.READ_DISPATCH) | set(dispatch.WRITE_DISPATCH) | _INPROCESS_METHODS
    declared = set(gate.METHOD_TIERS)
    assert declared == dispatchable, {
        "in_tiers_not_reachable": sorted(declared - dispatchable),
        "reachable_not_in_tiers": sorted(dispatchable - declared),
    }


def test_inprocess_methods_match_server():
    """The in-process set in this test mirrors mcp_server._INPROCESS; keep them equal
    so the dispatchability guard above stays honest."""
    import importlib

    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    mcp = importlib.import_module("rtime_library_gateway.mcp_server")
    assert set(mcp._INPROCESS) == _INPROCESS_METHODS


def test_policy_file_methods_match_tier_table():
    """The shipped policy file enumerates methods with their tier; it must match
    METHOD_TIERS exactly. This catches the policy file drifting behind new tools
    (a stale file silently omits new methods, hiding them from `enabled` controls)."""
    gate = _load_gate()
    policy_path = SRC.parent / "policy" / "library-gateway-policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    file_methods = policy["methods"]
    assert set(file_methods) == set(gate.METHOD_TIERS), {
        "in_tiers_not_in_file": sorted(set(gate.METHOD_TIERS) - set(file_methods)),
        "in_file_not_in_tiers": sorted(set(file_methods) - set(gate.METHOD_TIERS)),
    }
    for name, tier in gate.METHOD_TIERS.items():
        assert file_methods[name]["tier"] == tier, name


def test_write_executables_only_appear_in_write_dispatch(tmp_path, monkeypatch):
    dispatch = _load_dispatch()
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(tmp_path))
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.setenv("RTIME_REMINDERS_PATH", str(tmp_path / "reminders.jsonl"))

    # Build every read target and assert none invoke a deploy/bin write tool.
    read_args = {
        "lib.search": {"index": "idx.sqlite", "query": "q"},
        "lib.courses": {"index": "idx.sqlite", "code": "MATH1009"},
        "lib.get": {"index": "idx.sqlite"},
        "lib.read": {"path": "knowledge/a.md"},
        "lib.tree": {"path": "knowledge"},
        "lib.stat": {"path": "knowledge/a.md"},
        "lib.recent": {},
        "lib.freshness": {},
        "lib.list": {"op": "scan"},
        "lib.meta": {},
        "lib.docpack": {"op": "doctor"},
        "lib.citation": {"op": "doctor"},
        "lib.hub": {"op": "doctor"},
        "lib.context": {"op": "doctor"},
        "lib.profile": {"op": "doctor"},
        "lib.review": {"op": "doctor"},
        "lib.automation": {"op": "doctor"},
        "lib.runtime": {"op": "doctor"},
        "lib.jobs.get": {"id": "job-x"},
        "lib.jobs.list": {},
    }
    for method, builder in dispatch.READ_DISPATCH.items():
        target = builder(read_args[method])
        joined = " ".join(target.argv)
        for exe in dispatch.WRITE_EXECUTABLES:
            assert exe not in joined
        # read targets carry a package for PYTHONPATH; writes do not
        assert target.package is not None

    # Each write target uses exactly one of the three deploy/bin executables.
    write_args = {
        "lib.settings.context_source_list": {},
        "lib.settings.context_source_check": {},
        "lib.settings.context_source_add": {
            "id": "x",
            "kind": "note",
            "title": "t",
            "source_path": "knowledge/a.md",
        },
        "lib.settings.context_source_deactivate": {"id": "x"},
        "lib.settings.memory_candidate_add": {"claim": "c"},
        "lib.settings.reminder_register": {"due": "2026-06-20T09:00:00+08:00", "message": "m"},
        "lib.settings.reminder_list": {},
        "lib.settings.reminder_cancel": {"id": "x"},
        "lib.contribute": {"title": "t", "text": "x"},
        "lib.finalize": {"op": "plan", "inbox": "_inbox/agent/x.md", "dest": "knowledge/notes/x"},
        "lib.course-intake": {"op": "plan", "src": "_inbox/drop/optics", "course_id": "optics", "course_title": "光学"},
        "lib.jobs.submit": {"type": "echo", "params": {"k": "v"}},
    }
    for method, builder in dispatch.WRITE_DISPATCH.items():
        target = builder(write_args[method])
        joined = " ".join(target.argv)
        assert any(exe in joined for exe in dispatch.WRITE_EXECUTABLES)
        assert target.package is None


def test_contribute_keeps_body_out_of_argv():
    dispatch = _load_dispatch()
    target = dispatch.WRITE_DISPATCH["lib.contribute"](
        {"op": "stage", "title": "TITLE", "text": "SECRET BODY TEXT", "note": "n"}
    )
    joined = " ".join(target.argv)
    # neither the body nor the title appears in argv — both travel on stdin
    assert "SECRET BODY TEXT" not in joined
    assert "TITLE" not in joined
    assert "--json-stdin" in target.argv
    assert "rtime-contribute" in joined
    assert target.package is None  # write target carries no PYTHONPATH
    assert target.stdin is not None
    payload = json.loads(target.stdin)
    assert payload["title"] == "TITLE"
    assert payload["text"] == "SECRET BODY TEXT"


def test_memory_candidate_add_keeps_claim_out_of_argv():
    dispatch = _load_dispatch()
    target = dispatch.WRITE_DISPATCH["lib.settings.memory_candidate_add"](
        {"claim": "SUPER SECRET CLAIM", "scope": "study"}
    )
    joined = " ".join(target.argv)
    assert "SUPER SECRET CLAIM" not in joined
    assert "--json-stdin" in target.argv
    # claim travels via stdin, and entry is forced to library-gateway
    assert target.stdin is not None
    assert "SUPER SECRET CLAIM" in target.stdin
    import json

    payload = json.loads(target.stdin)
    assert payload["claim"] == "SUPER SECRET CLAIM"
    assert payload["entry"] == "library-gateway"


def test_jobs_submit_keeps_params_out_of_argv():
    dispatch = _load_dispatch()
    target = dispatch.WRITE_DISPATCH["lib.jobs.submit"](
        {"type": "course-intake-apply", "params": {"plan_sha": "SECRET_SHA"}}
    )
    joined = " ".join(target.argv)
    # the params (which may carry a sensitive token/path) travel on stdin
    assert "SECRET_SHA" not in joined
    assert "--params-stdin" in target.argv
    assert "rtime-jobs-submit" in joined
    assert "--type" in target.argv and "course-intake-apply" in target.argv
    # write target carries no PYTHONPATH (goes to a deploy/bin narrow tool)
    assert target.package is None
    assert target.stdin is not None
    import json

    assert json.loads(target.stdin) == {"plan_sha": "SECRET_SHA"}


def test_jobs_submit_rejects_non_object_params():
    dispatch = _load_dispatch()
    import pytest

    with pytest.raises(dispatch.GateError):
        dispatch.WRITE_DISPATCH["lib.jobs.submit"]({"type": "echo", "params": "not-an-object"})


def test_contacts_op_forces_redaction():
    dispatch = _load_dispatch()
    target = dispatch.READ_DISPATCH["lib.hub"]({"op": "contacts"})
    assert target.redact_force is True
    other = dispatch.READ_DISPATCH["lib.hub"]({"op": "panel"})
    assert other.redact_force is False


def test_search_defaults_index_from_env(tmp_path, monkeypatch):
    # lib.search may omit `index`; it then resolves from BRAIN_LIBRARY_INDEX so a
    # caller need not know the machine-local cache path.
    dispatch = _load_dispatch()
    idx = tmp_path / "state" / "brain-library" / "brain-library.sqlite"
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))

    target = dispatch.READ_DISPATCH["lib.search"]({"query": "q"})
    assert str(idx) in target.argv
    assert target.package == "brain-library"


def test_run_cli_wraps_non_json_text_output():
    # A backend that prints a human report (exit 0) must be WRAPPED, not raised.
    dispatch = _load_dispatch()
    target = dispatch.Target(argv=[sys.executable, "-c", "print('audit report: 3 docpacks ok')"])
    parsed, rc, _raw = dispatch.run_cli(target)
    assert rc == 0
    assert parsed["ok"] is True
    assert parsed["non_json_output"] is True
    assert "audit report" in parsed["raw_output"]


def test_run_cli_non_json_nonzero_is_not_ok():
    dispatch = _load_dispatch()
    target = dispatch.Target(argv=[sys.executable, "-c", "import sys; print('boom'); sys.exit(3)"])
    parsed, rc, _raw = dispatch.run_cli(target)
    assert rc == 3
    assert parsed["ok"] is False
    assert parsed["non_json_output"] is True


def test_run_cli_passes_through_json_object():
    dispatch = _load_dispatch()
    target = dispatch.Target(argv=[sys.executable, "-c", "print('{\"ok\": true, \"n\": 5}')"])
    parsed, _rc, _raw = dispatch.run_cli(target)
    assert parsed == {"ok": True, "n": 5}


def test_run_cli_wraps_bare_json_value():
    dispatch = _load_dispatch()
    target = dispatch.Target(argv=[sys.executable, "-c", "print('[1, 2, 3]')"])
    parsed, _rc, _raw = dispatch.run_cli(target)
    assert parsed["ok"] is True
    assert parsed["value"] == [1, 2, 3]

    # An explicit index still wins over the env default.
    explicit = dispatch.READ_DISPATCH["lib.search"]({"index": "custom.sqlite", "query": "q"})
    assert "custom.sqlite" in explicit.argv


def test_build_stat_honors_index_override(tmp_path, monkeypatch):
    # Regression: _build_stat used to hardcode default_index(), ignoring a caller
    # override. A caller-supplied index must win; omitting it falls back to default.
    dispatch = _load_dispatch()
    idx = tmp_path / "state" / "brain-library" / "brain-library.sqlite"
    monkeypatch.setenv("BRAIN_LIBRARY_INDEX", str(idx))

    explicit = dispatch.READ_DISPATCH["lib.stat"]({"path": "knowledge/a.md", "index": "custom.sqlite"})
    assert "custom.sqlite" in explicit.argv

    default = dispatch.READ_DISPATCH["lib.stat"]({"path": "knowledge/a.md"})
    assert str(idx) in default.argv


def test_env_for_forces_utf8_over_inherited(monkeypatch):
    # The child must be pinned to UTF-8 even if the gateway itself inherited a
    # non-UTF-8 PYTHONIOENCODING (setdefault would have kept the wrong value).
    dispatch = _load_dispatch()
    monkeypatch.setenv("PYTHONIOENCODING", "cp936")
    monkeypatch.setenv("PYTHONUTF8", "0")
    env = dispatch._env_for(dispatch.Target(argv=[sys.executable, "-c", "pass"]))
    assert env["PYTHONIOENCODING"] == "utf-8"
    assert env["PYTHONUTF8"] == "1"


def test_run_cli_flags_decode_replacements():
    # When undecodable bytes were replaced with U+FFFD, surface decode_replacements
    # so a corrupted read is visible; clean output never sets it.
    dispatch = _load_dispatch()
    dirty = dispatch.Target(
        argv=[sys.executable, "-c", "print('{\"ok\": true, \"x\": \"' + chr(0xFFFD) + '\"}')"]
    )
    parsed, _rc, _raw = dispatch.run_cli(dirty)
    assert parsed["ok"] is True
    assert parsed["decode_replacements"] is True

    clean = dispatch.Target(argv=[sys.executable, "-c", "print('{\"ok\": true}')"])
    parsed_clean, _rc2, _raw2 = dispatch.run_cli(clean)
    assert "decode_replacements" not in parsed_clean


# ---------------------------------------------------------------------------
# A3 决策1: 在校学生 PII 网关层 INLINE 脱敏开关 (redact_student_pii)
# ---------------------------------------------------------------------------
def test_pii_inline_redaction_scrubs_tokens_keeps_answer():
    gate = _load_gate()
    # 虚构人物+假学号(格式合法):测试夹具不得含真实个人数据
    text = (
        "张示例:地球和空间科学学院2022级本科生,学号PB00000001,"
        "政治面貌:中共党员,邮箱 zsl-test@mail.ustc.edu.cn,手机13800138000,"
        "身份证34012320020101001X。校学生会主席团成员。"
    )
    # pii 关(且 secret 关): 原样(隔离测 PII 路径,不受敏感行整行替换影响)
    out_off, n_off = gate.redact_output(text, redact=False, pii=False)
    assert out_off == text and n_off == 0
    # pii 开(secret 关): token 抹除,但答案骨架(姓名/学院/任职)保留
    out, n = gate.redact_output(text, redact=False, pii=True)
    assert n >= 5
    assert "PB00000001" not in out
    assert "13800138000" not in out
    assert "34012320020101001X" not in out
    assert "zsl-test@mail.ustc.edu.cn" not in out
    assert "政治面貌：***" in out
    assert "张示例" in out and "校学生会主席团成员" in out  # 姓名/公开任职保留


def test_pii_redaction_independent_of_sensitive_redaction():
    gate = _load_gate()
    text = "学号PB00000001 token=deadbeef"
    # 只开 pii(secret 关): 抹学号但不整行 nuke token 行(secret 关)
    out, n = gate.redact_output(text, redact=False, pii=True)
    assert "PB00000001" not in out and n >= 1
    # 只开 secret(pii 关): token 行整行 redacted,学号原样
    out2, n2 = gate.redact_output(text, redact=True, pii=False)
    assert gate.REDACTED_LINE in out2


def test_pii_redaction_in_json():
    gate = _load_gate()
    data = {"results": [{"snippet": "学号SA23001234的同学", "title": "公示"}]}
    off, n0 = gate.redact_json(data, redact=False, pii=False)
    assert off == data and n0 == 0
    on, n1 = gate.redact_json(data, redact=False, pii=True)
    assert "SA23001234" not in on["results"][0]["snippet"] and n1 >= 1
    assert on["results"][0]["title"] == "公示"


def test_pii_default_off_in_policy():
    gate = _load_gate()
    assert gate._DEFAULT_POLICY.get("redact_student_pii") is False
