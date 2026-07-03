# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K2 models endpoints: /v1/models/catalog + /v1/models/probe.

注入座纹理与 profile reload 一致:没接 => 501;接了 => 只读投影。探测的网络/env
访问都在注入的 callable 里,这里用假件断言 API 层协议(鉴权/参数透传/404/501)。
"""

from __future__ import annotations

from _helpers import READER_TOKEN, auth
from fastapi.testclient import TestClient
from rtime_admin_api import create_app

FAKE_REGISTRY = {
    "schema_version": 1,
    "default_model": "",
    "providers": [{"id": "p1", "label": "P1", "protocol": "openai-chat"}],
}


def _probe_results(provider_id=None, timeout=3.0, check_url=True):
    results = [
        {
            "id": "p1",
            "secret_present": True,
            "reachable": None if not check_url else True,
            "timeout_seen": timeout,
        }
    ]
    if provider_id is not None:
        results = [r for r in results if r["id"] == provider_id]
    return results


def _wired_client(store, api_keys, sink) -> TestClient:
    app = create_app(
        store,
        api_keys=api_keys,
        audit_reader=lambda: [e.to_dict() for e in sink.entries],
        version="0.0-test",
        models_catalog=lambda: FAKE_REGISTRY,
        models_probe=_probe_results,
    )
    return TestClient(app)


def test_models_endpoints_501_when_not_wired(client):
    # 默认 app fixture 不注入 models 件
    for path in ("/v1/models/catalog", "/v1/models/probe"):
        resp = client.get(path, headers=auth(READER_TOKEN))
        assert resp.status_code == 501, resp.text
        assert resp.json()["error"]["code"].startswith("models_")


def test_models_endpoints_require_auth(store, api_keys, sink):
    client = _wired_client(store, api_keys, sink)
    assert client.get("/v1/models/catalog").status_code == 401
    assert client.get("/v1/models/probe").status_code == 401


def test_catalog_shape_and_effective_default(store, api_keys, sink):
    client = _wired_client(store, api_keys, sink)
    resp = client.get("/v1/models/catalog", headers=auth(READER_TOKEN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["registry"] == FAKE_REGISTRY
    # 生效默认来自 CONFIG(models.default_model 的 schema 默认),不是 registry 的兜底
    assert body["effective_default_model"] == "claude"
    assert "PATCH /v1/config" in body["set_default_via"]


def test_probe_passthrough_and_unknown_provider(store, api_keys, sink):
    client = _wired_client(store, api_keys, sink)
    resp = client.get(
        "/v1/models/probe?timeout=1.5&check_url=0", headers=auth(READER_TOKEN)
    )
    assert resp.status_code == 200, resp.text
    result = resp.json()["results"][0]
    assert result["timeout_seen"] == 1.5 and result["reachable"] is None
    # provider 过滤命中
    resp = client.get("/v1/models/probe?provider=p1", headers=auth(READER_TOKEN))
    assert resp.status_code == 200 and len(resp.json()["results"]) == 1
    # 未知 provider -> 404
    resp = client.get("/v1/models/probe?provider=ghost", headers=auth(READER_TOKEN))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_provider"
    # timeout 上限 10(参数校验)
    resp = client.get("/v1/models/probe?timeout=60", headers=auth(READER_TOKEN))
    assert resp.status_code == 422
