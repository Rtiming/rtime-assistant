# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Audit: entry structure, injected ts, secret hashing, apply/rollback hooks."""

from __future__ import annotations

import json

from rtime_admin_core import (
    AuditEntry,
    ConfigStore,
    InMemoryHistory,
    JsonlAuditSink,
    MemoryBackend,
    ValidationError,
    default_registry,
)


def test_apply_emits_one_audit_entry(store, sink):
    store.apply(
        {"models.default_model": "ds"},
        ts="TS",
        snapshot_id="s1",
        actor="op",
        source="cli",
    )
    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.ts == "TS"  # injected, not time.now()
    assert e.actor == "op"
    assert e.source == "cli"
    assert e.action == "apply"
    assert e.outcome == "ok"
    assert e.snapshot_id == "s1"
    assert e.paths == ["models.default_model"]


def test_audit_entry_full_field_set(store, sink):
    store.apply({"models.default_model": "ds"}, ts="TS", snapshot_id="s1")
    d = sink.entries[0].to_dict()
    assert set(d.keys()) == {
        "ts",
        "actor",
        "source",
        "action",
        "outcome",
        "paths",
        "diff",
        "snapshot_id",
        "detail",
    }


def test_audit_diff_hashes_secret(store, sink):
    store.apply({"models.ustc_api_key": "sk-super-secret"}, ts="TS", snapshot_id="s1")
    e = sink.entries[0]
    blob = json.dumps(e.to_dict())
    assert "sk-super-secret" not in blob
    # store-produced diffs use a SALTED keyed digest (hmac:), never a bare sha256
    # prefix an attacker could enumerate offline (defect #9).
    assert e.diff["models.ustc_api_key"]["after"].startswith("hmac:")


def test_failed_apply_audited_as_error(store, sink):
    try:
        store.apply({"library-gateway.http_port": 999999}, ts="TS", snapshot_id="s1")
    except ValidationError:
        pass
    assert len(sink.entries) == 1
    e = sink.entries[0]
    assert e.outcome == "error"
    assert e.snapshot_id is None  # no snapshot for a failed change
    assert e.detail  # carries the reason


def test_rollback_writes_audit(store, sink):
    store.apply({"models.default_model": "ds"}, ts="t1", snapshot_id="A")
    store.rollback("A", ts="t2", new_snapshot_id="B")
    actions = [e.action for e in sink.entries]
    assert actions == ["apply", "rollback"]
    assert sink.entries[-1].outcome == "ok"


def test_no_hook_is_fine():
    # a store without an audit hook must not blow up on apply
    store = ConfigStore(default_registry(), MemoryBackend(), InMemoryHistory(), env={})
    res = store.apply({"models.default_model": "ds"}, ts="t", snapshot_id="s")
    assert res.changed == ["models.default_model"]


def test_jsonl_sink_roundtrip(tmp_path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    sink(AuditEntry(ts="t1", actor="a", source="cli", action="apply", outcome="ok"))
    sink(AuditEntry(ts="t2", actor="b", source="http", action="rollback", outcome="ok"))
    rows = sink.read_all()
    assert len(rows) == 2
    assert rows[0]["ts"] == "t1" and rows[1]["action"] == "rollback"
    # append-only: two physical lines
    assert path.read_text(encoding="utf-8").strip().count("\n") == 1


def test_jsonl_entry_is_sorted_keys(tmp_path):
    path = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(path)
    sink(AuditEntry(ts="t", actor="a", source="cli", action="apply", outcome="ok"))
    line = path.read_text(encoding="utf-8").strip()
    # deterministic key order (sorted) -> diff-friendly
    assert line.index('"action"') < line.index('"actor"') < line.index('"ts"')
