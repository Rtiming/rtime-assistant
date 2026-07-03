# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""load_api_keys: file parsing, sanity checks, and the shipped example file."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from rtime_admin_api import load_api_keys
from rtime_admin_api.auth import MIN_KEY_LENGTH

EXAMPLE = Path(__file__).resolve().parents[1] / "keys.example.json"


def _write(tmp_path: Path, data) -> Path:
    p = tmp_path / "keys.json"
    p.write_text(json.dumps(data), encoding="utf-8")
    return p


def _entry(**over):
    base = {
        "name": "ops-agent",
        "key": "long-enough-test-key-000000",
        "scopes": ["read", "write"],
    }
    base.update(over)
    return base


def test_example_file_parses_and_is_obviously_fake():
    keys = load_api_keys(EXAMPLE)
    assert {k.name for k in keys} == {
        "ops-agent", "owner-admin", "readonly-probe", "old-agent-revoked",
    }
    for k in keys:
        assert "EXAMPLE-ONLY-FAKE-VALUE" in k.key  # nobody ships these as real
    admin = next(k for k in keys if k.name == "owner-admin")
    assert admin.has_scope("read:sensitive")
    assert admin.is_platform_super is True  # J3: owner=平台超管
    ops = next(k for k in keys if k.name == "ops-agent")
    assert ops.project_roles == {"studentunion": "admin"}
    probe = next(k for k in keys if k.name == "readonly-probe")
    assert probe.scopes == frozenset({"read"})
    # J4: example demonstrates TTL + revocation
    assert probe.expires_at == "2026-12-31T00:00:00Z"
    assert next(k for k in keys if k.name == "old-agent-revoked").revoked is True


def test_missing_file_raises(tmp_path):
    with pytest.raises(ValueError, match="not found"):
        load_api_keys(tmp_path / "nope.json")


def test_invalid_json_raises(tmp_path):
    p = tmp_path / "keys.json"
    p.write_text("{not json", encoding="utf-8")
    with pytest.raises(ValueError, match="not valid JSON"):
        load_api_keys(p)


def test_empty_list_raises(tmp_path):
    with pytest.raises(ValueError, match="non-empty"):
        load_api_keys(_write(tmp_path, []))


def test_non_object_entry_raises(tmp_path):
    with pytest.raises(ValueError, match="must be an object"):
        load_api_keys(_write(tmp_path, ["just-a-string"]))


def test_short_key_rejected(tmp_path):
    path = _write(tmp_path, [_entry(key="x" * (MIN_KEY_LENGTH - 1))])
    with pytest.raises(ValueError, match="at least"):
        load_api_keys(path)


def test_empty_name_rejected(tmp_path):
    with pytest.raises(ValueError, match="'name'"):
        load_api_keys(_write(tmp_path, [_entry(name="  ")]))


def test_bad_scopes_rejected(tmp_path):
    with pytest.raises(ValueError, match="'scopes'"):
        load_api_keys(_write(tmp_path, [_entry(scopes="read")]))
    with pytest.raises(ValueError, match="'scopes'"):
        load_api_keys(_write(tmp_path, [_entry(scopes=["read", ""])]))


def test_duplicate_names_rejected(tmp_path):
    path = _write(
        tmp_path,
        [_entry(), _entry(key="another-long-test-key-11111")],
    )
    with pytest.raises(ValueError, match="duplicate key name"):
        load_api_keys(path)


def test_duplicate_key_values_rejected(tmp_path):
    path = _write(tmp_path, [_entry(), _entry(name="other")])
    with pytest.raises(ValueError, match="duplicate key value"):
        load_api_keys(path)


# --- J4: token TTL + revocation (config-and-access §3.2) -----------------------
def test_load_optional_expires_at_and_revoked(tmp_path):
    p = _write(tmp_path, [
        _entry(name="a", key="long-enough-test-key-aaaaaa", expires_at="2026-12-31T00:00:00Z"),
        _entry(name="b", key="long-enough-test-key-bbbbbb", revoked=True),
        _entry(name="c", key="long-enough-test-key-cccccc"),  # no ttl = live forever
    ])
    keys = {k.name: k for k in load_api_keys(p)}
    assert keys["a"].expires_at == "2026-12-31T00:00:00Z" and keys["a"].revoked is False
    assert keys["b"].revoked is True
    assert keys["c"].expires_at is None and keys["c"].revoked is False


