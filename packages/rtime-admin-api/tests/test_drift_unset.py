# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J1 provenance/drift/unset:面板"改了UI不生效"的解药(config-and-access §2.2)。

GET /v1/config?provenance=1 已有(标每个值来自哪层);本文件覆盖新增的
GET /v1/config/drift(被 profile 遮蔽的 store override 清单)+ DELETE /v1/config/{path}
(unset override 交还上层)。
"""

from __future__ import annotations

from _helpers import (
    ADMIN_TOKEN,
    READER_TOKEN,
    WRITER_TOKEN,
    auth,
    get_etag,
    patch_config,
)


def _shadow(store, path, profile_value):
    """给 store 装一个 profile 层值(遮蔽用),模拟 git profile 声明了该字段。"""
    store._profile_layer[path] = profile_value


def test_drift_empty_by_default(client):
    resp = client.get("/v1/config/drift", headers=auth(READER_TOKEN))
    assert resp.status_code == 200
    assert resp.json() == {"drift": []}


def test_drift_lists_store_override_shadowed_by_profile(client, store):
    # profile 声明 default_model=profile-kimi,但 store 又 override 成 store-ds
    _shadow(store, "models.default_model", "profile-kimi")
    patch_config(client, ADMIN_TOKEN, {"models.default_model": "store-ds"})
    resp = client.get("/v1/config/drift", headers=auth(READER_TOKEN))
    drift = resp.json()["drift"]
    entry = next(d for d in drift if d["path"] == "models.default_model")
    assert entry["store"] == "store-ds" and entry["profile"] == "profile-kimi"
    assert entry["secret"] is False


def test_drift_route_not_captured_by_path_param(client):
    # /v1/config/drift 必须命中 drift 端点,不被 /v1/config/{path} 当成字段名
    resp = client.get("/v1/config/drift", headers=auth(READER_TOKEN))
    assert resp.status_code == 200 and "drift" in resp.json()


def test_unset_clears_override_falls_back_to_profile(client, store):
    _shadow(store, "models.default_model", "profile-kimi")
    patch_config(client, ADMIN_TOKEN, {"models.default_model": "store-ds"})
    assert store.get("models.default_model") == "store-ds"
    etag = get_etag(client, WRITER_TOKEN)
    resp = client.delete(
        "/v1/config/models.default_model",
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 200, resp.text
    # override 清掉,值落回 profile 层
    assert store.get("models.default_model") == "profile-kimi"
    # drift 现在不再列它
    drift = client.get("/v1/config/drift", headers=auth(READER_TOKEN)).json()["drift"]
    assert all(d["path"] != "models.default_model" for d in drift)


def test_unset_requires_if_match(client, store):
    patch_config(client, ADMIN_TOKEN, {"models.default_model": "store-ds"})
    resp = client.delete(
        "/v1/config/models.default_model", headers=auth(WRITER_TOKEN)
    )
    assert resp.status_code == 428  # If-Match required (same as PATCH/rollback)


def test_unset_idempotent_noop_when_no_override(client, store, sink):
    etag = get_etag(client, WRITER_TOKEN)
    resp = client.delete(
        "/v1/config/models.default_model",
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 200  # no override to clear = idempotent success
    # still recorded as an unset audit entry (intent on the record)
    assert any(getattr(e, "action", None) == "unset" for e in sink.entries)


def test_unset_needs_write_scope(client):
    # reader (no write) cannot unset
    etag = get_etag(client, READER_TOKEN)
    resp = client.delete(
        "/v1/config/models.default_model",
        headers={**auth(READER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code in (401, 403)


# --- J5: secret unset needs two-phase confirm (config-and-access §3.3) ---------
def _set_secret(client):
    from _helpers import patch_config
    r = patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": "sk-live-secret-XYZ"})
    assert r.status_code == 200


def test_secret_unset_requires_confirm_token(client, store):
    _set_secret(client)
    etag = get_etag(client, WRITER_TOKEN)
    # 第一次 DELETE(无 confirm)=> 409 plan 阶段,给 confirm_token,不删
    resp = client.delete(
        "/v1/config/models.ustc_api_key",
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 409
    body = resp.json()
    assert body["needs_confirm"] is True and body["confirm_token"]
    assert "不可逆" in body["warning"] and "sk-live-secret-XYZ" not in resp.text
    # secret 还在(没删)
    assert store.get("models.ustc_api_key") == "sk-live-secret-XYZ"
    # 带 confirm_token 再调 => 执行
    token = body["confirm_token"]
    etag2 = get_etag(client, WRITER_TOKEN)
    resp2 = client.delete(
        f"/v1/config/models.ustc_api_key?confirm={token}",
        headers={**auth(WRITER_TOKEN), "If-Match": etag2},
    )
    assert resp2.status_code == 200
    assert store.get("models.ustc_api_key") is None  # 已 unset


def test_secret_unset_stale_token_rejected(client, store):
    _set_secret(client)
    etag = get_etag(client, WRITER_TOKEN)
    plan = client.delete(
        "/v1/config/models.ustc_api_key",
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    ).json()
    token = plan["confirm_token"]
    # 在 plan 之后改了 secret(ETag 变)=> 旧 token 陈旧
    _helpers_patch(client, {"models.ustc_api_key": "sk-live-changed"})
    etag2 = get_etag(client, WRITER_TOKEN)
    resp = client.delete(
        f"/v1/config/models.ustc_api_key?confirm={token}",
        headers={**auth(WRITER_TOKEN), "If-Match": etag2},
    )
    # 陈旧 token => 又回到 409 plan(给新 token),不误删
    assert resp.status_code == 409
    assert store.get("models.ustc_api_key") == "sk-live-changed"


def _helpers_patch(client, changes):
    from _helpers import patch_config
    assert patch_config(client, ADMIN_TOKEN, changes).status_code == 200


def test_nonsecret_unset_unaffected_by_two_phase(client, store):
    # 非 secret 字段 unset 不受两段式影响(值落回下层可再 set)
    store._profile_layer["models.default_model"] = "profile-kimi"
    from _helpers import patch_config
    patch_config(client, ADMIN_TOKEN, {"models.default_model": "store-ds"})
    etag = get_etag(client, WRITER_TOKEN)
    resp = client.delete(
        "/v1/config/models.default_model",
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    )
    assert resp.status_code == 200  # 一步完成,无需 confirm
    assert store.get("models.default_model") == "profile-kimi"
