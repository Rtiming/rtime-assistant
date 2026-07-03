# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J6 库共享 grant 一等对象 + 由 grant 生成网关 policy(config-and-access §五)。

关键:studentunion grant 生成的 policy 与手写 studentunion-policy.json 的安全相关字段
**等价**——证明 grant 能无缝替代手写 policy(阶段0 迁入不变行为)。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rtime_library_gateway.grants import (  # noqa: E402
    STATUS_REVOKED,
    Grant,
    GrantScope,
    dump_ledger,
    grant_to_policy,
    load_ledger,
    owner_audit_view,
)

STU = Grant(
    grant_id="studentunion",
    subject="studentunion",
    scopes=(GrantScope(prefix="knowledge/institutions/ustc", read=True, contribute=False),),
    granted_by="owner",
    granted_at="2026-07-02T00:00:00Z",
    expires_at=None,
)


def test_grant_to_policy_matches_handwritten_studentunion():
    live_path = ROOT / "packages" / "rtime-library-gateway" / "policy" / "studentunion-policy.json"
    live = json.loads(live_path.read_text(encoding="utf-8"))
    gen = grant_to_policy(STU)
    # 安全相关字段逐条等价
    assert gen["allowed_path_prefixes"] == live["allowed_path_prefixes"]
    assert gen["default_read"] == live["default_read"]
    assert gen["default_write"] == live["default_write"]
    assert gen["excluded_top_dirs"] == live["excluded_top_dirs"]
    assert gen["redact_sensitive"] == live["redact_sensitive"]
    assert gen["redact_student_pii"] == live["redact_student_pii"]
    assert gen["hide_excluded_in_results"] == live["hide_excluded_in_results"]
    # allow 精确等(同一组只读方法);deny 至少一样严(live_deny ⊆ gen_deny,
    # gen 额外显式 deny lib.annotate=更严,annotate 早于手写 policy 也已被白名单+
    # gate.SCOPE_DENIED_WRITE_METHODS 双重拦截)。
    assert set(gen["clients"]["default"]["allow"]) == set(live["clients"]["default"]["allow"])
    assert set(live["clients"]["default"]["deny"]) <= set(gen["clients"]["default"]["deny"])


def test_read_only_grant_has_no_contribute_in_allow():
    pol = grant_to_policy(STU)
    assert "lib.contribute" not in pol["clients"]["default"]["allow"]
    assert pol["default_write"] == "deny"
    # 直接写方法恒 deny
    assert "lib.annotate" in pol["clients"]["default"]["deny"]


def test_contribute_grant_adds_contribute_method_only():
    g = Grant(
        grant_id="g2",
        subject="stu",
        scopes=(GrantScope(prefix="knowledge/institutions/ustc", read=True, contribute=True),),
    )
    pol = grant_to_policy(g)
    assert "lib.contribute" in pol["clients"]["default"]["allow"]
    # 仍不给直接写:finalize/annotate 仍 deny(写=投稿+审核)
    assert pol["default_write"] == "deny"
    assert "lib.finalize" in pol["clients"]["default"]["deny"]
    assert "lib.annotate" in pol["clients"]["default"]["deny"]


def test_read_write_prefixes_orthogonal():
    g = Grant(
        grant_id="g3",
        subject="x",
        scopes=(
            GrantScope(prefix="knowledge/a", read=True, contribute=False),
            GrantScope(prefix="knowledge/b", read=True, contribute=True),
        ),
    )
    assert g.read_prefixes() == ["knowledge/a", "knowledge/b"]
    assert g.contribute_prefixes() == ["knowledge/b"]  # 只 b 能投稿


def test_is_active_status_and_expiry():
    now = "2026-07-04T00:00:00Z"
    assert STU.is_active(now) is True  # 无限期 active
    revoked = Grant("g", "s", (), status=STATUS_REVOKED)
    assert revoked.is_active(now) is False
    expired = Grant("g", "s", (), expires_at="2026-07-01T00:00:00Z")
    assert expired.is_active(now) is False  # 已过期
    future = Grant("g", "s", (), expires_at="2026-12-31T00:00:00Z")
    assert future.is_active(now) is True


def test_ledger_roundtrip_and_bad_lines():
    text = dump_ledger([STU])
    back = load_ledger(text)
    assert len(back) == 1 and back[0].grant_id == "studentunion"
    assert back[0].to_dict() == STU.to_dict()  # 往返一致
    # 坏行跳过
    assert load_ledger("not json\n" + text + "{}\n") == load_ledger(text)


def test_owner_audit_view():
    now = "2026-07-04T00:00:00Z"
    view = owner_audit_view([STU, Grant("g2", "other", (), status=STATUS_REVOKED)], now)
    assert view[0]["grant_id"] == "studentunion" and view[0]["active"] is True
    assert view[1]["active"] is False
    # 无正文,只有元数据
    assert set(view[0]) == {
        "grant_id", "subject", "read_prefixes", "contribute_prefixes",
        "status", "expires_at", "active",
    }


def test_generated_policy_enforced_by_gate(tmp_path, monkeypatch):
    """严格验证:studentunion grant → 生成 policy → gate 真的按它 enforce
    (in-scope 读放行、越界读拒、直接写拒)。证明 grant 能替代手写 policy 且行为一致。"""
    import json as _json

    from rtime_library_gateway import gate as gate_mod

    pol = grant_to_policy(STU)
    pol_file = tmp_path / "gen-policy.json"
    pol_file.write_text(_json.dumps(pol), encoding="utf-8")
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(pol_file))
    brain = tmp_path / "brain"
    (brain / "knowledge" / "institutions" / "ustc").mkdir(parents=True)
    (brain / "knowledge" / "other").mkdir(parents=True)

    policy = gate_mod.load_policy()
    assert policy["_generated_from_grant"] == "studentunion"
    # in-scope 读放行
    gate_mod.enforce(
        "lib.read", {"path": "knowledge/institutions/ustc/x.md"}, "default",
        policy=policy, brain_root=brain,
    )
    # 越界读拒
    import pytest as _pytest
    with _pytest.raises(gate_mod.GateError):
        gate_mod.enforce(
            "lib.read", {"path": "knowledge/other/leak.md"}, "default",
            policy=policy, brain_root=brain,
        )
    # 直接写(annotate)拒
    with _pytest.raises(gate_mod.GateError):
        gate_mod.enforce(
            "lib.annotate", {"op": "apply", "path": "knowledge/institutions/ustc/x.md", "changes": {}},
            "default", policy=policy, brain_root=brain,
        )
