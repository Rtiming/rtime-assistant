# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Change transactions: apply, reload classification, snapshot, history, rollback."""

from __future__ import annotations

import pytest
from rtime_admin_core import (
    ConfigStore,
    InMemoryHistory,
    MemoryBackend,
    SnapshotNotFoundError,
    default_registry,
)


def test_apply_classifies_hot_vs_restart(store):
    res = store.apply(
        {
            "models.default_model": "ds",  # hot
            "library-gateway.http_port": 9001,  # restart
        },
        ts="t",
        snapshot_id="s1",
    )
    assert res.hot == ["models.default_model"]
    assert res.restart_required == ["library-gateway.http_port"]
    assert res.needs_restart is True
    assert res.changed == ["library-gateway.http_port", "models.default_model"]


def test_apply_hot_only_no_restart(store):
    res = store.apply({"channel-common.max_turns": 5}, ts="t", snapshot_id="s1")
    assert res.hot == ["channel-common.max_turns"]
    assert res.restart_required == []
    assert res.needs_restart is False


def test_apply_empty_changes_rejected(store):
    with pytest.raises(ValueError):
        store.apply({}, ts="t", snapshot_id="s")


def test_apply_snapshots_pre_state(store):
    # change once; snap-1 should capture the PRE-change (empty) state
    store.apply({"models.default_model": "v1"}, ts="t1", snapshot_id="snap-1")
    store.apply({"models.default_model": "v2"}, ts="t2", snapshot_id="snap-2")
    hist = store.list_history()
    assert [h["id"] for h in hist] == ["snap-2", "snap-1"]  # newest first


def test_snapshot_explicit(store):
    sid = store.snapshot("manual-1", ts="t", note="checkpoint")
    assert sid == "manual-1"
    assert store.list_history()[0]["note"] == "checkpoint"


def test_history_cap_evicts_oldest():
    store = ConfigStore(
        default_registry(),
        MemoryBackend(),
        InMemoryHistory(),
        max_history=2,
        env={},
    )
    for i in range(4):
        store.apply({"models.default_model": f"m{i}"}, ts=f"t{i}", snapshot_id=f"h{i}")
    ids = [h["id"] for h in store.list_history()]
    assert ids == ["h3", "h2"]  # only the two newest survive


def test_rollback_restores_snapshot_state(store):
    store.apply({"channel-common.read_only": True}, ts="t1", snapshot_id="A")
    # A captured the pre-state where read_only == default (False)
    store.apply({"channel-common.max_turns": 9}, ts="t2", snapshot_id="B")
    rb = store.rollback("A", ts="t3", new_snapshot_id="C")
    # rolling back to A restores read_only=False (and undoes max_turns=9 too,
    # since A predates both changes)
    assert store.get("channel-common.read_only") is False
    assert store.get("channel-common.max_turns") == 0
    assert "channel-common.read_only" in rb.changed


def test_rollback_is_itself_reversible(store):
    store.apply({"models.default_model": "ds"}, ts="t1", snapshot_id="A")
    store.rollback("A", ts="t2", new_snapshot_id="C")  # back to pre-A (claude)
    assert store.get("models.default_model") == "claude"
    # C snapshotted the state right before rollback (default_model == ds)
    store.rollback("C", ts="t3", new_snapshot_id="D")
    assert store.get("models.default_model") == "ds"


def test_rollback_unknown_snapshot_raises(store):
    with pytest.raises(SnapshotNotFoundError):
        store.rollback("ghost", ts="t", new_snapshot_id="x")


def test_apply_persists_secret_to_secret_store(registry):
    backend = MemoryBackend()
    store = ConfigStore(registry, backend, InMemoryHistory(), env={})
    store.apply(
        {"models.ustc_api_key": "sk-1", "models.default_model": "ds"},
        ts="t",
        snapshot_id="s",
    )
    # secret lands only in the secret store; non-secret only in config
    assert backend.load_secrets() == {"models": {"ustc_api_key": "sk-1"}}
    assert backend.load_config() == {"models": {"default_model": "ds"}}


def test_reload_classification_uses_x_reload(store):
    # idle_timeout is hot, http_port is restart (both in library-gateway)
    res = store.apply(
        {"library-gateway.idle_timeout": 900, "library-gateway.http_port": 9002},
        ts="t",
        snapshot_id="s",
    )
    assert res.hot == ["library-gateway.idle_timeout"]
    assert res.restart_required == ["library-gateway.http_port"]
