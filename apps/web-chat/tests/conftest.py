# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Test bootstrap for web-chat: import paths, isolated run log, live-server helpers.

Protocol-level and zero-network-beyond-loopback: every test drives a real
ThreadingHTTPServer on 127.0.0.1:<ephemeral> with the model runner FAKED
(monkeypatched ``web_chat.server.run_claude``), same discipline as
apps/qq-bridge/tests (fake ``run_claude``, real everything else).
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]  # apps/web-chat
REPO_ROOT = APP_ROOT.parents[1]  # repo root (apps/ sibling of packages/, profiles/)
#: The git profiles/ tree the real loader compiles (design §5.2 web-enabled list).
REPO_PROFILES = REPO_ROOT / "profiles"
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import web_chat  # noqa: E402,F401 — side effect: puts rtime_chat_runtime on sys.path
from rtime_chat_runtime.sse import iter_sse_events  # noqa: E402
from web_chat import server as server_mod  # noqa: E402
from web_chat.config import WebChatConfig  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_state(tmp_path, monkeypatch):
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(tmp_path / "web-chat-run.jsonl"))
    monkeypatch.delenv("RTIME_WEB_CHAT_PROFILES", raising=False)
    monkeypatch.delenv("WEB_CHAT_READ_ONLY", raising=False)
    # The real profile loader (T5b) compiles the git profiles/ tree; point every
    # test at the repo's tree so load_profiles() is hermetic (no /etc/rtime).
    monkeypatch.setenv("RTIME_PROFILES_ROOT", str(REPO_PROFILES))


def make_config(tmp_path, **overrides) -> WebChatConfig:
    defaults = dict(
        bind="127.0.0.1",
        port=0,  # ephemeral
        state_dir=str(tmp_path / "state"),
        claude_cli="/usr/bin/claude-fake",  # model path taken; runner is faked
        run_timeout_seconds=10.0,
    )
    defaults.update(overrides)
    return WebChatConfig(**defaults)


@pytest.fixture
def live_server(tmp_path, monkeypatch):
    """Factory: start a server (optionally with a fake runner), yield its base URL."""
    servers: list = []
    threads: list = []

    def start(cfg: WebChatConfig | None = None, fake_run=None) -> str:
        if fake_run is not None:
            monkeypatch.setattr(server_mod, "run_claude", fake_run)
        httpd = server_mod.build_server(cfg or make_config(tmp_path))
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        servers.append(httpd)
        threads.append(thread)
        host, port = httpd.server_address[:2]
        return f"http://{host}:{port}"

    yield start

    for httpd in servers:
        httpd.shutdown()
        httpd.server_close()
    for thread in threads:
        thread.join(timeout=5)


# --- tiny HTTP helpers (stdlib only) ----------------------------------------
def http_get(url: str):
    """GET -> (status, headers, body bytes); 4xx/5xx don't raise."""
    try:
        with urllib.request.urlopen(url, timeout=10) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def post_chat(base_url: str, body: dict, path: str = "/api/chat"):
    """POST JSON -> (status, list of parsed SSE events | one JSON error object)."""
    req = urllib.request.Request(
        base_url + path,
        data=json.dumps(body, ensure_ascii=False).encode(),
        headers={"Content-Type": "application/json", "Accept": "text/event-stream"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            content_type = resp.headers.get("Content-Type", "")
            if "text/event-stream" in content_type:
                return resp.status, list(iter_sse_events(resp))
            return resp.status, json.loads(resp.read() or b"{}")
    except urllib.error.HTTPError as err:
        return err.code, json.loads(err.read() or b"{}")


def read_run_log(tmp_path) -> list[dict]:
    path = tmp_path / "web-chat-run.jsonl"
    if not path.exists():
        return []
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
