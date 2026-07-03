# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""serve_unix_socket: one warm gateway daemon serving many per-message connections.

The prewarmed-gateway path for the chat bridges: a single long-lived process binds a
unix socket and handles many short-lived ``socat``-bridged ``claude`` connections,
reusing one warm server (jieba/embedder loaded once) instead of cold-starting per
message. This test proves multiple sequential connections are served by one daemon.
"""

from __future__ import annotations

import json
import os
import socket
import sys
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"


def _load():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.mcp_server")


def _rpc(conn: socket.socket, obj: dict) -> dict:
    conn.sendall((json.dumps(obj) + "\n").encode())
    buf = b""
    while b"\n" not in buf:
        chunk = conn.recv(65536)
        if not chunk:
            break
        buf += chunk
    return json.loads(buf.split(b"\n", 1)[0].decode())


def test_unix_socket_serves_multiple_connections(monkeypatch):
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", "0")  # no jieba/index load in test
    mcp = _load()
    # AF_UNIX paths are capped (~104 chars on macOS); the pytest tmp_path is too long,
    # so use a short /tmp path. Production sockets live at short paths (/run, NVMe).
    sock_path = f"/tmp/rtgw-{os.getpid()}.sock"  # noqa: S108 — short path required by AF_UNIX
    if Path(sock_path).exists():
        os.unlink(sock_path)

    threading.Thread(
        target=mcp.serve_unix_socket, args=(sock_path,), daemon=True
    ).start()

    for _ in range(60):  # wait for the daemon to bind
        if Path(sock_path).exists():
            break
        time.sleep(0.05)
    assert Path(sock_path).exists()

    # Two SEPARATE connections (each = one per-message claude) served by one daemon.
    for req_id in (1, 2):
        conn = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        conn.connect(sock_path)
        resp = _rpc(conn, {"jsonrpc": "2.0", "id": req_id, "method": "ping"})
        conn.close()
        assert resp.get("id") == req_id
        assert "result" in resp


def _free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def test_http_transport_serves_jsonrpc(monkeypatch):
    """The streamable-HTTP transport (what the claude CLI connects to natively) handles
    JSON-RPC over POST from one warm daemon — verified against the same protocol the spike
    proved claude accepts."""
    import urllib.request

    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_PREWARM", "0")
    mcp = _load()
    port = _free_port()
    base = f"http://127.0.0.1:{port}/mcp"
    threading.Thread(
        target=mcp.serve_http, args=("127.0.0.1", port), daemon=True
    ).start()

    def post(obj: dict) -> dict:
        req = urllib.request.Request(
            base, data=json.dumps(obj).encode(), headers={"Content-Type": "application/json"}
        )
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.loads(r.read())

    for _ in range(60):  # wait for the daemon to bind
        try:
            post({"jsonrpc": "2.0", "id": 0, "method": "ping"})
            break
        except Exception:
            time.sleep(0.05)

    r1 = post(
        {"jsonrpc": "2.0", "id": 1, "method": "initialize",
         "params": {"protocolVersion": "2024-11-05", "capabilities": {}}}
    )
    assert r1.get("id") == 1 and r1.get("result", {}).get("serverInfo")
    r2 = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    assert r2.get("id") == 2 and isinstance(r2.get("result", {}).get("tools"), list)
