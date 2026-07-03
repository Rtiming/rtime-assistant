# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""P2 config pilot — behaviour-preservation tests for the pydantic-settings model.

Guards that the schema-driven ``QQBridgeConfig`` (packages/rtime-config base +
apps/qq-bridge/qq_bridge/config.py) is a drop-in for the old dataclass:

  1. every field's default == the legacy default (parametrized table);
  2. legacy env names still load the value (compat shim never regresses);
  3. new/canonical env names load too;
  4. from_env clean-env output == the legacy from_env computed defaults;
  5. the generated docs/config/qq-bridge.md matches the model schema (golden).

The legacy defaults below are copied verbatim from the pre-pilot dataclass field
declarations. If a field's default must change, that is a deliberate config
change: update this table AND note it in the PR — the test makes it loud.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from qq_bridge.config import QQ_CHAT_SYSTEM_PROMPT, QQBridgeConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
CONFIG_SRC = REPO_ROOT / "packages" / "rtime-config" / "src"
DOC_PATH = REPO_ROOT / "docs" / "config" / "qq-bridge.md"

# --- (1) field default parity: legacy dataclass defaults, verbatim ---
# admin_ids' legacy default is None (falls back to owner_ids via __post_init__);
# after construction with empty owner_ids it resolves to frozenset(), which is what
# a fresh QQBridgeConfig() exposes — tested separately below.
LEGACY_DEFAULTS = {
    "owner_ids": frozenset(),
    "allowed_users": frozenset(),
    "private_access": "admin_allowed",
    "blocked_users": frozenset(),
    "ws_host": "0.0.0.0",
    "ws_port": 8080,
    "ws_path": "/onebot/v11",
    "access_token": None,
    "archive_path": None,
    "archive_root": None,
    "archive_mode": "events",
    "group_invite_policy": "reject",
    "group_allowlist": frozenset(),
    "group_autoleave": True,
    "public_groups": frozenset(),
    "open_public": False,
    "group_reply_at_sender": False,
    "claude_cli": "",
    "model": "",
    "permission_mode": "default",
    "read_only": False,
    "default_cwd": "",
    "sessions_dir": "",
    "stream_output": True,
    "show_tool_calls": False,
    "system_prompt": QQ_CHAT_SYSTEM_PROMPT,
    "mcp_config": '{"mcpServers": {}}',
    "log_level": "INFO",
    "media_dir": "",
    "max_download_bytes": 20 * 1024 * 1024,
    "send_media": True,
    "napcat_file_dir": "",
    "run_timeout_seconds": 600.0,
    "max_chat_locks": 256,
    "debounce_seconds": 0.0,
    "debounce_max_messages": 20,
    "debounce_max_chars": 12000,
    "replay_grace_seconds": 5.0,
    "suppress_sends_when_offline": True,
    "stt_model_dir": "",
    "napcat_http": "http://127.0.0.1:3000",
    "alert_webhook": "",
    "direct_rules_path": "",
}


@pytest.mark.parametrize("field,expected", sorted(LEGACY_DEFAULTS.items()))
def test_field_default_matches_legacy(field, expected):
    cfg = QQBridgeConfig(owner_ids=frozenset())
    assert getattr(cfg, field) == expected


def test_admin_ids_defaults_to_owner_ids():
    # 向后兼容:未显式给 admin_ids 时沿用 owner_ids。
    assert QQBridgeConfig(owner_ids=frozenset({"1"})).admin_ids == frozenset({"1"})
    # 显式给出以其为准。
    assert QQBridgeConfig(
        owner_ids=frozenset({"1"}), admin_ids=frozenset({"2"})
    ).admin_ids == frozenset({"2"})
    # 显式空集 => "无 admin",不回落(与旧 dataclass __post_init__ 一致)。
    assert (
        QQBridgeConfig(owner_ids=frozenset({"1"}), admin_ids=frozenset()).admin_ids
        == frozenset()
    )


def test_private_access_rejects_unknown_mode():
    with pytest.raises(ValueError, match="private_access"):
        QQBridgeConfig(private_access="everyone")


def test_no_field_default_drift():
    """The declared-default set must cover every model field (no field added
    without recording its legacy default)."""
    model_fields = set(QQBridgeConfig.model_fields)
    covered = set(LEGACY_DEFAULTS) | {"admin_ids"}
    assert model_fields == covered, (
        "field set drifted; add new fields to LEGACY_DEFAULTS: "
        f"{model_fields ^ covered}"
    )


