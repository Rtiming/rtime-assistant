# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""File-backed wiring: salt persistence (0600), env contract, e2e on disk."""

from __future__ import annotations

import json
import stat

import pytest
from _helpers import ADMIN_TOKEN, auth, patch_config
from fastapi.testclient import TestClient
from rtime_admin_api import build_store, create_app
from rtime_admin_api.app import compute_etag
from rtime_admin_api.auth import ApiKey
from rtime_admin_api.wiring import (
    app_from_env,
    host_port_from_env,
    load_or_create_salt,
)


def _mode(path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


def test_salt_created_0600_and_reused(tmp_path):
    first = load_or_create_salt(tmp_path)
    salt_file = tmp_path / "salt"
    assert salt_file.is_file()
    assert _mode(salt_file) == 0o600
    assert len(first) == 32  # token_hex(16)
    assert load_or_create_salt(tmp_path) == first  # stable across "restarts"


def test_etag_stable_across_process_restarts(tmp_path):
    """Same store dir -> same salt -> same ETag for the same state."""
    store_a, _ = build_store(tmp_path, env={})
    store_b, _ = build_store(tmp_path, env={})
    assert compute_etag(store_a) == compute_etag(store_b)


def test_build_store_lays_out_files_with_modes(tmp_path):
    store, sink = build_store(tmp_path, env={})
    result = store.apply(
        {"models.ustc_api_key": "sk-on-disk-000", "models.default_model": "kimi"},
        ts="2026-07-02T00:00:00+00:00",
        snapshot_id="snap-layout",
        actor="test",
        source="test",
    )
    assert result.snapshot_id == "snap-layout"
    assert _mode(tmp_path / "secrets.json") == 0o600
    secrets_doc = json.loads((tmp_path / "secrets.json").read_text())
    assert secrets_doc["models"]["ustc_api_key"] == "sk-on-disk-000"
    config_doc = json.loads((tmp_path / "config.json").read_text())
    assert config_doc["models"]["default_model"] == "kimi"
    assert "ustc_api_key" not in config_doc.get("models", {})  # secrets stay apart
    snapshots = list((tmp_path / "history").glob("*.json"))
    assert len(snapshots) == 1
    assert _mode(snapshots[0]) == 0o600
    audit_lines = (tmp_path / "audit.jsonl").read_text().splitlines()
    assert len(audit_lines) == 1
    assert "sk-on-disk-000" not in audit_lines[0]  # redacted on disk too


def test_app_from_env_requires_store_dir(tmp_path):
    with pytest.raises(ValueError, match="RTIME_ADMIN_STORE_DIR"):
        app_from_env({"RTIME_ADMIN_API_KEYS": str(tmp_path / "keys.json")})


def test_app_from_env_requires_keys_path(tmp_path):
    with pytest.raises(ValueError, match="RTIME_ADMIN_API_KEYS"):
        app_from_env({"RTIME_ADMIN_STORE_DIR": str(tmp_path)})


def test_app_from_env_builds_a_locked_down_app(tmp_path):
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps(
            [{"name": "ops", "key": "wiring-test-key-0000000000", "scopes": ["read"]}]
        ),
        encoding="utf-8",
    )
    app = app_from_env(
        {
            "RTIME_ADMIN_STORE_DIR": str(tmp_path / "store"),
            "RTIME_ADMIN_API_KEYS": str(keys_file),
        }
    )
    client = TestClient(app)
    assert client.get("/v1/health").status_code == 401  # auth still required
    ok = client.get(
        "/v1/health", headers={"Authorization": "Bearer wiring-test-key-0000000000"}
    )
    assert ok.status_code == 200
    assert ok.json()["ok"] is True


def test_host_port_defaults_and_validation():
    assert host_port_from_env({}) == ("127.0.0.1", 8790)
    # non-loopback needs the explicit opt-in (defect #13)
    assert host_port_from_env(
        {
            "RTIME_ADMIN_API_HOST": "192.0.2.7",
            "RTIME_ADMIN_API_PORT": "9000",
            "RTIME_ADMIN_API_ALLOW_NONLOOPBACK": "1",
        }
    ) == ("192.0.2.7", 9000)
    with pytest.raises(ValueError, match="not an integer"):
        host_port_from_env({"RTIME_ADMIN_API_PORT": "eight"})
    with pytest.raises(ValueError, match="out of range"):
        host_port_from_env({"RTIME_ADMIN_API_PORT": "0"})


def test_end_to_end_on_disk_survives_restart(tmp_path):
    """PATCH via HTTP against a file store; a rebuilt app sees the state."""
    keys = [
        ApiKey(
            name="admin",
            key=ADMIN_TOKEN,
            scopes=frozenset(("read", "write", "read:sensitive", "write:models")),
        )
    ]

    def make_client():
        store, sink = build_store(tmp_path, env={})
        app = create_app(
            store, api_keys=keys, audit_reader=sink.read_all, version="e2e"
        )
        return TestClient(app)

    c1 = make_client()
    resp = patch_config(
        c1,
        ADMIN_TOKEN,
        {"models.default_model": "kimi", "models.ustc_api_key": "sk-e2e-111"},
    )
    assert resp.status_code == 200, resp.text

    # "restart": fresh store objects over the same directory
    c2 = make_client()
    values = c2.get("/v1/config", headers=auth(ADMIN_TOKEN)).json()["values"]
    assert values["models.default_model"] == "kimi"
    assert values["models.ustc_api_key"] == "***"
    # ETag continuity across restart (same salt, same state)
    assert (
        resp.json()["etag"]
        == c2.get("/v1/config", headers=auth(ADMIN_TOKEN)).json()["etag"]
    )
    # audit tail served from the JSONL file
    entries = c2.get("/v1/audit", headers=auth(ADMIN_TOKEN)).json()["entries"]
    assert len(entries) == 1
    assert entries[0]["actor"] == "admin"
    assert "sk-e2e-111" not in json.dumps(entries)
    # rollback works across the restart boundary too
    snap = c2.get("/v1/history", headers=auth(ADMIN_TOKEN)).json()["snapshots"][0]
    etag = c2.get("/v1/config", headers=auth(ADMIN_TOKEN)).json()["etag"]
    back = c2.post(
        "/v1/rollback",
        json={"snapshot_id": snap["id"]},
        headers={**auth(ADMIN_TOKEN), "If-Match": etag},
    )
    assert back.status_code == 200, back.text
    restored = c2.get("/v1/config/models.default_model", headers=auth(ADMIN_TOKEN))
    assert restored.json()["value"] == "claude"
