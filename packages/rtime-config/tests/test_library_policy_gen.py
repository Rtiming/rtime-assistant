# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""library.scope -> gateway policy JSON generation (design §2.7 single-source).

``library.scope`` is the ONE source of truth; ``build_library_policy`` compiles it
into the gateway policy (``allowed_path_prefixes`` + the read-only method allow/deny
+ personal-data exclusion). These tests pin:

  - scope becomes ``allowed_path_prefixes`` verbatim;
  - personal-data / profile are always excluded (a public consumer never sees them);
  - an empty scope is a hard error (would deny every read);
  - the rendered JSON is byte-stable (sorted keys) for a doctor cross-check;
  - the COMMITTED ``profiles/studentunion/library-policy.json`` equals the generated
    output for that profile's scope (golden: the file cannot silently drift from the
    profile), and matches the hand-written studentunion gateway policy's semantics.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rtime_config.profile import build_library_policy, render_library_policy_json

_REPO = Path(__file__).resolve().parents[3]


def test_scope_becomes_allowed_path_prefixes():
    policy = build_library_policy(["knowledge/institutions/ustc"])
    assert policy["allowed_path_prefixes"] == ["knowledge/institutions/ustc"]
    assert policy["default_read"] == "allow"
    assert policy["default_write"] == "deny"


def test_personal_data_always_excluded():
    policy = build_library_policy(["knowledge/x"])
    assert policy["excluded_top_dirs"] == ["personal-data", "profile"]


def test_read_only_method_lists():
    policy = build_library_policy(["knowledge/x"])
    client = policy["clients"]["default"]
    assert "lib.search" in client["allow"] and "lib.read" in client["allow"]
    # writes denied by the client deny globs (belt: also default_write=deny).
    assert "lib.settings.*" in client["deny"]
    assert "lib.contribute" in client["deny"]
    assert "lib.finalize" in client["deny"]
    # no write method leaked into allow.
    assert not any(
        m.startswith(("lib.settings", "lib.contribute")) for m in client["allow"]
    )


def test_redact_flags_flow_through():
    on = build_library_policy(
        ["knowledge/x"], redact_sensitive=True, hide_excluded_in_results=True
    )
    off = build_library_policy(
        ["knowledge/x"], redact_sensitive=False, hide_excluded_in_results=False
    )
    assert on["redact_sensitive"] is True and on["hide_excluded_in_results"] is True
    assert off["redact_sensitive"] is False and off["hide_excluded_in_results"] is False


def test_empty_scope_is_error():
    with pytest.raises(ValueError):
        build_library_policy([])


def test_render_is_byte_stable():
    p = build_library_policy(["knowledge/institutions/ustc"])
    a = render_library_policy_json(p)
    b = render_library_policy_json(p)
    assert a == b
    assert a.endswith("\n")
    assert json.loads(a) == p  # round-trips


def test_committed_studentunion_policy_matches_profile_scope():
    """Golden: the committed library-policy.json == generated from the profile scope.

    Guards the single-source invariant — if someone edits the profile scope without
    regenerating (or hand-edits the policy), this fails.
    """
    prof_dir = _REPO / "profiles" / "studentunion"
    committed = (prof_dir / "library-policy.json").read_text(encoding="utf-8")
    # the studentunion profile.yaml declares scope = [knowledge/institutions/ustc]
    # with redact_sensitive + hide_excluded_in_results = true.
    expected = render_library_policy_json(
        build_library_policy(
            ["knowledge/institutions/ustc"],
            redact_sensitive=True,
            hide_excluded_in_results=True,
        )
    )
    assert committed == expected


def test_matches_hand_written_gateway_policy_semantics():
    """The generated policy matches the live studentunion gateway policy's semantics.

    The hand-written packages/rtime-library-gateway/policy/studentunion-policy.json is
    the deploy-time truth for the 8781 process; the generator must reproduce its
    allow/deny + scope + exclusion so the two never disagree.
    """
    live_path = (
        _REPO
        / "packages"
        / "rtime-library-gateway"
        / "policy"
        / "studentunion-policy.json"
    )
    if not live_path.is_file():
        pytest.skip("live gateway policy not present")
    live = json.loads(live_path.read_text(encoding="utf-8"))
    gen = build_library_policy(
        live["allowed_path_prefixes"],
        redact_sensitive=live["redact_sensitive"],
        hide_excluded_in_results=live["hide_excluded_in_results"],
        audit_log=live["audit_log"],
    )
    for key in (
        "allowed_path_prefixes",
        "excluded_top_dirs",
        "default_read",
        "default_write",
        "redact_sensitive",
        "hide_excluded_in_results",
        "audit_log",
    ):
        assert gen[key] == live[key], key
    assert gen["clients"]["default"]["allow"] == live["clients"]["default"]["allow"]
    assert gen["clients"]["default"]["deny"] == live["clients"]["default"]["deny"]
