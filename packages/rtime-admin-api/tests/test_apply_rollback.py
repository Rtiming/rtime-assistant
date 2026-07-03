# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""PATCH apply + rollback through the HTTP surface: transaction, audit, history."""

from __future__ import annotations

from _helpers import ADMIN_TOKEN, auth, get_etag, patch_config, rollback


def test_patch_applies_audits_and_snapshots(client, store, sink):
    resp = patch_config(
        client, ADMIN_TOKEN, {"models.default_model": "kimi"}, note="switch model"
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["changed"] == ["models.default_model"]
    assert body["hot"] == ["models.default_model"]  # x-reload=hot
    assert body["restart_required"] == []
    assert body["diff"]["models.default_model"] == {"before": "claude", "after": "kimi"}
    # L2 injected ts + snapshot id
    assert body["ts"].endswith("+00:00")  # UTC ISO-8601
    assert len(body["snapshot_id"]) == 32  # uuid4 hex
    # value actually persisted
    assert store.get("models.default_model") == "kimi"
    # one audit line with the L2 clock and the note
    entry = sink.entries[-1]
    assert entry.ts == body["ts"]
    assert entry.snapshot_id == body["snapshot_id"]
    assert entry.detail == "switch model"
    # snapshot listed in history
    history = client.get("/v1/history", headers=auth(ADMIN_TOKEN)).json()["snapshots"]
    assert [s["id"] for s in history] == [body["snapshot_id"]]
    assert set(history[0]) == {"id", "ts", "note"}  # descriptors only, no payload


def test_patch_classifies_hot_vs_restart(client):
    resp = patch_config(
        client,
        ADMIN_TOKEN,
        {"library-gateway.http_port": 9999, "library-gateway.idle_timeout": 60},
    )
    body = resp.json()
    assert body["hot"] == ["library-gateway.idle_timeout"]
    assert body["restart_required"] == ["library-gateway.http_port"]


def test_patch_invalid_value_is_422_with_field_errors(client, store, sink):
    resp = patch_config(client, ADMIN_TOKEN, {"library-gateway.http_port": 70000})
    assert resp.status_code == 422
    err = resp.json()["error"]
    assert err["code"] == "validation_failed"
    assert err["errors"][0]["path"] == "library-gateway.http_port"
    # nothing persisted; the failed attempt IS audited by the core (outcome=error)
    assert store.get("library-gateway.http_port") == 8780
    assert sink.entries[-1].outcome == "error"
    assert sink.entries[-1].actor == "admin"


def test_patch_multi_field_changeset(client, store):
    resp = patch_config(
        client,
        ADMIN_TOKEN,
        {"models.default_model": "kimi", "channel-common.read_only": True},
    )
    assert resp.status_code == 200
    assert store.get("models.default_model") == "kimi"
    assert store.get("channel-common.read_only") is True


def test_patch_empty_changes_400(client):
    etag = get_etag(client, ADMIN_TOKEN)
    resp = client.patch(
        "/v1/config",
        json={"changes": {}},
        headers={**auth(ADMIN_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "empty_changes"


def test_rollback_roundtrip_via_api(client, store, sink):
    first = patch_config(client, ADMIN_TOKEN, {"models.default_model": "kimi"})
    assert first.status_code == 200
    second = patch_config(client, ADMIN_TOKEN, {"models.default_model": "deepseek"})
    assert second.status_code == 200
    # rolling back to the SECOND snapshot restores the state before change #2
    resp = rollback(client, ADMIN_TOKEN, second.json()["snapshot_id"])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert store.get("models.default_model") == "kimi"
    assert body["changed"] == ["models.default_model"]
    assert body["snapshot_id"] != second.json()["snapshot_id"]  # pre-rollback snapshot
    assert body["etag"] == get_etag(client, ADMIN_TOKEN)
    entry = sink.entries[-1]
    assert entry.action == "rollback"
    assert entry.actor == "admin"
    assert entry.source == "http"


def test_rollback_unknown_snapshot_404(client):
    resp = rollback(client, ADMIN_TOKEN, "no-such-snapshot")
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_snapshot"


def test_rollback_itself_reversible(client, store):
    """A rollback takes a pre-rollback snapshot you can roll back to again."""
    step = patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "changed"})
    assert step.status_code == 200
    back = rollback(client, ADMIN_TOKEN, step.json()["snapshot_id"])
    assert back.status_code == 200
    assert store.get("sandbox.greeting") == "hello"
    again = rollback(client, ADMIN_TOKEN, back.json()["snapshot_id"])
    assert again.status_code == 200
    assert store.get("sandbox.greeting") == "changed"
