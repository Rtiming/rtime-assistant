# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Auth gate: 401 vs 403, scope enforcement (incl. per-field x-scope), actor audit."""

from __future__ import annotations

import pytest
from _helpers import (
    ADMIN_TOKEN,
    PLAIN_TOKEN,
    READER_TOKEN,
    WRITER_TOKEN,
    auth,
    get_etag,
    patch_config,
)
from rtime_admin_api import create_app

# (method, path, json body or None) — every endpoint in the surface.
ENDPOINTS = [
    ("GET", "/v1/health", None),
    ("GET", "/v1/schema", None),
    ("GET", "/v1/config", None),
    ("GET", "/v1/config/models.default_model", None),
    ("GET", "/v1/history", None),
    ("GET", "/v1/audit", None),
    ("POST", "/v1/config/validate", {"changes": {"models.default_model": "x"}}),
    ("POST", "/v1/config/diff", {"changes": {"models.default_model": "x"}}),
    ("PATCH", "/v1/config", {"changes": {"models.default_model": "x"}}),
    ("POST", "/v1/rollback", {"snapshot_id": "nope"}),
]


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_every_endpoint_401_without_token(client, method, path, body):
    resp = client.request(method, path, json=body)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"
    assert resp.headers.get("WWW-Authenticate") == "Bearer"


@pytest.mark.parametrize("method,path,body", ENDPOINTS)
def test_every_endpoint_401_with_wrong_token(client, method, path, body):
    headers = auth("not-a-configured-token-000000")
    resp = client.request(method, path, json=body, headers=headers)
    assert resp.status_code == 401
    assert resp.json()["error"]["code"] == "unauthorized"


def test_non_bearer_scheme_is_401(client):
    resp = client.get("/v1/health", headers={"Authorization": "Basic dXNlcjpwdw=="})
    assert resp.status_code == 401


def test_empty_bearer_token_is_401(client):
    resp = client.get("/v1/health", headers={"Authorization": "Bearer   "})
    assert resp.status_code == 401


def test_reader_can_read_but_not_write(client):
    assert client.get("/v1/config", headers=auth(READER_TOKEN)).status_code == 200
    resp = patch_config(client, READER_TOKEN, {"sandbox.greeting": "hi"})
    assert resp.status_code == 403
    assert resp.json()["error"]["code"] == "forbidden"


def test_reader_cannot_rollback(client):
    etag = get_etag(client, READER_TOKEN)
    resp = client.post(
        "/v1/rollback",
        json={"snapshot_id": "whatever"},
        headers={**auth(READER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 403


def test_scoped_field_needs_its_x_scope(client):
    """models.default_model declares x-scope=write:models; plain `write` is not enough."""
    resp = patch_config(client, PLAIN_TOKEN, {"models.default_model": "kimi"})
    assert resp.status_code == 403
    assert "write:models" in resp.json()["error"]["message"]


def test_scopeless_field_open_to_plain_write(client):
    resp = patch_config(client, PLAIN_TOKEN, {"sandbox.greeting": "hi"})
    assert resp.status_code == 200, resp.text
    got = client.get("/v1/config/sandbox.greeting", headers=auth(PLAIN_TOKEN))
    assert got.json()["value"] == "hi"


def test_field_scope_holder_can_write_scoped_field(client):
    resp = patch_config(client, WRITER_TOKEN, {"models.default_model": "kimi"})
    assert resp.status_code == 200, resp.text


def test_mixed_changeset_fails_atomically_on_scope(client):
    """One scoped path in the set -> whole PATCH is 403, nothing is applied."""
    resp = patch_config(
        client,
        PLAIN_TOKEN,
        {"sandbox.greeting": "nope", "models.default_model": "kimi"},
    )
    assert resp.status_code == 403
    got = client.get("/v1/config/sandbox.greeting", headers=auth(PLAIN_TOKEN))
    assert got.json()["value"] == "hello"  # unchanged default


def test_reveal_requires_read_sensitive_scope(client):
    resp = client.get("/v1/config", params={"reveal": 1}, headers=auth(WRITER_TOKEN))
    assert resp.status_code == 403
    ok = client.get("/v1/config", params={"reveal": 1}, headers=auth(ADMIN_TOKEN))
    assert ok.status_code == 200


def test_audit_actor_is_key_name_and_source_http(client, sink):
    resp = patch_config(client, WRITER_TOKEN, {"models.default_model": "kimi"})
    assert resp.status_code == 200
    entry = sink.entries[-1]
    assert entry.actor == "writer"
    assert entry.source == "http"
    assert entry.action == "apply"
    assert entry.outcome == "ok"


def test_create_app_refuses_empty_key_list(store):
    with pytest.raises(ValueError):
        create_app(store, api_keys=[], audit_reader=lambda: [], version="0")
