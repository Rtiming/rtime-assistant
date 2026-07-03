# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K1 module manifest 加载/校验/doctor + 真实 deploy/modules.json 对账。"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from rtime_admin_core.modules import (
    KINDS,
    Module,
    load_manifest,
    manifest_report,
    validate_manifest,
)
from rtime_admin_core.registry import KNOWN_MODULE_NAMES

ROOT = Path(__file__).resolve().parents[3]
MANIFEST = ROOT / "deploy" / "modules.json"


def test_load_and_from_dict():
    m = Module.from_dict({"id": "x", "kind": "channel", "title": "X", "compose_profile": "x"})
    assert m.id == "x" and m.optional is True and m.hot_pluggable == "restart"


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError, match="重复"):
        load_manifest(json.dumps({"schema_version": 1, "modules": [
            {"id": "a", "kind": "core"}, {"id": "a", "kind": "core"},
        ]}))


def test_bad_shape_rejected():
    with pytest.raises(ValueError):
        load_manifest(json.dumps({"no_modules": 1}))


def test_validate_catches_bad_refs():
    mods = [
        Module.from_dict({"id": "a", "kind": "channel", "compose_profile": "ghost",
                          "config_module": "nope", "depends_on": ["missing"], "docs": "no/such.md"}),
        Module.from_dict({"id": "b", "kind": "weird", "hot_pluggable": "maybe"}),
    ]
    issues = validate_manifest(
        mods, known_config_modules={"qq"}, known_profiles={"qq"},
        docs_exists=lambda rel: False,
    )
    codes = {i["code"] for i in issues}
    assert {"compose_profile_missing", "config_module_unknown", "dep_missing",
            "docs_missing", "bad_kind", "bad_hot_pluggable"} <= codes


def test_report_installed_by_profile():
    mods = [
        Module.from_dict({"id": "qq", "kind": "channel", "compose_profile": "qq", "optional": True}),
        Module.from_dict({"id": "core", "kind": "core", "optional": False}),
        Module.from_dict({"id": "web", "kind": "channel", "compose_profile": "web", "optional": True}),
    ]
    rep = manifest_report(mods, [], enabled_profiles={"qq"})
    by_id = {m["id"]: m for m in rep["modules"]}
    assert by_id["qq"]["installed"] is True
    assert by_id["web"]["installed"] is False
    assert by_id["core"]["installed"] is True  # 非 optional 恒装


# --- 真实 manifest 对账(这是活的地基,必须始终合法) ---
def test_real_manifest_loads_and_validates():
    mods = load_manifest(MANIFEST.read_text(encoding="utf-8"))
    assert len(mods) >= 10
    # 所有 config_module 真在 registry;所有 kind 合法;docs 文件存在
    issues = validate_manifest(
        mods,
        known_config_modules=set(KNOWN_MODULE_NAMES),
        known_profiles={"qq", "web"},  # compose.prod.yml 的真实 profiles
        docs_exists=lambda rel: (ROOT / rel).exists(),
    )
    # docs_missing 允许(有的指向尚未建的文档);但引用错误(config/kind/dep)必须为空
    hard = [i for i in issues if i["code"] != "docs_missing"]
    assert hard == [], hard
    # 每个模块 kind 合法
    assert all(m.kind in KINDS for m in mods)


def test_real_manifest_data_paths_documented():
    # 碰数据的模块必须声明 data_paths(数据永在仓库外的可审计性)
    mods = load_manifest(MANIFEST.read_text(encoding="utf-8"))
    gateway = next(m for m in mods if m.id == "gateway-core")
    assert any("brain" in p for p in gateway.data_paths)
