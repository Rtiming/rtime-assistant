# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Endpoint protocol tests: healthz, static page, profiles shape, 400/404, CORS."""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request

from conftest import http_get, post_chat


def test_healthz(live_server):
    base = live_server()
    status, headers, body = http_get(base + "/healthz")
    assert status == 200
    assert "application/json" in headers.get("Content-Type", "")
    assert json.loads(body) == {"ok": True, "service": "web-chat"}


def test_healthz_sends_cors(live_server):
    base = live_server()
    _, headers, _ = http_get(base + "/healthz")
    assert headers.get("Access-Control-Allow-Origin") == "*"


def test_index_served_at_root(live_server):
    base = live_server()
    status, headers, body = http_get(base + "/")
    assert status == 200
    assert "text/html" in headers.get("Content-Type", "")
    text = body.decode("utf-8")
    assert "rtime 问答" in text
    assert "api/chat" in text  # the fetch-streaming client is wired in


def test_index_pins_cdn_with_sri(live_server):
    """No npm/build step: renderers come from pinned CDN URLs, every one with SRI."""
    base = live_server()
    _, _, body = http_get(base + "/")
    text = body.decode("utf-8")
    for asset in ("marked@12.0.2", "dompurify@3.1.6", "katex@0.16.11"):
        assert asset in text, f"pinned CDN asset missing: {asset}"
    # every external script/stylesheet tag carries an integrity hash
    tags = [t for t in re.findall(r"<(?:script|link)\b[^>]*>", text, re.S) if "cdn.jsdelivr.net" in t]
    assert tags, "expected CDN script/link tags in index.html"
    for tag in tags:
        assert "integrity=" in tag and "crossorigin=" in tag, tag


def test_profiles_shape(live_server):
    base = live_server()
    status, _, body = http_get(base + "/api/profiles")
    assert status == 200
    data = json.loads(body)
    profiles = data["profiles"]
    assert isinstance(profiles, list) and profiles
    for p in profiles:
        assert set(p) == {"id", "name", "description", "read_only"}
        assert "system_prompt" not in p  # never leak the prompt text
    assert data["default"] == profiles[0]["id"]
    ids = {p["id"] for p in profiles}
    assert {"owner", "studentunion"} <= ids
    by_id = {p["id"]: p for p in profiles}
    assert by_id["studentunion"]["read_only"] is True
    assert by_id["owner"]["read_only"] is False


def test_unknown_get_404(live_server):
    base = live_server()
    status, _, body = http_get(base + "/api/nope")
    assert status == 404
    assert json.loads(body) == {"error": "not found"}


def test_unknown_post_404(live_server):
    base = live_server()
    status, payload = post_chat(base, {"message": "hi"}, path="/api/nope")
    assert status == 404


def test_chat_invalid_json_400(live_server):
    base = live_server()
    req = urllib.request.Request(
        base + "/api/chat",
        data=b"not json{",
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected HTTP 400")
    except urllib.error.HTTPError as err:
        assert err.code == 400


def test_chat_missing_message_400(live_server):
    base = live_server()
    status, payload = post_chat(base, {"profile": "owner"})
    assert status == 400
    assert "message" in payload["error"]


def test_chat_blank_message_400(live_server):
    base = live_server()
    status, _ = post_chat(base, {"profile": "owner", "message": "   "})
    assert status == 400


def test_chat_non_object_body_400(live_server):
    base = live_server()
    req = urllib.request.Request(
        base + "/api/chat",
        data=b'["a", "list"]',
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        raise AssertionError("expected HTTP 400")
    except urllib.error.HTTPError as err:
        assert err.code == 400


def test_chat_unknown_profile_400(live_server):
    base = live_server()
    status, payload = post_chat(base, {"profile": "ghost", "message": "hi"})
    assert status == 400
    assert "ghost" in payload["error"]


def test_options_preflight_cors(live_server):
    base = live_server()
    req = urllib.request.Request(base + "/api/chat", method="OPTIONS")
    with urllib.request.urlopen(req, timeout=10) as resp:
        assert resp.status == 204
        assert resp.headers.get("Access-Control-Allow-Origin") == "*"
        assert "POST" in resp.headers.get("Access-Control-Allow-Methods", "")
