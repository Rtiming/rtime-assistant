# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Core seams added for the L2 API (persisted_flat / rollback_changed_paths)
and the file-mode hardening (audit 0600, history dir 0700).

These are the L1 primitives the L2 fixes for defects #2/#6/#10/#11 rely on, so
they carry their own core-level regression coverage independent of the HTTP
layer.
"""

from __future__ import annotations

import stat

import pytest
from pydantic_settings import SettingsConfigDict
from rtime_admin_core import (
    AuditEntry,
    ConfigStore,
    FileHistory,
    InMemoryHistory,
    JsonlAuditSink,
    MemoryBackend,
    Snapshot,
    SnapshotNotFoundError,
    default_registry,
)
from rtime_config import RtimeBaseSettings, config_field
from rtime_config.fields import Reload


class _Pinned(RtimeBaseSettings):
    model_config = SettingsConfigDict(env_prefix="RTIME_PIN2_")

    pinned: str = config_field(
        "default",
        description="env-pinned",
        reload=Reload.HOT,
        env_aliases=["RTIME_PIN2_PINNED"],
    )


def _store(env=None):
    reg = default_registry()
    reg.register("pin2", _Pinned)
    return ConfigStore(
        reg,
        MemoryBackend(),
        InMemoryHistory(),
        env=env if env is not None else {},
        secret_salt="seam-salt",
    )


# --- persisted_flat (defect #6 seam) -------------------------------------------
def test_persisted_flat_reflects_persisted_writes_not_env():
    """persisted_flat shows the persisted layer; an env override does not appear,
    but a persisted write to an env-pinned field DOES — so it moves on any write
    even when the resolved get() stays env-pinned."""
    store = _store(env={"RTIME_PIN2_PINNED": "ENVWINS"})
    assert store.get("pin2.pinned") == "ENVWINS"  # resolved = env
    assert "pin2.pinned" not in store.persisted_flat()  # nothing persisted yet

    store.apply({"pin2.pinned": "written"}, ts="t", snapshot_id="s1")
    # resolved still env-pinned ...
    assert store.get("pin2.pinned") == "ENVWINS"
    # ... but persisted_flat records the real write
    assert store.persisted_flat()["pin2.pinned"] == "written"


def test_persisted_flat_excludes_unset_defaults():
    store = _store()
    assert store.persisted_flat() == {}  # defaults are not persisted


# --- rollback_changed_paths (defect #2 seam) -----------------------------------
def test_rollback_changed_paths_previews_without_writing():
    store = _store()
    r1 = store.apply({"models.default_model": "kimi"}, ts="t1", snapshot_id="s1")
    r2 = store.apply({"models.default_model": "deepseek"}, ts="t2", snapshot_id="s2")
    # rolling back to s2's snapshot (state {kimi}) would change default_model
    paths = store.rollback_changed_paths(r2.snapshot_id)
    assert paths == ["models.default_model"]
    # pure preview: no write, no new snapshot, current value unchanged
    assert store.get("models.default_model") == "deepseek"
    assert [s["id"] for s in store.list_history()] == ["s2", "s1"]
    # r1 unused for value assertions
    assert r1.snapshot_id == "s1"


def test_rollback_changed_paths_unknown_snapshot_raises():
    store = _store()
    with pytest.raises(SnapshotNotFoundError):
        store.rollback_changed_paths("nope")


def test_rollback_changed_paths_matches_actual_rollback_changed():
    """The preview set must equal what rollback() then reports as changed."""
    store = _store()
    store.apply({"models.default_model": "kimi"}, ts="t1", snapshot_id="s1")
    r2 = store.apply(
        {"models.default_model": "deepseek", "channel-common.read_only": True},
        ts="t2",
        snapshot_id="s2",
    )
    preview = store.rollback_changed_paths(r2.snapshot_id)
    result = store.rollback(r2.snapshot_id, ts="t3", new_snapshot_id="s3")
    assert preview == result.changed


# --- audit log 0600 (defect #10) -----------------------------------------------
def test_jsonl_audit_sink_creates_0600(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    sink(AuditEntry(ts="t", actor="a", source="s", action="apply", outcome="ok"))
    assert stat.S_IMODE((tmp_path / "audit.jsonl").stat().st_mode) == 0o600
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_jsonl_audit_sink_tightens_existing_0644(tmp_path):
    p = tmp_path / "audit.jsonl"
    p.write_text("", encoding="utf-8")
    p.chmod(0o644)
    JsonlAuditSink(p)(
        AuditEntry(ts="t", actor="a", source="s", action="apply", outcome="ok")
    )
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_jsonl_audit_sink_appends_not_truncates(tmp_path):
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    for i in range(3):
        sink(
            AuditEntry(ts=f"t{i}", actor="a", source="s", action="apply", outcome="ok")
        )
    assert len(sink.read_all()) == 3


# --- history dir 0700 (defect #11) ---------------------------------------------
def test_file_history_dir_is_0700(tmp_path):
    hist = FileHistory(tmp_path / "history")
    hist.add(Snapshot(id="s1", ts="2026-01-01T00:00:00+00:00", config={}, secrets={}))
    assert stat.S_IMODE((tmp_path / "history").stat().st_mode) == 0o700
    # snapshot files remain 0600
    files = list((tmp_path / "history").glob("*.json"))
    assert files and stat.S_IMODE(files[0].stat().st_mode) == 0o600
