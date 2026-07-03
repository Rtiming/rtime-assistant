# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K5 GET /v1/modules:模块总览端点(注入座:未接501/接了透传报告/鉴权)。

真实 manifest 的内容校验在 admin-core 的 test_modules.py;这里只测 API 层协议,
加一条 wiring 的 make_modules_report 对真仓库 deploy/modules.json 的真读冒烟。
"""

from __future__ import annotations

from pathlib import Path

from _helpers import READER_TOKEN, auth
from fastapi.testclient import TestClient
from rtime_admin_api import create_app

FAKE_REPORT = {"ok": True, "total": 2, "by_kind": {"core": 2}, "modules": [], "issues": []}


def _wired_client(store, api_keys, sink) -> TestClient:
    app = create_app(
        store,
        api_keys=api_keys,
        audit_reader=lambda: [e.to_dict() for e in sink.entries],
        version="0.0-test",
        modules_report=lambda: FAKE_REPORT,
    )
    return TestClient(app)


def test_modules_501_when_not_wired(client):
    resp = client.get("/v1/modules", headers=auth(READER_TOKEN))
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "modules_report_unavailable"


def test_modules_requires_auth_and_returns_report(store, api_keys, sink):
    client = _wired_client(store, api_keys, sink)
    assert client.get("/v1/modules").status_code == 401
    resp = client.get("/v1/modules", headers=auth(READER_TOKEN))
    assert resp.status_code == 200 and resp.json() == FAKE_REPORT


def test_make_modules_report_reads_real_manifest():
    from rtime_admin_api.wiring import make_modules_report

    repo = Path(__file__).resolve().parents[3]  # tests -> rtime-admin-api -> packages -> repo
    manifest = repo / "deploy" / "modules.json"
    report = make_modules_report(manifest, environ={"COMPOSE_PROFILES": "qq"})()
    assert report["total"] >= 22 and report["ok"] is True
    by_id = {m["id"]: m for m in report["modules"]}
    assert by_id["channel-qq"]["installed"] is True
    assert by_id["channel-web"]["installed"] is False
    assert by_id["integration-sync"]["config_module"] == "sync"
