# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""THE redaction invariant: once a secret is stored, its plaintext must never
appear in ANY response body — success or error, any endpoint — to a caller
without the ``read:sensitive`` scope. (File deliberately not named *secret*:
the repo .gitignore ignores that glob.)"""

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
    rollback,
)

PLAINTEXT = "sk-live-hunter2-do-not-leak-ever-42"


@pytest.fixture
def loaded_client(client):
    """A client whose store already holds the secret (set by the admin key)."""
    resp = patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": PLAINTEXT})
    assert resp.status_code == 200
    assert PLAINTEXT not in resp.text  # even the admin's apply response is redacted
    return client


def _assert_clean(resp):
    assert PLAINTEXT not in resp.text, (
        f"plaintext secret leaked ({resp.request.method} "
        f"{resp.request.url.path} -> {resp.status_code})"
    )
    return resp


def test_success_paths_never_leak(loaded_client):
    c = loaded_client
    for token in (READER_TOKEN, WRITER_TOKEN, PLAIN_TOKEN):
        _assert_clean(c.get("/v1/config", headers=auth(token)))
        _assert_clean(c.get("/v1/config/models.ustc_api_key", headers=auth(token)))
        _assert_clean(c.get("/v1/schema", headers=auth(token)))
        _assert_clean(c.get("/v1/history", headers=auth(token)))
        _assert_clean(c.get("/v1/audit", headers=auth(token)))
        _assert_clean(c.get("/v1/health", headers=auth(token)))


def test_dry_runs_never_leak(loaded_client):
    c = loaded_client
    # validate: valid secret change, invalid (collection) secret change
    _assert_clean(
        c.post(
            "/v1/config/validate",
            json={"changes": {"models.ustc_api_key": PLAINTEXT}},
            headers=auth(READER_TOKEN),
        )
    )
    _assert_clean(
        c.post(
            "/v1/config/validate",
            json={"changes": {"models.ustc_api_key": {"v": PLAINTEXT}}},
            headers=auth(READER_TOKEN),
        )
    )
    # diff of a secret rotation shows CONSTANT ***/*** to a non-sensitive
    # caller (not a salted hmac — that was the equality oracle, defect #1)
    resp = _assert_clean(
        c.post(
            "/v1/config/diff",
            json={"changes": {"models.ustc_api_key": PLAINTEXT + "-rotated"}},
            headers=auth(READER_TOKEN),
        )
    )
    change = resp.json()["diff"]["models.ustc_api_key"]
    assert change == {"before": "***", "after": "***"}


def test_error_paths_never_leak(loaded_client):
    c = loaded_client
    # 403: reveal without the scope
    _assert_clean(c.get("/v1/config", params={"reveal": 1}, headers=auth(WRITER_TOKEN)))
    # 428: apply carrying the secret, no If-Match
    _assert_clean(
        c.patch(
            "/v1/config",
            json={"changes": {"models.ustc_api_key": PLAINTEXT}},
            headers=auth(WRITER_TOKEN),
        )
    )
    # 412: stale etag, secret in body
    _assert_clean(
        c.patch(
            "/v1/config",
            json={"changes": {"models.ustc_api_key": PLAINTEXT}},
            headers={**auth(WRITER_TOKEN), "If-Match": "0" * 64},
        )
    )
    # 422 core validation: secret alongside an invalid field in one change set
    _assert_clean(
        patch_config(
            c,
            WRITER_TOKEN,
            {"models.ustc_api_key": PLAINTEXT, "library-gateway.http_port": 70000},
        )
    )
    # 403 field-scope rejection with the secret in the change set
    _assert_clean(patch_config(c, PLAIN_TOKEN, {"models.ustc_api_key": PLAINTEXT}))
    # 404s
    _assert_clean(rollback(c, WRITER_TOKEN, "no-such-snapshot"))
    _assert_clean(c.get("/v1/config/ghost.field", headers=auth(READER_TOKEN)))


def test_malformed_bodies_are_not_echoed(loaded_client):
    """FastAPI request-validation errors must not reflect the offending input."""
    c = loaded_client
    etag = get_etag(c, WRITER_TOKEN)
    headers = {**auth(WRITER_TOKEN), "If-Match": etag}
    # changes as a list carrying the secret
    resp = c.patch(
        "/v1/config",
        json={"changes": ["models.ustc_api_key", PLAINTEXT]},
        headers=headers,
    )
    assert resp.status_code == 422
    _assert_clean(resp)
    assert resp.json()["error"]["code"] == "invalid_request"
    # secret smuggled into an unexpected extra field (extra=forbid)
    _assert_clean(
        c.patch(
            "/v1/config",
            json={"changes": {}, "smuggle": PLAINTEXT},
            headers=headers,
        )
    )
    # non-string note
    _assert_clean(
        c.patch(
            "/v1/config",
            json={"changes": {"sandbox.greeting": "x"}, "note": {"n": PLAINTEXT}},
            headers=headers,
        )
    )
    # secret in a broken validate body
    _assert_clean(
        c.post(
            "/v1/config/validate",
            json={"changes": PLAINTEXT},
            headers=auth(READER_TOKEN),
        )
    )


def test_history_snapshots_hold_secrets_but_descriptors_do_not(loaded_client, store):
    """The snapshot PAYLOAD contains secrets by design (rollback needs them) —
    the API must only ever serve descriptors."""
    # a second apply snapshots the CURRENT state, which now includes the secret
    assert (
        patch_config(loaded_client, ADMIN_TOKEN, {"sandbox.greeting": "hi"}).status_code
        == 200
    )
    snaps = store.history.list()
    assert any(
        s.secrets.get("models", {}).get("ustc_api_key") == PLAINTEXT for s in snaps
    )  # payload really is there...
    resp = loaded_client.get("/v1/history", headers=auth(READER_TOKEN))
    _assert_clean(resp)  # ...but never on the wire


def test_audit_log_itself_is_redacted(loaded_client, sink):
    for entry in sink.entries:
        assert PLAINTEXT not in entry.to_json()


def test_reveal_scope_is_the_only_door(loaded_client):
    resp = loaded_client.get(
        "/v1/config", params={"reveal": 1}, headers=auth(ADMIN_TOKEN)
    )
    assert resp.json()["values"]["models.ustc_api_key"] == PLAINTEXT
