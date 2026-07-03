# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T5b config migration — behaviour-preservation for the pydantic-settings model.

Guards that the schema-driven ``WebChatConfig`` (rtime-config base + config_field)
is a drop-in for the old frozen dataclass:

  1. every field's default == the legacy default (parametrized table);
  2. legacy env names still load the value (compat shim never regresses);
  3. from_env clean-env output == the legacy from_env computed defaults;
  4. every field carries rtime metadata (x-env-aliases + x-reload) so the panel /
     docs can render it (T5b coverage lane);
  5. it registers into the admin-core registry as module ``web-chat``.

Legacy defaults below are copied verbatim from the pre-migration dataclass field
declarations. Changing a default is a deliberate config change: update the table.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from web_chat.config import WebChatConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "config" / "web-chat.md"

# --- (1) field default parity: legacy dataclass defaults, verbatim -------------
LEGACY_DEFAULTS = {
    "bind": "127.0.0.1",
    "port": 8788,
    "state_dir": "",
    "claude_cli": "",
    "model": "",
    "permission_mode": "default",
    "default_cwd": "",
    "mcp_config": '{"mcpServers": {}}',
    "archive_root": None,
    "archive_mode": "events",
    "show_tool_calls": False,
    "run_timeout_seconds": 600.0,
    "read_only": False,
    "log_level": "INFO",
}


@pytest.mark.parametrize("field,expected", sorted(LEGACY_DEFAULTS.items()))
def test_field_default_matches_legacy(field, expected, monkeypatch):
    # a clean env so pydantic-settings doesn't pick up a stray alias from the shell.
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    cfg = WebChatConfig()
    assert getattr(cfg, field) == expected


def test_no_field_default_drift():
    """The table covers exactly the model's fields (a new field must be added here)."""
    assert set(LEGACY_DEFAULTS) == set(WebChatConfig.model_fields)


# --- (2) legacy env names still load via the model ----------------------------
LEGACY_ENV = [
    ("WEB_CHAT_BIND", "0.0.0.0", "bind", "0.0.0.0"),
    ("WEB_CHAT_PORT", "9999", "port", 9999),
    ("RTIME_WEB_CHAT_STATE_DIR", "/tmp/ws", "state_dir", "/tmp/ws"),
    ("CLAUDE_CLI_PATH", "/x/claude", "claude_cli", "/x/claude"),
    ("DEFAULT_MODEL", "opus", "model", "opus"),
    ("PERMISSION_MODE", "dontAsk", "permission_mode", "dontAsk"),
    ("DEFAULT_CWD", "/srv", "default_cwd", "/srv"),
    (
        "WEB_CHAT_MCP_CONFIG",
        '{"mcpServers": {"x": {}}}',
        "mcp_config",
        '{"mcpServers": {"x": {}}}',
    ),
    ("WEB_CHAT_SHOW_TOOL_CALLS", "1", "show_tool_calls", True),
    ("WEB_CHAT_RUN_TIMEOUT_SECONDS", "42", "run_timeout_seconds", 42.0),
    ("WEB_CHAT_READ_ONLY", "1", "read_only", True),
    ("WEB_CHAT_LOG_LEVEL", "DEBUG", "log_level", "DEBUG"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_legacy_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    cfg = WebChatConfig()
    assert getattr(cfg, field) == expected


def test_read_only_env_zero_is_false(monkeypatch):
    """WEB_CHAT_READ_ONLY=0 -> read_only False (only '1' is truthy; matches QQ)."""
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("WEB_CHAT_READ_ONLY", "0")
    assert WebChatConfig().read_only is False


# --- (3) from_env clean-env parity with the legacy computed defaults -----------
def test_from_env_clean_env_matches_legacy_computed(monkeypatch):
    for alias in _all_env_aliases() + ["WEB_CHAT_DEBUG", "WEB_CHAT_MCP_CONFIG"]:
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setattr("shutil.which", lambda _name: "")  # no claude on PATH
    cfg = WebChatConfig.from_env()
    assert cfg.bind == "127.0.0.1"
    assert cfg.port == 8788
    # from_env expands the state-dir default (differs from the bare field default "").
    assert cfg.state_dir.endswith("rtime-assistant/web-chat")
    assert cfg.claude_cli == ""
    assert cfg.mcp_config == '{"mcpServers": {}}'
    assert cfg.read_only is False
    assert cfg.model_enabled is False


def test_from_env_debug_forces_debug_level(monkeypatch):
    for alias in _all_env_aliases() + ["WEB_CHAT_DEBUG"]:
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("WEB_CHAT_DEBUG", "1")
    assert WebChatConfig.from_env().log_level == "DEBUG"


# --- (4) rtime metadata: x-env-aliases + x-reload on every field --------------
def test_every_field_carries_env_aliases_and_reload():
    schema = WebChatConfig.model_json_schema(by_alias=False)
    props = schema["properties"]
    assert set(props) == set(WebChatConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"


def test_hot_reload_fields_marked():
    """model is hot-reloadable; read_only is restart (security from strict)."""
    props = WebChatConfig.model_json_schema(by_alias=False)["properties"]
    assert props["model"]["x-reload"] == "hot"
    assert props["read_only"]["x-reload"] == "restart"


# --- (5) admin-core registry registration -------------------------------------
def test_registers_into_admin_core_registry():
    from rtime_admin_core import default_registry, register_web_chat_module

    reg = default_registry(include_web_chat=True)
    assert reg.has("web-chat")
    assert reg.model("web-chat") is WebChatConfig
    # the standalone helper also works on a bare registry.
    from rtime_admin_core import Registry

    bare = Registry()
    register_web_chat_module(bare)
    assert bare.has("web-chat")


def test_registry_schema_exposes_metadata():
    from rtime_admin_core import default_registry

    reg = default_registry(include_web_chat=True)
    schema = reg.get_schema("web-chat")
    assert schema["properties"]["port"]["x-env-aliases"] == ["WEB_CHAT_PORT"]


# --- (6) generated config doc stays in lockstep with the model ----------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "web_chat.config:WebChatConfig",
            "--title",
            "web-chat 配置项",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT / "apps" / "web-chat"),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "web_chat.config:WebChatConfig --title 'web-chat 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/web-chat.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "web_chat.config:WebChatConfig --title 'web-chat 配置项' "
        f"--out {DOC_PATH}"
    )


# --- helpers ------------------------------------------------------------------
def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    for prop in WebChatConfig.model_json_schema(by_alias=False)["properties"].values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases
