# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Caddy-style optimistic concurrency: If-Match required, stale rejected."""

from __future__ import annotations

from _helpers import ADMIN_TOKEN, auth, get_etag, patch_config, rollback


def test_get_then_patch_with_fresh_etag_succeeds(client):
    etag = get_etag(client, ADMIN_TOKEN)
    resp = patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "hi"}, etag=etag)
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["etag"] != etag  # state changed -> new tag
    assert resp.headers["ETag"] == f'"{body["etag"]}"'
    # the returned tag IS the current one
    assert get_etag(client, ADMIN_TOKEN) == body["etag"]


def test_patch_without_if_match_is_428_and_writes_nothing(client, sink, store):
    resp = client.patch(
        "/v1/config",
        json={"changes": {"sandbox.greeting": "hi"}},
        headers=auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 428
    assert resp.json()["error"]["code"] == "precondition_required"
    assert store.get("sandbox.greeting") == "hello"
    assert sink.entries == []  # rejected before the core transaction: no audit line
    assert store.list_history() == []  # and no snapshot


def test_patch_with_stale_etag_is_412_and_writes_nothing(client, store):
    stale = get_etag(client, ADMIN_TOKEN)
    assert (
        patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "v1"}).status_code == 200
    )
    resp = patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "v2"}, etag=stale)
    assert resp.status_code == 412
    assert resp.json()["error"]["code"] == "etag_mismatch"
    assert store.get("sandbox.greeting") == "v1"


def test_wildcard_if_match_rejected(client):
    resp = patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "hi"}, etag="*")
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "wildcard_if_match_rejected"


def test_weak_etag_rejected(client):
    etag = get_etag(client, ADMIN_TOKEN)
    resp = patch_config(
        client, ADMIN_TOKEN, {"sandbox.greeting": "hi"}, etag=f'W/"{etag}"'
    )
    assert resp.status_code == 400
    assert resp.json()["error"]["code"] == "weak_etag_rejected"


def test_quoted_if_match_accepted(client):
    etag = get_etag(client, ADMIN_TOKEN)
    resp = patch_config(
        client, ADMIN_TOKEN, {"sandbox.greeting": "hi"}, etag=f'"{etag}"'
    )
    assert resp.status_code == 200, resp.text


def test_non_ascii_if_match_is_clean_412_not_500(client):
    """HTTP headers arrive latin-1-decoded; a high-byte If-Match becomes a
    non-ASCII str server-side, which would make ``hmac.compare_digest`` on str
    raise TypeError (500) — the handler compares utf-8 BYTES so it is a 412.
    (Sent as raw bytes because httpx refuses non-ASCII str header values.)"""
    resp = client.patch(
        "/v1/config",
        json={"changes": {"sandbox.greeting": "hi"}},
        headers={"Authorization": f"Bearer {ADMIN_TOKEN}", "If-Match": b"\xe9tag"},
    )
    assert resp.status_code == 412


def test_rollback_missing_if_match_428(client):
    assert (
        patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "v1"}).status_code == 200
    )
    snap = client.get("/v1/history", headers=auth(ADMIN_TOKEN)).json()["snapshots"][0]
    resp = client.post(
        "/v1/rollback", json={"snapshot_id": snap["id"]}, headers=auth(ADMIN_TOKEN)
    )
    assert resp.status_code == 428


def test_rollback_stale_etag_412(client):
    stale = get_etag(client, ADMIN_TOKEN)
    assert (
        patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "v1"}).status_code == 200
    )
    snap = client.get("/v1/history", headers=auth(ADMIN_TOKEN)).json()["snapshots"][0]
    resp = rollback(client, ADMIN_TOKEN, snap["id"], etag=stale)
    assert resp.status_code == 412


def test_two_agents_second_writer_loses(client):
    """Both agents read the same ETag; the slower one gets 412, not a silent clobber."""
    shared = get_etag(client, ADMIN_TOKEN)
    first = patch_config(
        client, ADMIN_TOKEN, {"models.default_model": "kimi"}, etag=shared
    )
    assert first.status_code == 200
    second = patch_config(
        client, ADMIN_TOKEN, {"models.default_model": "deepseek"}, etag=shared
    )
    assert second.status_code == 412
    got = client.get("/v1/config/models.default_model", headers=auth(ADMIN_TOKEN))
    assert got.json()["value"] == "kimi"