def test_bad_expires_at_and_revoked_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_api_keys(_write(tmp_path, [_entry(expires_at=123)]))
    with pytest.raises(ValueError):
        load_api_keys(_write(tmp_path, [_entry(revoked="yes")]))


def test_authenticate_rejects_expired_and_revoked():
    from rtime_admin_api.auth import ApiKey, authenticate
    from rtime_admin_api.errors import ApiError

    live = ApiKey("live", "K" * 26, frozenset({"read"}))
    expiring = ApiKey("exp", "E" * 26, frozenset({"read"}), expires_at="2026-07-01T00:00:00Z")
    revoked = ApiKey("rev", "R" * 26, frozenset({"read"}), revoked=True)
    now = "2026-07-04T00:00:00Z"
    keys = [live, expiring, revoked]
    # live token OK
    assert authenticate("Bearer " + "K" * 26, keys, now_iso=now).name == "live"
    # expired -> 401
    with pytest.raises(ApiError) as e1:
        authenticate("Bearer " + "E" * 26, keys, now_iso=now)
    assert e1.value.status == 401 and "expired" in e1.value.message
    # revoked -> 401 (time-independent)
    with pytest.raises(ApiError) as e2:
        authenticate("Bearer " + "R" * 26, keys, now_iso=now)
    assert "revoked" in e2.value.message
    # without now_iso: expiry not enforced (backward compat), but revoked still is
    assert authenticate("Bearer " + "E" * 26, keys).name == "exp"
    with pytest.raises(ApiError):
        authenticate("Bearer " + "R" * 26, keys)


def test_not_yet_expired_token_ok():
    from rtime_admin_api.auth import ApiKey, authenticate

    future = ApiKey("f", "F" * 26, frozenset({"read"}), expires_at="2026-12-31T00:00:00Z")
    assert authenticate("Bearer " + "F" * 26, [future], now_iso="2026-07-04T00:00:00Z").name == "f"


# --- J3: RBAC identity on tokens (config-and-access §一) -----------------------
def test_load_platform_super_and_project_roles(tmp_path):
    p = _write(tmp_path, [
        _entry(name="owner", key="long-enough-test-key-owner0", is_platform_super=True),
        _entry(name="stu-lead", key="long-enough-test-key-stu000",
               project_roles={"studentunion": "admin"}),
        _entry(name="plain", key="long-enough-test-key-plain0"),  # no rbac = non-super
    ])
    keys = {k.name: k for k in load_api_keys(p)}
    assert keys["owner"].is_platform_super is True
    assert keys["stu-lead"].project_roles == {"studentunion": "admin"}
    assert keys["plain"].is_platform_super is False and keys["plain"].project_roles == {}


def test_bad_rbac_fields_rejected(tmp_path):
    with pytest.raises(ValueError):
        load_api_keys(_write(tmp_path, [_entry(is_platform_super="yes")]))
    with pytest.raises(ValueError, match="not a valid role"):
        load_api_keys(_write(tmp_path, [_entry(project_roles={"p": "superuser"})]))
    with pytest.raises(ValueError):
        load_api_keys(_write(tmp_path, [_entry(project_roles=["not", "a", "dict"])]))


def test_principal_mapping_and_require_capability():
    from rtime_admin_api.auth import ApiKey, require_capability
    from rtime_admin_core.rbac import Capability
    from rtime_admin_api.errors import ApiError

    owner = ApiKey("owner", "K" * 26, frozenset({"read"}), is_platform_super=True)
    stu = ApiKey("stu", "S" * 26, frozenset({"read"}), project_roles={"studentunion": "admin"})
    # 平台超管能行使超管独占能力
    require_capability(owner, Capability.PLATFORM_ISSUE_TOKEN)  # 不抛
    # 项目 admin 够不到超管独占
    with pytest.raises(ApiError) as e:
        require_capability(stu, Capability.PLATFORM_ISSUE_TOKEN)
    assert e.value.status == 403 and "issue_token" in e.value.message
    # 项目 admin 在其 project 内能直接写
    require_capability(stu, Capability.WRITE_DIRECT, project="studentunion")
    # 但对别的 project 不行
    with pytest.raises(ApiError):
        require_capability(stu, Capability.WRITE_DIRECT, project="consumption")
