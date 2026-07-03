# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Dry-run endpoints: validate (200 {ok, errors}, never writes) and diff."""

from __future__ import annotations

from _helpers import ADMIN_TOKEN, READER_TOKEN, auth, patch_config


def _validate(client, changes, token=READER_TOKEN):
    return client.post(
        "/v1/config/validate", json={"changes": changes}, headers=auth(token)
    )


def test_validate_ok(client):
    resp = _validate(client, {"models.default_model": "kimi"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True and body["errors"] == []
    # J1 preview-impact fields now always present
    assert body["hot"] == ["models.default_model"]  # x-reload=hot
    assert body["restart_required"] == []
    assert body["diff"]["models.default_model"]["after"] == "kimi"


def test_validate_preview_partitions_hot_vs_restart(client):
    # J1: dry-run previews which changes need a restart, without applying.
    resp = _validate(
        client,
        {"models.default_model": "kimi", "library-gateway.http_port": 9999},
    )
    body = resp.json()
    assert body["ok"] is True
    assert body["hot"] == ["models.default_model"]          # hot
    assert body["restart_required"] == ["library-gateway.http_port"]  # restart
    # and it truly did not apply
    assert body["diff"]["library-gateway.http_port"]["after"] == 9999


def test_validate_preview_redacts_secret_diff_without_reveal(client):
    # secret path in a dry-run preview comes back as constant *** (no oracle)
    resp = _validate(client, {"models.ustc_api_key": "sk-live-preview"})
    body = resp.json()
    assert "sk-live-preview" not in resp.text
    assert body["diff"]["models.ustc_api_key"]["after"] == "***"
    assert body["restart_required"] == ["models.ustc_api_key"]


def test_validate_reports_field_errors_and_does_not_write(client, store, sink):
    resp = _validate(client, {"library-gateway.http_port": 70000})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["path"] == "library-gateway.http_port"
    assert "65535" in body["errors"][0]["message"]
    # dry-run truly dry: no write, no snapshot, no audit line
    assert store.get("library-gateway.http_port") == 8780
    assert store.list_history() == []
    assert sink.entries == []


def test_validate_unknown_path_reported_as_error_entry_not_404(client):
    resp = _validate(client, {"ghost.field": 1})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert body["errors"][0]["path"] == "ghost.field"
    assert body["errors"][0]["type"] == "unknown_path"


def test_validate_mixed_known_and_unknown(client):
    resp = _validate(
        client,
        {
            "ghost.field": 1,
            "channel-common.max_turns": -5,
            "models.default_model": "ok-value",
        },
    )
    body = resp.json()
    assert body["ok"] is False
    paths = {e["path"] for e in body["errors"]}
    assert paths == {"ghost.field", "channel-common.max_turns"}


def test_validate_never_echoes_collection_inputs(client):
    hidden = "wrapped-secret-material-000"
    resp = _validate(client, {"models.ustc_api_key": {"nested": hidden}})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is False
    assert hidden not in resp.text
    assert body["errors"][0]["input"] == "***"


def test_diff_shows_redacted_before_after(client):
    secret = "sk-new-credential-999"
    resp = client.post(
        "/v1/config/diff",
        json={
            "changes": {
                "models.default_model": "kimi",
                "models.ustc_api_key": secret,
            }
        },
        headers=auth(READER_TOKEN),
    )
    assert resp.status_code == 200
    diff = resp.json()["diff"]
    assert diff["models.default_model"] == {"before": "claude", "after": "kimi"}
    # non-sensitive caller: secret path shows CONSTANT ***/***, not a salted
    # hmac (which would be an equality oracle, defect #1)
    assert diff["models.ustc_api_key"] == {"before": "***", "after": "***"}
    assert secret not in resp.text


def test_diff_unknown_path_404(client):
    resp = client.post(
        "/v1/config/diff", json={"changes": {"ghost.x": 1}}, headers=auth(READER_TOKEN)
    )
    assert resp.status_code == 404


def test_diff_of_noop_change_is_empty(client):
    resp = client.post(
        "/v1/config/diff",
        json={"changes": {"models.default_model": "claude"}},
        headers=auth(READER_TOKEN),
    )
    assert resp.json()["diff"] == {}


def test_validate_reflects_current_persisted_state(client):
    """Validation merges onto what is stored NOW, not defaults."""
    assert (
        patch_config(client, ADMIN_TOKEN, {"channel-common.max_turns": 7}).status_code
        == 200
    )
    resp = _validate(client, {"channel-common.reply_timeout_seconds": 30})
    assert resp.json()["ok"] is True
