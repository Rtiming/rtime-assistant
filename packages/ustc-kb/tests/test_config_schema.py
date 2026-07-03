# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""批 3 config 收编 — schema for the ustc-kb crawler.

Guards that ``UstcKbConfig`` (rtime-config base + config_field):

  1. every field default == the crawler's ``os.environ.get`` default (parametrized),
     since the crawler (ustc_kb.config / ustc_kb.crawl) reads env directly and this
     schema only MIRRORS those defaults for the panel — a drift misreports, not
     changes, the crawler;
  2. every USTC_KB_* env name loads via the model;
  3. every field carries rtime metadata (x-env-aliases + x-reload + x-scope);
  4. it registers into the admin-core registry as module ``ustc-kb``.

Crawler defaults below are copied verbatim from the ``os.environ.get(..., DEFAULT)``
calls in ustc_kb/config.py (DATA_ROOT via ``~/Desktop/ustc-kb-data``, TODAY) and
ustc_kb/crawl.py (DEFAULT_WORKERS). Keep in lockstep with those.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from ustc_kb.config_schema import UstcKbConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "config" / "ustc-kb.md"

CRAWLER_DEFAULTS = {
    "data_root": "~/Desktop/ustc-kb-data",
    "workers": 8,
    "today": "2026-06-20",
}


def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    schema = UstcKbConfig.model_json_schema(by_alias=False)
    for prop in schema["properties"].values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases


@pytest.mark.parametrize("field,expected", sorted(CRAWLER_DEFAULTS.items()))
def test_field_default_matches_crawler(field, expected, monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    cfg = UstcKbConfig()
    assert getattr(cfg, field) == expected


def test_no_field_default_drift():
    assert set(CRAWLER_DEFAULTS) == set(UstcKbConfig.model_fields)


LEGACY_ENV = [
    ("USTC_KB_DATA", "/data/x", "data_root", "/data/x"),
    ("USTC_KB_WORKERS", "16", "workers", 16),
    ("USTC_KB_TODAY", "2027-01-01", "today", "2027-01-01"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    cfg = UstcKbConfig()
    assert getattr(cfg, field) == expected


def test_every_field_carries_metadata():
    props = UstcKbConfig.model_json_schema(by_alias=False)["properties"]
    assert set(props) == set(UstcKbConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"
        assert prop.get("x-scope") == "write:library", f"{name} missing/wrong x-scope"
        # no secrets: the login password is entered interactively, never an env.
        assert prop.get("x-secret") is not True


def test_registers_into_admin_core_registry():
    from rtime_admin_core import (
        Registry,
        default_registry,
        register_ustc_kb_module,
    )

    reg = default_registry(include_ustc_kb=True)
    assert reg.has("ustc-kb")
    assert reg.model("ustc-kb") is UstcKbConfig
    bare = Registry()
    register_ustc_kb_module(bare)
    assert bare.has("ustc-kb")


# --- generated config doc stays in lockstep with the model --------------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "ustc_kb.config_schema:UstcKbConfig",
            "--title",
            "ustc-kb 配置项",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "ustc_kb.config_schema:UstcKbConfig --title 'ustc-kb 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/ustc-kb.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "ustc_kb.config_schema:UstcKbConfig --title 'ustc-kb 配置项' "
        f"--out {DOC_PATH}"
    )
