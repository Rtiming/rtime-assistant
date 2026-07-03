# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""/v1/health pending-restart accumulation + /v1/audit tail."""

from __future__ import annotations

from _helpers import ADMIN_TOKEN, READER_TOKEN, auth, patch_config, rollback


def _health(client):
    resp = client.get("/v1/health", headers=auth(READER_TOKEN))
    assert resp.status_code == 200
    return resp.json()


def test_health_shape(client):
    body = _health(client)
    assert body == {"ok": True, "version": "0.0-test", "needs_restart": []}


def test_needs_restart_accumulates_across_applies(client):
    assert (
        patch_config(
            client, ADMIN_TOKEN, {"library-gateway.http_port": 9999}
        ).status_code
        == 200
    )
    assert _health(client)["needs_restart"] == ["library-gateway.http_port"]

    # a hot-only apply does NOT add anything (and clears nothing)
    assert (
        patch_config(client, ADMIN_TOKEN, {"models.default_model": "kimi"}).status_code
        == 200
    )
    assert _health(client)["needs_restart"] == ["library-gateway.http_port"]

    # a second restart-required path accumulates
    assert (
        patch_config(
            client, ADMIN_TOKEN, {"channel-common.read_only": True}
        ).status_code
        == 200
    )
    assert _health(client)["needs_restart"] == [
        "channel-common.read_only",
        "library-gateway.http_port",
    ]


def test_rollback_restart_paths_also_accumulate(client, app):
    step = patch_config(client, ADMIN_TOKEN, {"library-gateway.http_port": 9999})
    assert step.status_code == 200
    # simulate the operator having restarted: clear the in-process set
    app.state.pending_restart.clear()
    assert _health(client)["needs_restart"] == []
    back = rollback(client, ADMIN_TOKEN, step.json()["snapshot_id"])
    assert back.status_code == 200
    # rolling the port back is itself a restart-required change
    assert _health(client)["needs_restart"] == ["library-gateway.http_port"]


def test_audit_endpoint_returns_tail(client, sink):
    for i in range(5):
        assert (
            patch_config(
                client, ADMIN_TOKEN, {"channel-common.max_turns": i + 1}
            ).status_code
            == 200
        )
    resp = client.get("/v1/audit", params={"limit": 2}, headers=auth(READER_TOKEN))
    entries = resp.json()["entries"]
    assert len(entries) == 2
    # newest last (a tail), matching the sink order
    assert entries[-1]["diff"]["channel-common.max_turns"]["after"] == 5
    assert entries[0]["diff"]["channel-common.max_turns"]["after"] == 4


def test_audit_default_limit_and_bounds(client):
    resp = client.get("/v1/audit", headers=auth(READER_TOKEN))
    assert resp.status_code == 200
    assert resp.json()["entries"] == []
    bad = client.get("/v1/audit", params={"limit": 0}, headers=auth(READER_TOKEN))
    assert bad.status_code == 422
    assert bad.json()["error"]["code"] == "invalid_request"


def test_audit_includes_failed_attempts(client, sink):
    resp = patch_config(client, ADMIN_TOKEN, {"channel-common.max_turns": -1})
    assert resp.status_code == 422
    entries = client.get("/v1/audit", headers=auth(READER_TOKEN)).json()["entries"]
    assert entries[-1]["outcome"] == "error"
    assert entries[-1]["action"] == "apply"
