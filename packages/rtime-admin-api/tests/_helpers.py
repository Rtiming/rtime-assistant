# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Request helpers + test tokens shared by the API tests.

Lives in ``_helpers`` (a name unique across the repo) rather than in
``conftest`` because BOTH admin packages have a ``tests/conftest.py`` on
``sys.path`` under pytest's prepend import mode — ``from conftest import ...``
could silently resolve to the other package's conftest when the two suites run
in one pytest invocation.
"""

from __future__ import annotations

from typing import Any

# ---------------------------------------------------------------- test tokens
# Personas (token -> scopes); wired into ApiKey records in conftest.
ADMIN_TOKEN = "test-admin-0123456789abcdef"  # everything incl. read:sensitive
WRITER_TOKEN = "test-writer-0123456789abcdef"  # read+write+ALL field scopes
PLAIN_TOKEN = "test-plain-0123456789abcdef"  # read+write only (no field scopes)
READER_TOKEN = "test-reader-0123456789abcdef"  # read only

FIELD_SCOPES = ("write:models", "write:library", "write:channel")


def auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def get_etag(client, token: str) -> str:
    resp = client.get("/v1/config", headers=auth(token))
    assert resp.status_code == 200, resp.text
    return resp.json()["etag"]


def patch_config(
    client,
    token: str,
    changes: dict[str, Any],
    *,
    etag: str | None = None,
    note: str | None = None,
):
    """PATCH /v1/config with a fresh (or given) If-Match ETag."""
    if etag is None:
        etag = get_etag(client, token)
    body: dict[str, Any] = {"changes": changes}
    if note is not None:
        body["note"] = note
    return client.patch(
        "/v1/config", json=body, headers={**auth(token), "If-Match": etag}
    )


def rollback(client, token: str, snapshot_id: str, *, etag: str | None = None):
    if etag is None:
        etag = get_etag(client, token)
    return client.post(
        "/v1/rollback",
        json={"snapshot_id": snapshot_id},
        headers={**auth(token), "If-Match": etag},
    )