# --- (2) legacy env names still load ---
LEGACY_ENV_CASES = [
    ("QQ_OWNER_IDS", "10 20,30", "owner_ids", frozenset({"10", "20", "30"})),
    ("QQ_ADMIN_IDS", "77", "admin_ids", frozenset({"77"})),
    ("QQ_ALLOWED_USERS", "5", "allowed_users", frozenset({"5"})),
    (
        "QQ_PRIVATE_ACCESS",
        "friends-and-temporary",
        "private_access",
        "friends_and_temporary",
    ),
    ("QQ_BLOCKED_USERS", "6", "blocked_users", frozenset({"6"})),
    ("QQ_BRIDGE_WS_HOST", "127.0.0.1", "ws_host", "127.0.0.1"),
    ("QQ_BRIDGE_WS_PORT", "9001", "ws_port", 9001),
    ("QQ_BRIDGE_WS_PATH", "/x", "ws_path", "/x"),
    ("QQ_ONEBOT_ACCESS_TOKEN", "sekret", "access_token", "sekret"),
    ("QQ_BRIDGE_ARCHIVE", "/a.jsonl", "archive_path", "/a.jsonl"),
    ("QQ_ARCHIVE_ROOT", "/arch", "archive_root", "/arch"),
    ("RTIME_CHAT_ARCHIVE_ROOT", "/arch2", "archive_root", "/arch2"),
    ("QQ_ARCHIVE_MODE", "off", "archive_mode", "off"),
    ("QQ_GROUP_INVITE_POLICY", "owner", "group_invite_policy", "owner"),
    ("QQ_GROUP_ALLOWLIST", "600", "group_allowlist", frozenset({"600"})),
    ("QQ_GROUP_AUTOLEAVE", "0", "group_autoleave", False),
    ("QQ_PUBLIC_GROUPS", "700", "public_groups", frozenset({"700"})),
    ("QQ_OPEN_PUBLIC", "1", "open_public", True),
    ("QQ_GROUP_REPLY_AT_SENDER", "1", "group_reply_at_sender", True),
    ("QQ_READ_ONLY", "true", "read_only", True),
    ("CLAUDE_CLI_PATH", "/x/claude", "claude_cli", "/x/claude"),
    ("DEFAULT_MODEL", "kimi-code", "model", "kimi-code"),
    ("PERMISSION_MODE", "acceptEdits", "permission_mode", "acceptEdits"),
    ("DEFAULT_CWD", "/w", "default_cwd", "/w"),
    ("QQ_SESSIONS_DIR", "/s", "sessions_dir", "/s"),
    ("QQ_SYSTEM_PROMPT", "hi", "system_prompt", "hi"),
    (
        "QQ_MCP_CONFIG",
        '{"mcpServers":{"x":{}}}',
        "mcp_config",
        '{"mcpServers":{"x":{}}}',
    ),
    ("QQ_LOG_LEVEL", "DEBUG", "log_level", "DEBUG"),
    ("QQ_MEDIA_DIR", "/m", "media_dir", "/m"),
    ("QQ_NAPCAT_FILE_DIR", "/nf", "napcat_file_dir", "/nf"),
    ("QQ_RUN_TIMEOUT_SECONDS", "120", "run_timeout_seconds", 120.0),
    ("QQ_MAX_CHAT_LOCKS", "8", "max_chat_locks", 8),
    ("QQ_DEBOUNCE_SECONDS", "1.5", "debounce_seconds", 1.5),
    ("QQ_DEBOUNCE_MAX_MESSAGES", "3", "debounce_max_messages", 3),
    ("QQ_DEBOUNCE_MAX_CHARS", "99", "debounce_max_chars", 99),
    ("QQ_REPLAY_GRACE_SECONDS", "0", "replay_grace_seconds", 0.0),
    ("QQ_SUPPRESS_SENDS_WHEN_OFFLINE", "0", "suppress_sends_when_offline", False),
    ("QQ_STT_MODEL_DIR", "/stt", "stt_model_dir", "/stt"),
    ("QQ_NAPCAT_HTTP", "http://h:1", "napcat_http", "http://h:1"),
    ("QQ_ALERT_WEBHOOK", "http://wh", "alert_webhook", "http://wh"),
    ("QQ_DIRECT_RULES", "/r.json", "direct_rules_path", "/r.json"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV_CASES)
def test_legacy_env_name_loads_via_model(monkeypatch, env, value, field, expected):
    monkeypatch.setenv(env, value)
    cfg = QQBridgeConfig()
    assert getattr(cfg, field) == expected


# --- (3) new/canonical env name loads ---
def test_new_env_name_loads(monkeypatch):
    # max_download_bytes is the new raw-bytes name (legacy from_env used the MB
    # spelling QQ_MAX_DOWNLOAD_MB, which lives only in from_env).
    monkeypatch.setenv("QQ_MAX_DOWNLOAD_BYTES", str(3 * 1024 * 1024))
    assert QQBridgeConfig().max_download_bytes == 3 * 1024 * 1024


def test_implicit_prefix_name_not_accepted(monkeypatch):
    # env_prefix="" => only declared aliases load; a prefix-guess must NOT leak.
    monkeypatch.setenv("QQ_MODEL", "leak")
    monkeypatch.setenv("QQ_WS_PORT", "1234")
    cfg = QQBridgeConfig()
    assert cfg.model == ""
    assert cfg.ws_port == 8080


# --- (4) from_env clean-env parity with the legacy computed defaults ---
def test_from_env_clean_env_matches_legacy_computed(monkeypatch):
    for k in list(__import__("os").environ):
        if k.startswith(("QQ_", "DEFAULT_", "PERMISSION_", "CLAUDE_CLI")):
            monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("CLAUDE_CLI_PATH", "")  # avoid PATH-dependent claude_cli
    monkeypatch.setattr("shutil.which", lambda _: None)
    cfg = QQBridgeConfig.from_env()
    # legacy from_env defaults that differ from the plain field default:
    assert cfg.claude_cli == ""  # no CLI on PATH
    assert cfg.sessions_dir.endswith("/.qq-claude")  # ~ expanded fallback
    # everything else equals the plain field default table:
    for field, expected in LEGACY_DEFAULTS.items():
        if field in ("claude_cli", "sessions_dir"):
            continue
        assert getattr(cfg, field) == expected, field


# --- (5) generated doc golden ---
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "qq_bridge.config:QQBridgeConfig",
            "--title",
            "qq-bridge 配置项",
        ],
        cwd=str(REPO_ROOT),
        env={
            **__import__("os").environ,
            "PYTHONPATH": f"{CONFIG_SRC}{__import__('os').pathsep}"
            f"{REPO_ROOT / 'apps' / 'qq-bridge'}",
        },
        capture_output=True,
        text=True,
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "qq_bridge.config:QQBridgeConfig --title 'qq-bridge 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/qq-bridge.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "qq_bridge.config:QQBridgeConfig --title 'qq-bridge 配置项' "
        f"--out {DOC_PATH}"
    )
