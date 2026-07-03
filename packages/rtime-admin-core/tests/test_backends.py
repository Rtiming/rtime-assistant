# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Storage backends: file separation, 0600 secrets, atomic write, persistence."""

from __future__ import annotations

import json
import os
import stat

from rtime_admin_core import (
    ConfigStore,
    FileBackend,
    FileHistory,
    JsonlAuditSink,
    MemoryBackend,
    default_registry,
)


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def test_file_backend_separates_secret_from_config(tmp_path):
    cfg = tmp_path / "config.json"
    sec = tmp_path / "secrets.json"
    store = ConfigStore(
        default_registry(),
        FileBackend(cfg, sec),
        FileHistory(tmp_path / "hist"),
        env={},
    )
    store.apply(
        {"models.default_model": "ds", "models.ustc_api_key": "sk-secret"},
        ts="t",
        snapshot_id="s",
    )
    config_data = json.loads(cfg.read_text())
    secret_data = json.loads(sec.read_text())
    # secret never in the config file
    assert "sk-secret" not in cfg.read_text()
    assert config_data == {"models": {"default_model": "ds"}}
    assert secret_data == {"models": {"ustc_api_key": "sk-secret"}}


def test_secret_file_is_0600(tmp_path):
    sec = tmp_path / "secrets.json"
    store = ConfigStore(
        default_registry(),
        FileBackend(tmp_path / "config.json", sec),
        FileHistory(tmp_path / "hist"),
        env={},
    )
    store.apply({"models.ustc_api_key": "sk-1"}, ts="t", snapshot_id="s")
    assert _mode(sec) == 0o600


def test_snapshot_files_are_0600(tmp_path):
    hist_dir = tmp_path / "hist"
    store = ConfigStore(
        default_registry(),
        FileBackend(tmp_path / "config.json", tmp_path / "secrets.json"),
        FileHistory(hist_dir),
        env={},
    )
    store.apply(
        {"models.default_model": "ds"}, ts="2026-07-02T00:00:00Z", snapshot_id="s"
    )
    files = list(hist_dir.glob("*.json"))
    assert files and all(_mode(f) == 0o600 for f in files)


def test_persistence_across_store_instances(tmp_path):
    cfg = tmp_path / "config.json"
    sec = tmp_path / "secrets.json"
    hist = tmp_path / "hist"
    reg = default_registry()
    ConfigStore(reg, FileBackend(cfg, sec), FileHistory(hist), env={}).apply(
        {"models.default_model": "ds", "models.ustc_api_key": "sk-1"},
        ts="t",
        snapshot_id="s",
    )
    # fresh store on the same files
    store2 = ConfigStore(reg, FileBackend(cfg, sec), FileHistory(hist), env={})
    assert store2.get("models.default_model") == "ds"
    assert store2.get("models.ustc_api_key") == "sk-1"
    assert [h["id"] for h in store2.list_history()] == ["s"]


def test_missing_files_read_as_empty(tmp_path):
    be = FileBackend(tmp_path / "no-config.json", tmp_path / "no-secrets.json")
    assert be.load_config() == {}
    assert be.load_secrets() == {}


def test_file_history_rollback_roundtrip(tmp_path):
    reg = default_registry()
    be = FileBackend(tmp_path / "config.json", tmp_path / "secrets.json")
    hist = FileHistory(tmp_path / "hist")
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    store = ConfigStore(reg, be, hist, audit_hook=sink, env={})
    store.apply(
        {"models.default_model": "v1"}, ts="2026-07-02T01:00:00Z", snapshot_id="A"
    )
    store.apply(
        {"models.default_model": "v2"}, ts="2026-07-02T02:00:00Z", snapshot_id="B"
    )
    # B captured the state where default_model == v1
    store.rollback("B", ts="2026-07-02T03:00:00Z", new_snapshot_id="C")
    assert store.get("models.default_model") == "v1"


def test_memory_backend_isolates_mutations():
    be = MemoryBackend(config={"models": {"default_model": "x"}})
    loaded = be.load_config()
    loaded["models"]["default_model"] = "mutated"
    # backend's internal copy is unaffected by mutating a load result
    assert be.load_config()["models"]["default_model"] == "x"
