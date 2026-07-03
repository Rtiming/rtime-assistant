# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""P2 config 收编 (批 1 · Lane A) — behaviour-preservation for the settings model.

Guards that the schema-driven ``FeishuBridgeConfig`` (rtime-config base +
config_field, apps/feishu-bridge/feishu_config.py) is a drop-in for the old
``bot_config`` module-level ``os.getenv`` block, mirroring the qq-bridge pilot
(apps/qq-bridge/tests/test_config_schema.py) and web-chat:

  1. every field's default == the legacy default (parametrized table);
  2. no field-default drift (the table covers exactly the model's fields);
  3. legacy env names still load the value (compat shim never regresses);
  4. from_env clean-env output == the legacy computed defaults (CLI PATH lookup,
     ~ expansion, admin->allowed fallback);
  5. every field carries rtime metadata (x-env-aliases + x-reload); secrets marked;
  6. it registers into the admin-core registry as module ``feishu``;
  7. the generated docs/config/feishu.md stays in lockstep with the model (golden).

Legacy defaults below are copied verbatim from the pre-migration ``bot_config``
constants. Changing a default is a deliberate config change: update the table AND
note it in the PR — the test makes it loud.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from feishu_config import FeishuBridgeConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_SRC = REPO_ROOT / "packages" / "rtime-config" / "src"
APP_ROOT = REPO_ROOT / "apps" / "feishu-bridge"
DOC_PATH = REPO_ROOT / "docs" / "config" / "feishu.md"

# --- (1) field default parity: legacy bot_config constants, verbatim -----------
LEGACY_DEFAULTS = {
    "app_id": None,
    "app_secret": None,
    "config_json": "~/.config/rtime-assistant/feishu.json",
    "claude_cli": "claude",
    "model": "",
    "default_cwd": "~",
    "permission_mode": "default",
    "mcp_config": None,
    "archive_root": None,
    "archive_mode": "events",
    "model_aliases_json": "",
    "allowed_users": frozenset(),
    "allowed_chats": frozenset(),
    "admin_users": frozenset(),
    "require_mention_in_group": True,
    "owner_personal_library_access": False,
    "sessions_dir": "~/.feishu-claude",
    "callback_port": 9981,
    "stream_chunk_size": 20,
    "message_debounce_seconds": 0.0,
    "message_debounce_max_messages": 20,
    "message_debounce_max_chars": 12000,
    "status_heartbeat_seconds": 6.0,
    "output_style": "segmented",
    "show_tool_calls": False,
    "outbound_attachment_max_bytes": 30 * 1024 * 1024,
    "handover_model": "claude-opus-4-6",
    "watchdog_max_uptime_seconds": float(4 * 3600),
    "ngrok_domain": "",
}


def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    props = FeishuBridgeConfig.model_json_schema(by_alias=False)["properties"]
    for prop in props.values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases


@pytest.mark.parametrize("field,expected", sorted(LEGACY_DEFAULTS.items()))
def test_field_default_matches_legacy(field, expected, monkeypatch):
    # a clean env so pydantic-settings doesn't pick up a stray alias from the shell.
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    cfg = FeishuBridgeConfig()
    assert getattr(cfg, field) == expected


def test_no_field_default_drift():
    """The declared-default set must be exactly the model's fields (no field added
    without recording its legacy default)."""
    model_fields = set(FeishuBridgeConfig.model_fields)
    assert model_fields == set(LEGACY_DEFAULTS), (
        "field set drifted; update LEGACY_DEFAULTS: "
        f"{model_fields ^ set(LEGACY_DEFAULTS)}"
    )


# --- (2) legacy env names still load via the model ----------------------------
LEGACY_ENV = [
    ("FEISHU_APP_ID", "app-123", "app_id", "app-123"),
    ("FEISHU_APP_SECRET", "sekret", "app_secret", "sekret"),
    ("FEISHU_CONFIG_JSON", "/etc/feishu.json", "config_json", "/etc/feishu.json"),
    ("CLAUDE_CLI_PATH", "/x/claude", "claude_cli", "/x/claude"),
    ("DEFAULT_MODEL", "kimi-code", "model", "kimi-code"),
    ("DEFAULT_CWD", "/w", "default_cwd", "/w"),
    ("PERMISSION_MODE", "acceptEdits", "permission_mode", "acceptEdits"),
    (
        "FEISHU_MCP_CONFIG",
        '{"mcpServers":{"x":{}}}',
        "mcp_config",
        '{"mcpServers":{"x":{}}}',
    ),
    ("MODEL_ALIASES_JSON", '{"kimi":""}', "model_aliases_json", '{"kimi":""}'),
    ("ALLOWED_USERS", "ou_a, ou_b", "allowed_users", frozenset({"ou_a", "ou_b"})),
    ("ALLOWED_CHATS", "oc_1,oc_2", "allowed_chats", frozenset({"oc_1", "oc_2"})),
    ("ADMIN_USERS", "ou_admin", "admin_users", frozenset({"ou_admin"})),
    ("REQUIRE_MENTION_IN_GROUP", "0", "require_mention_in_group", False),
    (
        "FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS",
        "1",
        "owner_personal_library_access",
        True,
    ),
    ("FEISHU_SESSIONS_DIR", "/s", "sessions_dir", "/s"),
    ("CALLBACK_PORT", "8000", "callback_port", 8000),
    ("STREAM_CHUNK_SIZE", "50", "stream_chunk_size", 50),
    ("MESSAGE_DEBOUNCE_SECONDS", "1.5", "message_debounce_seconds", 1.5),
    ("MESSAGE_DEBOUNCE_MAX_MESSAGES", "5", "message_debounce_max_messages", 5),
    ("MESSAGE_DEBOUNCE_MAX_CHARS", "99", "message_debounce_max_chars", 99),
    ("STATUS_HEARTBEAT_SECONDS", "3", "status_heartbeat_seconds", 3.0),
    ("OUTPUT_STYLE", "raw", "output_style", "raw"),
    ("SHOW_TOOL_CALLS", "1", "show_tool_calls", True),
    (
        "FEISHU_OUTBOUND_ATTACHMENT_MAX_BYTES",
        "1024",
        "outbound_attachment_max_bytes",
        1024,
    ),
    ("CLAUDE_MODEL", "claude-opus-4-8", "handover_model", "claude-opus-4-8"),
    (
        "WATCHDOG_MAX_UPTIME_SECONDS",
        "7200",
        "watchdog_max_uptime_seconds",
        7200.0,
    ),
    ("NGROK_DOMAIN", "x.ngrok.io", "ngrok_domain", "x.ngrok.io"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_legacy_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    cfg = FeishuBridgeConfig()
    assert getattr(cfg, field) == expected


def test_implicit_prefix_name_not_accepted(monkeypatch):
    # env_prefix="" => only declared aliases load; a prefix-guess must NOT leak.
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("FEISHU_MODEL", "leak")
    monkeypatch.setenv("FEISHU_CALLBACK_PORT", "1234")
    cfg = FeishuBridgeConfig()
    assert cfg.model == ""
    assert cfg.callback_port == 9981


# --- (3) from_env clean-env parity with the legacy computed defaults -----------
def test_from_env_clean_env_matches_legacy_computed(monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: None)  # no claude on PATH
    cfg = FeishuBridgeConfig.from_env()
    # legacy from_env values that differ from the plain field default:
    assert cfg.claude_cli == "claude"  # PATH miss => literal 'claude'
    assert cfg.default_cwd == os.path.expanduser("~")  # ~ expanded
    assert cfg.sessions_dir == os.path.expanduser("~/.feishu-claude")  # ~ expanded
    # admin_users defaults to allowed_users (both empty here).
    assert cfg.admin_users == frozenset()
    # everything else equals the plain field default table:
    skip = {"claude_cli", "default_cwd", "sessions_dir", "model"}
    for field, expected in LEGACY_DEFAULTS.items():
        if field in skip:
            continue
        assert getattr(cfg, field) == expected, field


def test_from_env_admin_defaults_to_allowed(monkeypatch):
    """ADMIN_USERS unset => admin_users == allowed_users (legacy `or set(...)`)."""
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("ALLOWED_USERS", "ou_x, ou_y")
    cfg = FeishuBridgeConfig.from_env()
    assert cfg.admin_users == frozenset({"ou_x", "ou_y"})


def test_from_env_watchdog_invalid_falls_back(monkeypatch):
    """WATCHDOG_MAX_UPTIME_SECONDS garbage => the 4h default (main.py tolerance)."""
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("WATCHDOG_MAX_UPTIME_SECONDS", "bad")
    assert FeishuBridgeConfig.from_env().watchdog_max_uptime_seconds == float(4 * 3600)


# --- (4) rtime metadata: x-env-aliases + x-reload; secrets marked -------------
def test_every_field_carries_env_aliases_and_reload():
    props = FeishuBridgeConfig.model_json_schema(by_alias=False)["properties"]
    assert set(props) == set(FeishuBridgeConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"


def test_secret_fields_marked():
    """The Feishu credentials are the only x-secret fields (redaction contract)."""
    props = FeishuBridgeConfig.model_json_schema(by_alias=False)["properties"]
    secrets = {name for name, prop in props.items() if prop.get("x-secret")}
    assert secrets == {"app_id", "app_secret"}


def test_hot_reload_fields_marked():
    """model + the id whitelists are hot; the rest restart-level."""
    props = FeishuBridgeConfig.model_json_schema(by_alias=False)["properties"]
    assert props["model"]["x-reload"] == "hot"
    for name in ("allowed_users", "allowed_chats", "admin_users"):
        assert props[name]["x-reload"] == "hot"
    assert props["callback_port"]["x-reload"] == "restart"


# --- (5) admin-core registry registration -------------------------------------
def test_registers_into_admin_core_registry():
    from rtime_admin_core import default_registry, register_feishu_module

    reg = default_registry(include_feishu=True)
    assert reg.has("feishu")
    assert reg.model("feishu") is FeishuBridgeConfig
    # the standalone helper also works on a bare registry.
    from rtime_admin_core import Registry

    bare = Registry()
    register_feishu_module(bare)
    assert bare.has("feishu")


def test_registry_schema_exposes_metadata():
    from rtime_admin_core import default_registry

    reg = default_registry(include_feishu=True)
    schema = reg.get_schema("feishu")
    assert schema["properties"]["callback_port"]["x-env-aliases"] == ["CALLBACK_PORT"]
    assert schema["properties"]["app_id"].get("x-secret") is True


# --- (6) generated config doc golden ------------------------------------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "feishu_config:FeishuBridgeConfig",
            "--title",
            "feishu-bridge 配置项",
        ],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": f"{CONFIG_SRC}{os.pathsep}{APP_ROOT}",
        },
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "feishu_config:FeishuBridgeConfig --title 'feishu-bridge 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/feishu.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "feishu_config:FeishuBridgeConfig --title 'feishu-bridge 配置项' "
        f"--out {DOC_PATH}"
    )
