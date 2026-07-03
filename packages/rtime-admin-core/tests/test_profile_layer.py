# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""ConfigStore profile-layer tests (T1 acceptance matrix, design §2.4/§2.10).

Uses the sample modules (channel-common / models / library-gateway) so these
tests never depend on the qq-bridge app being importable — the profile-layer
MECHANICS are module-agnostic. The real qq mapping is golden-tested in
rtime-config's profile tests.

Covered:
  - 4-layer precedence (env > store > profile > default), each layer winning in
    the right order;
  - unset store key falls back to the profile layer;
  - sparse store passthrough: a profile value flows through keys the store never
    touched; a store override survives a profile reload;
  - validate agrees with get (merge-base is store > profile > default);
  - provenance query correct for all 4 layers;
  - atomic reload: an invalid new profile leaves the old layer active (nothing
    half-applied), error surfaced;
  - reload rejects a secret-field key (defence in depth) and unknown paths;
  - reload emits ONE audit entry (action=profile_reload) with hot/restart split;
  - drift_report lists shadowed keys; unset clears them.
"""

from __future__ import annotations

import pytest
from rtime_admin_core import (
    PROV_DEFAULT,
    PROV_ENV,
    PROV_PROFILE,
    PROV_STORE,
    ConfigStore,
    InMemoryAuditSink,
    InMemoryHistory,
    MemoryBackend,
    ValidationError,
    default_registry,
)


def _store(*, profile_layer=None, env=None, config=None, secrets=None):
    reg = default_registry()
    sink = InMemoryAuditSink()
    store = ConfigStore(
        reg,
        MemoryBackend(config=config, secrets=secrets),
        InMemoryHistory(),
        audit_hook=sink,
        max_history=5,
        env=env or {},
        secret_salt="fixed-test-salt",
        profile_layer=profile_layer,
    )
    return store, sink


# --- 4-layer precedence ---------------------------------------------------------


def test_default_when_no_profile_no_store_no_env():
    store, _ = _store()
    # channel-common.permission_mode default is "default"
    assert store.get("channel-common.permission_mode") == "default"
    assert store.provenance("channel-common.permission_mode") == PROV_DEFAULT


def test_profile_beats_default():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    assert store.get("channel-common.permission_mode") == "plan"
    assert store.provenance("channel-common.permission_mode") == PROV_PROFILE


def test_store_beats_profile():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    store.set(
        "channel-common.permission_mode",
        "acceptEdits",
        ts="t1",
        snapshot_id="s1",
    )
    assert store.get("channel-common.permission_mode") == "acceptEdits"
    assert store.provenance("channel-common.permission_mode") == PROV_STORE


def test_env_beats_store_and_profile():
    store, _ = _store(
        profile_layer={"channel-common.permission_mode": "plan"},
        env={"RTIME_CHAT_PERMISSION_MODE": "bypassPermissions"},
    )
    store.set(
        "channel-common.permission_mode",
        "acceptEdits",
        ts="t1",
        snapshot_id="s1",
    )
    assert store.get("channel-common.permission_mode") == "bypassPermissions"
    assert store.provenance("channel-common.permission_mode") == PROV_ENV


def test_all_four_layers_in_one_store():
    # env pins A; store overrides B; profile supplies C; D falls to default.
    store, _ = _store(
        profile_layer={
            "channel-common.permission_mode": "plan",  # A: shadowed by env
            "channel-common.read_only": True,  # C: profile wins
        },
        env={"RTIME_CHAT_PERMISSION_MODE": "bypassPermissions"},  # A
    )
    store.set("channel-common.max_turns", 7, ts="t", snapshot_id="s")  # B
    assert store.get("channel-common.permission_mode") == "bypassPermissions"
    assert store.provenance("channel-common.permission_mode") == PROV_ENV
    assert store.get("channel-common.max_turns") == 7
    assert store.provenance("channel-common.max_turns") == PROV_STORE
    assert store.get("channel-common.read_only") is True
    assert store.provenance("channel-common.read_only") == PROV_PROFILE
    # reply_timeout_seconds: nothing set anywhere -> default
    assert store.provenance("channel-common.reply_timeout_seconds") == PROV_DEFAULT


# --- validate agrees with get ---------------------------------------------------


def test_validate_uses_store_over_profile_over_default():
    # profile supplies an int max_turns; a new edit to a DIFFERENT field must
    # validate against a base that includes the profile value (not the default).
    store, _ = _store(profile_layer={"channel-common.max_turns": 5})
    # editing read_only alone; base for max_turns comes from the profile (5).
    errors = store.validate({"channel-common.read_only": True})
    assert errors == []


def test_get_all_provenance_shape():
    store, _ = _store(profile_layer={"channel-common.read_only": True})
    allv = store.get_all(provenance=True)
    entry = allv["channel-common.read_only"]
    assert entry == {"value": True, "provenance": PROV_PROFILE}


# --- unset (clear-override) -----------------------------------------------------


def test_unset_falls_back_to_profile():
    store, sink = _store(profile_layer={"channel-common.permission_mode": "plan"})
    store.set(
        "channel-common.permission_mode", "acceptEdits", ts="t1", snapshot_id="s1"
    )
    assert store.provenance("channel-common.permission_mode") == PROV_STORE
    res = store.unset("channel-common.permission_mode", ts="t2", snapshot_id="s2")
    # value falls back to the profile layer, not the schema default.
    assert store.get("channel-common.permission_mode") == "plan"
    assert store.provenance("channel-common.permission_mode") == PROV_PROFILE
    assert "channel-common.permission_mode" in res.changed
    actions = [e.action for e in sink.entries]
    assert actions[-1] == "unset"


def test_unset_falls_back_to_default_when_no_profile():
    store, _ = _store()
    store.set("channel-common.permission_mode", "plan", ts="t1", snapshot_id="s1")
    store.unset("channel-common.permission_mode", ts="t2", snapshot_id="s2")
    assert store.get("channel-common.permission_mode") == "default"
    assert store.provenance("channel-common.permission_mode") == PROV_DEFAULT


def test_unset_noop_when_no_override():
    store, sink = _store(profile_layer={"channel-common.permission_mode": "plan"})
    res = store.unset("channel-common.permission_mode", ts="t", snapshot_id="s")
    assert res.changed == []
    assert store.get("channel-common.permission_mode") == "plan"
    assert sink.entries[-1].action == "unset"


# --- sparse passthrough + store survives reload ---------------------------------


def test_profile_update_flows_through_untouched_keys():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    # reload with a NEW profile that changes a key the store never touched.
    store.reload_profile(
        {"channel-common.permission_mode": "acceptEdits"},
        ts="t",
        snapshot_id="s",
    )
    assert store.get("channel-common.permission_mode") == "acceptEdits"
    assert store.provenance("channel-common.permission_mode") == PROV_PROFILE


def test_store_override_survives_profile_reload():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    store.set(
        "channel-common.permission_mode", "acceptEdits", ts="t1", snapshot_id="s1"
    )
    store.reload_profile(
        {"channel-common.permission_mode": "bypassPermissions"},
        ts="t2",
        snapshot_id="s2",
    )
    # the store override still wins after the profile reload.
    assert store.get("channel-common.permission_mode") == "acceptEdits"
    assert store.provenance("channel-common.permission_mode") == PROV_STORE


# --- atomic reload --------------------------------------------------------------


def test_reload_invalid_keeps_old_layer():
    store, sink = _store(profile_layer={"channel-common.max_turns": 3})
    with pytest.raises(ValidationError):
        # max_turns has ge=0; -1 must fail and NOT swap.
        store.reload_profile({"channel-common.max_turns": -1}, ts="t", snapshot_id="s")
    # old layer intact, nothing half-applied.
    assert store.get("channel-common.max_turns") == 3
    assert store.provenance("channel-common.max_turns") == PROV_PROFILE
    assert store.profile_layer == {"channel-common.max_turns": 3}
    assert sink.entries[-1].outcome == "error"


def test_reload_rejects_secret_key():
    store, _ = _store()
    with pytest.raises(ValidationError):
        store.reload_profile(
            {"models.ustc_api_key": "sk-leak"}, ts="t", snapshot_id="s"
        )
    assert store.profile_layer == {}


def test_reload_rejects_unknown_path():
    store, _ = _store()
    with pytest.raises(ValidationError):
        store.reload_profile({"nope.field": 1}, ts="t", snapshot_id="s")


def test_reload_one_audit_entry_with_hot_restart_split():
    store, sink = _store()
    before = len(sink.entries)
    # max_turns is hot; read_only is restart (channel-common). Both change.
    res = store.reload_profile(
        {"channel-common.max_turns": 9, "channel-common.read_only": True},
        ts="t",
        snapshot_id="s",
    )
    assert len(sink.entries) == before + 1
    e = sink.entries[-1]
    assert e.action == "profile_reload" and e.outcome == "ok"
    assert "channel-common.max_turns" in res.hot
    assert "channel-common.read_only" in res.restart_required


def test_reload_diff_records_env_pinned_field_change():
    """Defect #4: an env-pinned field reloaded to a DIFFERENT profile value must
    still show up in changed/diff/restart. get_all() would show the env value on
    both sides (empty diff, no restart signal); the reload must diff the NON-env
    (store>profile>default) view instead — same class as rollback defect #2."""
    # env pins max_turns to 4 (get() returns 4 both before and after the reload).
    store, sink = _store(
        profile_layer={"channel-common.max_turns": 1},
        env={"RTIME_CHAT_MAX_TURNS": "4"},
    )
    assert store.get("channel-common.max_turns") == 4  # env wins for get()
    res = store.reload_profile(
        {"channel-common.max_turns": 9},  # profile 1 -> 9 (real change under env)
        ts="t",
        snapshot_id="s",
    )
    # the profile change is recorded even though env pins the effective get() value.
    assert "channel-common.max_turns" in res.changed
    assert res.diff["channel-common.max_turns"] == {"before": 1, "after": 9}
    # max_turns is hot; the change is classified, not silently dropped.
    assert "channel-common.max_turns" in res.hot
    # a restart-level env-pinned field reloaded to a new value lands in restart.
    store2, _ = _store(
        profile_layer={"channel-common.read_only": False},
        env={"RTIME_CHAT_READ_ONLY": "1"},
    )
    res2 = store2.reload_profile(
        {"channel-common.read_only": True}, ts="t", snapshot_id="s"
    )
    assert "channel-common.read_only" in res2.changed
    assert "channel-common.read_only" in res2.restart_required


