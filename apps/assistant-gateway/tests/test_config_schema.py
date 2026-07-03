# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""P2 config 收编 (批 2) — behaviour-preservation for the settings model.

Guards that the schema-driven ``AssistantGatewayConfig`` (rtime-config base +
config_field, apps/assistant-gateway/gateway_config_schema.py) is a drop-in for the
old ``gateway_config.load_config`` ``os.environ`` block, mirroring the feishu-bridge
pilot (apps/feishu-bridge/tests/test_config_schema.py):

  1. every field's schema default == the recorded default (parametrized table);
  2. no field-default drift (the table covers exactly the model's fields);
  3. legacy env names still load the value (compat shim never regresses);
  4. from_env clean-env output == the recorded values; the ``load_config`` adapter
     rebuilds the exact historical dict (keys, Path typing, base-url fallbacks);
  5. every field carries rtime metadata (x-env-aliases + x-reload); no secrets;
  6. it registers into the admin-core registry as module ``assistant-gateway``;
  7. the generated docs/config/assistant-gateway.md stays in lockstep (golden).

Defaults below are the schema field defaults; path-shaped fields use an empty
sentinel that ``load_config`` resolves against Path.home()/brain_root/log_dir (that
resolution is asserted separately in ``test_load_config_*``). Changing a default is
a deliberate config change: update the table AND note it in the PR.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest
from gateway_config_schema import AssistantGatewayConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_SRC = REPO_ROOT / "packages" / "rtime-config" / "src"
MODELS_SRC = REPO_ROOT / "packages" / "rtime-models" / "src"
APP_ROOT = REPO_ROOT / "apps" / "assistant-gateway"
DOC_PATH = REPO_ROOT / "docs" / "config" / "assistant-gateway.md"

# --- (1) field default parity: recorded schema defaults ------------------------
# Path-shaped fields default to "" (empty) in the schema and are resolved in
# load_config(); their resolution is covered by test_load_config_clean_env.
LEGACY_DEFAULTS = {
    "bind": "127.0.0.1",
    "port": 8765,
    "brain_root": "/mnt/brain",
    "index_pythonpath": "",
    "index_db": "",
    "log_dir": "",
    "claude_bin": "",
    "claude_timeout": 110,
    "claude_max_turns": "",
    "claude_investigation_timeout": 180,
    "claude_web_timeout": 170,
    "claude_runtime_diag_timeout": 90,
    "claude_bare": True,
    "claude_no_session_persistence": True,
    "claude_exclude_dynamic_sections": True,
    "claude_permission_mode": "dontAsk",
    "approval_forwarding_enabled": True,
    "gateway_access_mode": "readonly",
    "web_tools_enabled": True,
    "extra_allowed_tools": "",
    "memory_capture_enabled": False,
    "memory_failed_query_log_enabled": False,
    "memory_capture_max_chars": 800,
    "memory_injection_enabled": True,
    "memory_root": "",
    "memory_injection_max_cards": 3,
    "memory_injection_max_chars": 1200,
    "memory_access_log_enabled": True,
    "context_sources_enabled": True,
    "context_sources_path": "",
    "context_sources_max_items": 3,
    "context_sources_max_chars": 5000,
    "memory_candidate_write_enabled": True,
    "memory_candidate_review_dir": "",
    "relations_path": "",
    "related_prefetch_limit": 5,
    "related_prefetch_max_chars": 1200,
    "queue_max": 2,
    "queue_wait_timeout": 30.0,
    "queue_heartbeat_secs": 3.0,
    "prepare_cache_ttl": 180,
    "prepare_cache_max": 64,
    "prewarm_enabled": True,
    "live_prewarm_enabled": True,
    "live_prewarm_idle_seconds": 240,
    "prewarm_ttl_seconds": 240,
    "prewarm_timeout": 30,
    "history_max_chars": 4000,
    "stream_trace_enabled": True,
    "intake_max_mb": 64,
    "file_extract_max_files": 4,
    "file_extract_max_chars": 80000,
    "notify_target": "",
    "reminder_register": "",
    "model_catalog_path": "",
    "plugin_release_dir": "",
    "model_refresh_timeout": 8.0,
    "moonshot_base_url": "",
    "ustc_base_url": "",
    "ustc_api_key_file": "",
    "gateway_url": "http://127.0.0.1:8765",
}


def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    props = AssistantGatewayConfig.model_json_schema(by_alias=False)["properties"]
    for prop in props.values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases


@pytest.mark.parametrize("field,expected", sorted(LEGACY_DEFAULTS.items()))
def test_field_default_matches_legacy(field, expected, monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    cfg = AssistantGatewayConfig()
    assert getattr(cfg, field) == expected


def test_no_field_default_drift():
    """The declared-default set must be exactly the model's fields."""
    model_fields = set(AssistantGatewayConfig.model_fields)
    assert model_fields == set(LEGACY_DEFAULTS), (
        "field set drifted; update LEGACY_DEFAULTS: "
        f"{model_fields ^ set(LEGACY_DEFAULTS)}"
    )


# --- (2) legacy env names still load via the model -----------------------------
LEGACY_ENV = [
    ("GATEWAY_BIND", "192.0.2.8", "bind", "192.0.2.8"),
    ("GATEWAY_PORT", "9000", "port", 9000),
    ("BRAIN_ROOT", "/data/brain", "brain_root", "/data/brain"),
    ("INDEX_PYTHONPATH", "/x/src", "index_pythonpath", "/x/src"),
    ("INDEX_DB", "/x/db.sqlite", "index_db", "/x/db.sqlite"),
    ("GATEWAY_LOG_DIR", "/x/log", "log_dir", "/x/log"),
    ("CLAUDE_BIN", "/x/claude", "claude_bin", "/x/claude"),
    ("CLAUDE_TIMEOUT", "60", "claude_timeout", 60),
    ("CLAUDE_MAX_TURNS", "5", "claude_max_turns", "5"),
    ("CLAUDE_INVESTIGATION_TIMEOUT", "200", "claude_investigation_timeout", 200),
    ("CLAUDE_WEB_TIMEOUT", "150", "claude_web_timeout", 150),
    ("CLAUDE_RUNTIME_DIAG_TIMEOUT", "70", "claude_runtime_diag_timeout", 70),
    ("CLAUDE_BARE", "0", "claude_bare", False),
    ("CLAUDE_NO_SESSION_PERSISTENCE", "0", "claude_no_session_persistence", False),
    ("CLAUDE_EXCLUDE_DYNAMIC_SECTIONS", "0", "claude_exclude_dynamic_sections", False),
    ("CLAUDE_PERMISSION_MODE", "acceptEdits", "claude_permission_mode", "acceptEdits"),
    ("GATEWAY_APPROVAL_FORWARDING", "0", "approval_forwarding_enabled", False),
    ("GATEWAY_ACCESS_MODE", "full", "gateway_access_mode", "full"),
    ("GATEWAY_WEB_TOOLS_ENABLED", "0", "web_tools_enabled", False),
    ("GATEWAY_EXTRA_ALLOWED_TOOLS", "mcp__x__*", "extra_allowed_tools", "mcp__x__*"),
    ("MEMORY_CAPTURE_ENABLED", "1", "memory_capture_enabled", True),
    ("MEMORY_FAILED_QUERY_LOG_ENABLED", "1", "memory_failed_query_log_enabled", True),
    ("MEMORY_CAPTURE_MAX_CHARS", "999", "memory_capture_max_chars", 999),
    ("MEMORY_INJECTION_ENABLED", "0", "memory_injection_enabled", False),
    ("MEMORY_ROOT", "/x/mem", "memory_root", "/x/mem"),
    ("MEMORY_INJECTION_MAX_CARDS", "7", "memory_injection_max_cards", 7),
    ("MEMORY_INJECTION_MAX_CHARS", "2000", "memory_injection_max_chars", 2000),
    ("MEMORY_ACCESS_LOG_ENABLED", "0", "memory_access_log_enabled", False),
    ("GATEWAY_CONTEXT_SOURCES_ENABLED", "0", "context_sources_enabled", False),
    (
        "GATEWAY_CONTEXT_SOURCES_PATH",
        "/x/ctx.jsonl",
        "context_sources_path",
        "/x/ctx.jsonl",
    ),
    ("GATEWAY_CONTEXT_SOURCES_MAX_ITEMS", "9", "context_sources_max_items", 9),
    ("GATEWAY_CONTEXT_SOURCES_MAX_CHARS", "7000", "context_sources_max_chars", 7000),
    (
        "GATEWAY_MEMORY_CANDIDATE_WRITE_ENABLED",
        "0",
        "memory_candidate_write_enabled",
        False,
    ),
    (
        "GATEWAY_MEMORY_CANDIDATE_REVIEW_DIR",
        "/x/rq",
        "memory_candidate_review_dir",
        "/x/rq",
    ),
    ("GATEWAY_RELATIONS_PATH", "/x/rel.jsonl", "relations_path", "/x/rel.jsonl"),
    ("GATEWAY_RELATED_PREFETCH_LIMIT", "8", "related_prefetch_limit", 8),
    ("GATEWAY_RELATED_PREFETCH_MAX_CHARS", "2500", "related_prefetch_max_chars", 2500),
    ("QUEUE_MAX", "4", "queue_max", 4),
    ("QUEUE_WAIT_TIMEOUT", "45", "queue_wait_timeout", 45.0),
    ("QUEUE_HEARTBEAT_SECS", "2", "queue_heartbeat_secs", 2.0),
    ("GATEWAY_PREPARE_CACHE_TTL", "300", "prepare_cache_ttl", 300),
    ("GATEWAY_PREPARE_CACHE_MAX", "128", "prepare_cache_max", 128),
    ("GATEWAY_PREWARM_ENABLED", "0", "prewarm_enabled", False),
    ("GATEWAY_LIVE_PREWARM_ENABLED", "0", "live_prewarm_enabled", False),
    ("GATEWAY_LIVE_PREWARM_IDLE_SECONDS", "300", "live_prewarm_idle_seconds", 300),
    ("GATEWAY_PREWARM_TTL_SECONDS", "300", "prewarm_ttl_seconds", 300),
    ("GATEWAY_PREWARM_TIMEOUT", "45", "prewarm_timeout", 45),
    ("HISTORY_MAX_CHARS", "6000", "history_max_chars", 6000),
    ("GATEWAY_STREAM_TRACE", "0", "stream_trace_enabled", False),
    ("GATEWAY_INTAKE_MAX_MB", "128", "intake_max_mb", 128),
    ("GATEWAY_FILE_EXTRACT_MAX_FILES", "8", "file_extract_max_files", 8),
    ("GATEWAY_FILE_EXTRACT_MAX_CHARS", "100000", "file_extract_max_chars", 100000),
    ("GATEWAY_NOTIFY_TARGET", "ou_x", "notify_target", "ou_x"),
    ("GATEWAY_REMINDER_REGISTER", "/x/rr", "reminder_register", "/x/rr"),
    ("GATEWAY_MODEL_CATALOG_PATH", "/x/cat.json", "model_catalog_path", "/x/cat.json"),
    ("GATEWAY_PLUGIN_RELEASE_DIR", "/x/rel", "plugin_release_dir", "/x/rel"),
    ("GATEWAY_MODEL_REFRESH_TIMEOUT", "12", "model_refresh_timeout", 12.0),
    ("RTIME_MOONSHOT_BASE_URL", "https://m/v1", "moonshot_base_url", "https://m/v1"),
    ("RTIME_USTC_BASE_URL", "https://u/v1", "ustc_base_url", "https://u/v1"),
    ("RTIME_USTC_API_KEY_FILE", "/x/key", "ustc_api_key_file", "/x/key"),
    ("RTIME_GATEWAY_URL", "http://h:1/", "gateway_url", "http://h:1/"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_legacy_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    cfg = AssistantGatewayConfig()
    assert getattr(cfg, field) == expected


def test_no_env_alias_left_out_of_table():
    """Every accepted env name is exercised by the LEGACY_ENV table (no gap)."""
    tabled = {env for env, *_ in LEGACY_ENV}
    assert tabled == set(_all_env_aliases())


def test_implicit_prefix_name_not_accepted(monkeypatch):
    # env_prefix="" => only declared aliases load; a prefix-guess must NOT leak.
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv("ASSISTANT_GATEWAY_BIND", "leak")
    monkeypatch.setenv("ASSISTANT_GATEWAY_PORT", "1234")
    cfg = AssistantGatewayConfig()
    assert cfg.bind == "127.0.0.1"
    assert cfg.port == 8765


# --- (3) from_env + load_config clean-env parity -------------------------------
def _clear_all(monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)


def test_from_env_clean_env_matches_defaults(monkeypatch):
    """from_env with a clean env == the schema defaults, except:
    - the two base URLs fall back to the rtime-models registry (trailing-slash
      stripped);
    - claude_permission_mode / gateway_access_mode are left as the RAW env value
      ("" when unset) — the load_config adapter runs sanitize_permission_mode /
      access_mode on them (=> "dontAsk" / "readonly"), asserted in
      test_load_config_clean_env_resolves_paths. from_env must NOT pre-normalise,
      matching the legacy os.environ.get(...) that fed those transforms."""
    import rtime_models

    _clear_all(monkeypatch)
    cfg = AssistantGatewayConfig.from_env()
    expected_moonshot = rtime_models.base_url("moonshot-openai").rstrip("/")
    expected_ustc = rtime_models.base_url("ustc-openai").rstrip("/")
    assert cfg.moonshot_base_url == expected_moonshot
    assert cfg.ustc_base_url == expected_ustc
    # raw pass-through: unset => "" here, normalised by the adapter later.
    assert cfg.claude_permission_mode == ""
    assert cfg.gateway_access_mode == ""
    skip = {
        "moonshot_base_url",
        "ustc_base_url",
        "claude_permission_mode",
        "gateway_access_mode",
    }
    for field, expected in LEGACY_DEFAULTS.items():
        if field in skip:
            continue
        assert getattr(cfg, field) == expected, field


def test_load_config_clean_env_resolves_paths(monkeypatch):
    """The load_config adapter resolves the empty path sentinels against
    Path.home()/brain_root/log_dir exactly like the pre-migration code."""
    from gateway_config import load_config

    _clear_all(monkeypatch)
    cfg = load_config()
    home = Path.home()
    brain = Path("/mnt/brain")
    log_dir = home / ".local/state/rtime-assistant/assistant-gateway"
    assert cfg["bind"] == "127.0.0.1"
    assert cfg["port"] == 8765
    assert cfg["brain_root"] == brain
    assert cfg["claude_bin"] == str(home / ".local/bin/claude-kimi")
    assert cfg["index_pythonpath"] == str(
        home / "rtime-assistant/packages/brain-library/src"
    )
    assert cfg["index_db"] == str(
        home / ".local/state/rtime-assistant/brain-library/brain-library.sqlite"
    )
    assert cfg["log_dir"] == log_dir
    assert cfg["memory_root"] == brain / "memory"
    assert cfg["context_sources_path"] == brain / "_system/rtime-context-sources.jsonl"
    assert cfg["memory_candidate_review_dir"] == brain / "memory/review-queue"
    assert cfg["relations_path"] == brain / "_indexes/relations.jsonl"
    assert cfg["model_catalog_path"] == log_dir / "model-catalog.json"
    assert cfg["plugin_release_dir"] == (
        home / ".local/share/rtime-assistant/plugin-release/rtime-assistant"
    )
    assert cfg["ustc_api_key_file"] == home / ".config/rtime-assistant/ustc-api-key"
    # the permission mode is sanitized in the adapter (dontAsk is valid, kept).
    assert cfg["claude_permission_mode"] == "dontAsk"
    assert cfg["gateway_access_mode"] == "readonly"


def test_load_config_bad_permission_mode_falls_back(monkeypatch):
    """An unknown CLAUDE_PERMISSION_MODE => the DEFAULT_PERMISSION_MODE (sanitize)."""
    from gateway_config import load_config

    _clear_all(monkeypatch)
    monkeypatch.setenv("CLAUDE_PERMISSION_MODE", "garbage")
    assert load_config()["claude_permission_mode"] == "dontAsk"


def test_load_config_base_url_trailing_slash_stripped(monkeypatch):
    """Explicit base URLs keep the legacy rstrip('/')."""
    from gateway_config import load_config

    _clear_all(monkeypatch)
    monkeypatch.setenv("RTIME_MOONSHOT_BASE_URL", "https://m/v1/")
    monkeypatch.setenv("RTIME_USTC_BASE_URL", "https://u/v2///")
    cfg = load_config()
    assert cfg["moonshot_base_url"] == "https://m/v1"
    assert cfg["ustc_base_url"] == "https://u/v2"


# --- (4) rtime metadata: x-env-aliases + x-reload; no secrets ------------------
def test_every_field_carries_env_aliases_and_reload():
    props = AssistantGatewayConfig.model_json_schema(by_alias=False)["properties"]
    assert set(props) == set(AssistantGatewayConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"


def test_no_secret_fields():
    """The gateway embeds no credentials — the keyfile PATH is not itself secret."""
    props = AssistantGatewayConfig.model_json_schema(by_alias=False)["properties"]
    secrets = {name for name, prop in props.items() if prop.get("x-secret")}
    assert secrets == set()


# --- (5) admin-core registry registration -------------------------------------
def test_registers_into_admin_core_registry():
    from rtime_admin_core import default_registry, register_assistant_gateway_module

    reg = default_registry(include_assistant_gateway=True)
    assert reg.has("assistant-gateway")
    assert reg.model("assistant-gateway") is AssistantGatewayConfig
    # the standalone helper also works on a bare registry.
    from rtime_admin_core import Registry

    bare = Registry()
    register_assistant_gateway_module(bare)
    assert bare.has("assistant-gateway")


def test_registry_schema_exposes_metadata():
    from rtime_admin_core import default_registry

    reg = default_registry(include_assistant_gateway=True)
    schema = reg.get_schema("assistant-gateway")
    assert schema["properties"]["port"]["x-env-aliases"] == ["GATEWAY_PORT"]
    assert schema["properties"]["port"]["x-scope"] == "write:channel"


# --- (6) generated config doc golden ------------------------------------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "gateway_config_schema:AssistantGatewayConfig",
            "--title",
            "assistant-gateway 配置项",
        ],
        cwd=str(REPO_ROOT),
        env={
            **os.environ,
            "PYTHONPATH": os.pathsep.join(
                str(p) for p in (CONFIG_SRC, MODELS_SRC, APP_ROOT)
            ),
        },
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "gateway_config_schema:AssistantGatewayConfig --title "
        f"'assistant-gateway 配置项' --out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/assistant-gateway.md is stale — the config schema changed. "
        "Review the diff, then regenerate with: python -m rtime_config "
        "gateway_config_schema:AssistantGatewayConfig --title "
        f"'assistant-gateway 配置项' --out {DOC_PATH}"
    )
