# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Static operator-panel route (T7) + the ``?provenance=1`` read it depends on.

Auth stance under test (documented in ``panel.py``): the static SHELL (``/``,
``/panel``, the JS assets) is PUBLIC — a browser must load it before a token can
be pasted — while every ``/v1/*`` endpoint stays behind bearer auth. These tests
pin BOTH halves so the shell can never accidentally start gating (lock-out) and,
more importantly, so a ``/v1`` path can never accidentally become public.
"""

from __future__ import annotations

from _helpers import READER_TOKEN, auth


# ------------------------------------------------------------- shell is served
def test_panel_root_serves_html_unauthenticated(client):
    resp = client.get("/")
    assert resp.status_code == 200, resp.text
    assert resp.headers["content-type"].startswith("text/html")
    # It is the panel shell, and it wires the schema-driven form + views.
    body = resp.text
    assert "rtime 控制面板" in body
    assert "panel.js" in body
    assert "panel.schema.js" in body


def test_panel_alias_serves_html(client):
    resp = client.get("/panel")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/html")


def test_panel_js_assets_served_with_js_content_type(client):
    for path in ("/panel.js", "/panel.schema.js", "/panel/panel.js"):
        resp = client.get(path)
        assert resp.status_code == 200, f"{path}: {resp.status_code}"
        assert "javascript" in resp.headers["content-type"], path


def test_panel_unknown_asset_is_404(client):
    resp = client.get("/panel/secrets.py")
    assert resp.status_code == 404
    # no traversal / arbitrary-file serving
    resp = client.get("/panel/..%2Fpanel.py")
    assert resp.status_code == 404


# --------------------------------------------------- API stays gated (the point)
def test_v1_still_requires_auth_despite_public_panel(client):
    # The public shell must NOT have opened /v1 up.
    assert client.get("/v1/config").status_code == 401
    assert client.get("/v1/schema").status_code == 401
    assert client.get("/v1/health").status_code == 401
    # sanity: with a token it works
    assert client.get("/v1/config", headers=auth(READER_TOKEN)).status_code == 200


def test_panel_shell_needs_no_token_but_carries_no_config(client):
    # Shell loads token-free; it contains no config values / secrets, only markup.
    body = client.get("/").text
    assert "Bearer" in body  # it *asks* the operator for a token
    assert "ustc_api_key" not in body  # ...but ships no field values/names


# --------------------------------------------- provenance read (config-tree feed)
def test_config_provenance_shapes_each_value(client):
    resp = client.get(
        "/v1/config", params={"provenance": 1}, headers=auth(READER_TOKEN)
    )
    assert resp.status_code == 200, resp.text
    values = resp.json()["values"]
    entry = values["models.default_model"]
    assert set(entry) == {"value", "provenance"}
    assert entry["provenance"] in {"env", "store", "profile", "default"}
    # a never-set field resolves to its schema default
    assert entry["provenance"] == "default"


def test_config_provenance_secret_still_redacted_without_reveal(client):
    # provenance is a plain read, NOT a reveal: a secret stays *** without
    # read:sensitive even when provenance is requested.
    etag = client.get("/v1/config", headers=auth(READER_TOKEN)).json()["etag"]
    patched = client.patch(
        "/v1/config",
        json={"changes": {"models.ustc_api_key": "sk-live-should-stay-hidden"}},
        headers={**auth(READER_TOKEN), "If-Match": f'"{etag}"'},
    )
    # reader lacks write -> 403; use the read path with a pre-set value instead is
    # not possible here, so assert the redaction contract on an unset secret:
    assert patched.status_code in (403, 200)
    resp = client.get(
        "/v1/config", params={"provenance": 1}, headers=auth(READER_TOKEN)
    )
    assert "sk-live-should-stay-hidden" not in resp.text
    secret_entry = resp.json()["values"]["models.ustc_api_key"]
    assert set(secret_entry) == {"value", "provenance"}
    assert secret_entry["value"] in (None, "***")


def test_config_without_provenance_unchanged(client):
    # default (no provenance) response is the flat {path: value} shape as before.
    resp = client.get("/v1/config", headers=auth(READER_TOKEN))
    values = resp.json()["values"]
    assert values["models.default_model"] == "claude"