# --- drift report ---------------------------------------------------------------


def test_drift_report_lists_shadowed_keys():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    # no store override yet -> no drift
    assert store.drift_report() == []
    store.set("channel-common.permission_mode", "acceptEdits", ts="t", snapshot_id="s")
    drift = store.drift_report()
    assert len(drift) == 1
    d = drift[0]
    assert d["path"] == "channel-common.permission_mode"
    assert d["store"] == "acceptEdits" and d["profile"] == "plan"
    assert d["secret"] is False


def test_drift_cleared_by_unset():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    store.set("channel-common.permission_mode", "acceptEdits", ts="t", snapshot_id="s")
    assert store.drift_report()
    store.unset("channel-common.permission_mode", ts="t2", snapshot_id="s2")
    assert store.drift_report() == []


def test_drift_no_entry_when_store_equals_profile():
    store, _ = _store(profile_layer={"channel-common.permission_mode": "plan"})
    store.set("channel-common.permission_mode", "plan", ts="t", snapshot_id="s")
    # store value equals profile value -> not shadowing (no drift)
    assert store.drift_report() == []


# --- backward compatibility -----------------------------------------------------


def test_no_profile_layer_is_three_layer_behaviour():
    store, _ = _store()  # no profile_layer
    assert store.profile_layer == {}
    assert store.provenance("channel-common.read_only") == PROV_DEFAULT
    store.set("channel-common.read_only", True, ts="t", snapshot_id="s")
    assert store.provenance("channel-common.read_only") == PROV_STORE
