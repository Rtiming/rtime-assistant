# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""批 3 config 收编 — schema for the qq-selfheal ops sidecar.

Guards that ``QQSelfhealConfig`` (rtime-config base + config_field/secret_field):

  1. every field default == the daemon's ``os.getenv`` default (parametrized table),
     since the daemon (ops/qq_selfheal.py) reads env directly and this schema only
     MIRRORS those defaults for the panel — a drift would misreport, not change,
     runtime;
  2. every SELFHEAL_* env name loads via the model (the accepted surface);
  3. the owner open_id is x-secret (PII never rendered plaintext);
  4. every field carries rtime metadata (x-env-aliases + x-reload + x-scope);
  5. it registers into the admin-core registry as module ``qq-selfheal``.

The daemon defaults below are copied verbatim from ops/qq_selfheal.py's ``Config``
``os.getenv(..., DEFAULT)`` calls. Changing one is a deliberate config change: update
BOTH the daemon and this table (kept in lockstep on purpose).
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from qq_bridge.selfheal_config import QQSelfhealConfig

APP_ROOT = Path(__file__).resolve().parents[1]  # apps/qq-bridge
REPO_ROOT = APP_ROOT.parents[1]
DOC_PATH = REPO_ROOT / "docs" / "config" / "qq-selfheal.md"

# --- (1) field default parity with the stdlib daemon's os.getenv defaults ------
DAEMON_DEFAULTS = {
    "status_url": "http://127.0.0.1:3000/get_status",
    "napcat_container": "qqbr-napcat",
    "qr_in_container": "/app/napcat/cache/qrcode.png",
    "qr_host_tmp": "/tmp/qq-selfheal-qr.png",
    "poll_seconds": 60,
    "offline_confirm_seconds": 120,
    "cooldown_seconds": 900,
    "qr_wait_seconds": 45,
    "qr_fresh_seconds": 60,
    "qr_refresh_wait_seconds": 150,
    "qr_request_check_seconds": 4,
    "qr_request_file": "~/.local/state/rtime-assistant/qq-qr-request",
    "notify_queue_dir": "~/.local/state/rtime-assistant/notify-queue",
    "feishu_owner_open_id": "",
}


def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    schema = QQSelfhealConfig.model_json_schema(by_alias=False)
    for prop in schema["properties"].values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases


@pytest.mark.parametrize("field,expected", sorted(DAEMON_DEFAULTS.items()))
def test_field_default_matches_daemon(field, expected, monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    cfg = QQSelfhealConfig()
    assert getattr(cfg, field) == expected


def test_no_field_default_drift():
    """The table covers exactly the model's fields (a new field must be added here)."""
    assert set(DAEMON_DEFAULTS) == set(QQSelfhealConfig.model_fields)


# --- (2) each SELFHEAL_* / owner env name loads via the model ------------------
LEGACY_ENV = [
    ("SELFHEAL_STATUS_URL", "http://x:1/s", "status_url", "http://x:1/s"),
    ("SELFHEAL_NAPCAT_CONTAINER", "nc2", "napcat_container", "nc2"),
    ("SELFHEAL_QR_IN_CONTAINER", "/a/q.png", "qr_in_container", "/a/q.png"),
    ("SELFHEAL_QR_HOST_TMP", "/b/q.png", "qr_host_tmp", "/b/q.png"),
    ("SELFHEAL_POLL_SECONDS", "30", "poll_seconds", 30),
    ("SELFHEAL_OFFLINE_CONFIRM_SECONDS", "90", "offline_confirm_seconds", 90),
    ("SELFHEAL_COOLDOWN_SECONDS", "600", "cooldown_seconds", 600),
    ("SELFHEAL_QR_WAIT_SECONDS", "20", "qr_wait_seconds", 20),
    ("SELFHEAL_QR_FRESH_SECONDS", "30", "qr_fresh_seconds", 30),
    ("SELFHEAL_QR_REFRESH_WAIT_SECONDS", "99", "qr_refresh_wait_seconds", 99),
    ("SELFHEAL_QR_REQUEST_CHECK_SECONDS", "2", "qr_request_check_seconds", 2),
    ("SELFHEAL_QR_REQUEST_FILE", "/c/req", "qr_request_file", "/c/req"),
    ("SELFHEAL_NOTIFY_QUEUE_DIR", "/c/nq", "notify_queue_dir", "/c/nq"),
    ("FEISHU_OWNER_OPEN_ID", "ou_abc", "feishu_owner_open_id", "ou_abc"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    cfg = QQSelfhealConfig()
    assert getattr(cfg, field) == expected


# --- (3) owner open_id is x-secret (PII) ---------------------------------------
def test_owner_open_id_is_secret():
    props = QQSelfhealConfig.model_json_schema(by_alias=False)["properties"]
    assert props["feishu_owner_open_id"].get("x-secret") is True


# --- (4) rtime metadata on every field -----------------------------------------
def test_every_field_carries_metadata():
    props = QQSelfhealConfig.model_json_schema(by_alias=False)["properties"]
    assert set(props) == set(QQSelfhealConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"
        assert prop.get("x-scope") == "write:channel", f"{name} missing/wrong x-scope"


def test_hot_reload_fields_marked():
    """poll/qr-check are hot (loop-read); the rest are restart-level."""
    props = QQSelfhealConfig.model_json_schema(by_alias=False)["properties"]
    assert props["poll_seconds"]["x-reload"] == "hot"
    assert props["qr_request_check_seconds"]["x-reload"] == "hot"
    assert props["status_url"]["x-reload"] == "restart"


# --- (5) admin-core registry registration --------------------------------------
def test_registers_into_admin_core_registry():
    from rtime_admin_core import (
        Registry,
        default_registry,
        register_qq_selfheal_module,
    )

    reg = default_registry(include_qq_selfheal=True)
    assert reg.has("qq-selfheal")
    assert reg.model("qq-selfheal") is QQSelfhealConfig
    bare = Registry()
    register_qq_selfheal_module(bare)
    assert bare.has("qq-selfheal")


# --- (6) generated config doc stays in lockstep with the model ----------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "qq_bridge.selfheal_config:QQSelfhealConfig",
            "--title",
            "qq-selfheal 配置项",
        ],
        capture_output=True,
        text=True,
        cwd=str(APP_ROOT),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "qq_bridge.selfheal_config:QQSelfhealConfig --title 'qq-selfheal 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/qq-selfheal.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "qq_bridge.selfheal_config:QQSelfhealConfig --title 'qq-selfheal 配置项' "
        f"--out {DOC_PATH}"
    )
