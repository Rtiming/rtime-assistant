# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read surface: schema, full/single config reads, redaction default + reveal."""

from __future__ import annotations

from _helpers import ADMIN_TOKEN, READER_TOKEN, WRITER_TOKEN, auth, patch_config

PLAINTEXT = "sk-live-plaintext-credential-XYZ"


def _set_secret(client):
    resp = patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": PLAINTEXT})
    assert resp.status_code == 200, resp.text


def test_schema_lists_modules_with_metadata(client):
    resp = client.get("/v1/schema", headers=auth(READER_TOKEN))
    assert resp.status_code == 200
    modules = resp.json()["modules"]
    assert set(modules) == {
        "channel-common",
        "chat-runtime",
        "library-gateway",
        "models",
        "sandbox",
        "sync",
    }
    key_prop = modules["models"]["properties"]["ustc_api_key"]
    assert key_prop["x-secret"] is True
    assert modules["models"]["properties"]["default_model"]["x-scope"] == "write:models"


def test_get_config_redacts_set_secret_by_default(client):
    _set_secret(client)
    resp = client.get("/v1/config", headers=auth(WRITER_TOKEN))
    values = resp.json()["values"]
    assert values["models.ustc_api_key"] == "***"
    assert PLAINTEXT not in resp.text


def test_get_config_unset_secret_reads_none_not_placeholder(client):
    resp = client.get("/v1/config", headers=auth(READER_TOKEN))
    assert resp.json()["values"]["models.ustc_api_key"] is None


def test_reveal_with_sensitive_scope_returns_plaintext(client):
    _set_secret(client)
    resp = client.get("/v1/config", params={"reveal": 1}, headers=auth(ADMIN_TOKEN))
    assert resp.json()["values"]["models.ustc_api_key"] == PLAINTEXT


def test_get_single_non_secret_value(client):
    resp = client.get("/v1/config/models.default_model", headers=auth(READER_TOKEN))
    assert resp.status_code == 200
    assert resp.json() == {"path": "models.default_model", "value": "claude"}


def test_get_single_secret_redacted_and_revealable(client):
    _set_secret(client)
    path = "/v1/config/models.ustc_api_key"
    redacted = client.get(path, headers=auth(WRITER_TOKEN))
    assert redacted.json()["value"] == "***"
    assert PLAINTEXT not in redacted.text
    revealed = client.get(path, params={"reveal": 1}, headers=auth(ADMIN_TOKEN))
    assert revealed.json()["value"] == PLAINTEXT
    forbidden = client.get(path, params={"reveal": 1}, headers=auth(WRITER_TOKEN))
    assert forbidden.status_code == 403
    assert PLAINTEXT not in forbidden.text


def test_get_single_unknown_path_404(client):
    resp = client.get("/v1/config/models.nope", headers=auth(READER_TOKEN))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_path"
    resp = client.get("/v1/config/ghost.field", headers=auth(READER_TOKEN))
    assert resp.status_code == 404


def test_get_single_malformed_path_400(client):
    resp = client.get("/v1/config/nodotpath", headers=auth(READER_TOKEN))
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "invalid_path"


def test_etag_header_is_quoted_and_matches_body(client):
    resp = client.get("/v1/config", headers=auth(READER_TOKEN))
    etag = resp.json()["etag"]
    assert resp.headers["ETag"] == f'"{etag}"'
    assert len(etag) == 64  # sha256 hex


def test_etag_stable_across_reads_and_actors(client):
    a = client.get("/v1/config", headers=auth(READER_TOKEN)).json()["etag"]
    b = client.get("/v1/config", headers=auth(ADMIN_TOKEN)).json()["etag"]
    assert a == b
