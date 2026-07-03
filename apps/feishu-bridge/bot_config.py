# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Feishu bridge config — the live module-level constants the bridge imports.

This is the backward-compatibility layer over the schema-driven model in
``feishu_config`` (P2 config 收编 批 1 · Lane A, see
docs/design/config-full-coverage-plan-2026-07.zh-CN.md §二 批 1). The pydantic-
settings model ``FeishuBridgeConfig`` — the single source of truth for defaults,
validation and JSON Schema, and the class admin-core registers as module ``feishu``
— lives in ``feishu_config`` (import-safe, no side effects). This file:

  * re-exports ``FeishuBridgeConfig`` (so ``from bot_config import FeishuBridgeConfig``
    keeps working);
  * loads the Feishu credentials with the JSON-file fallback, raising when truly
    unconfigured (unchanged behaviour — the bridge must fail closed on missing creds);
  * derives the module-level constants other bridge modules import (``CLAUDE_CLI``,
    ``SESSIONS_DIR``, ``ALLOWED_USERS``, …) from ONE ``FeishuBridgeConfig.from_env()``
    load, so their historical values and types are byte-identical to before.

Behaviour is unchanged: every constant below equals what the old module-level
``os.getenv`` block produced.
"""

import json
import os

import _shared_runtime  # noqa: F401 — side effect: put rtime_config on sys.path
import model_routing
from feishu_config import (
    DEFAULT_CONFIG_JSON,
    FeishuBridgeConfig,
)

__all__ = [
    "FeishuBridgeConfig",
    "FEISHU_APP_ID",
    "FEISHU_APP_SECRET",
    "CLAUDE_CLI",
    "DEFAULT_MODEL",
    "DEFAULT_CWD",
    "PERMISSION_MODE",
    "FEISHU_MCP_CONFIG",
    "ARCHIVE_ROOT",
    "ARCHIVE_MODE",
    "MODEL_ALIASES",
    "ALLOWED_USERS",
    "ALLOWED_CHATS",
    "ADMIN_USERS",
    "REQUIRE_MENTION_IN_GROUP",
    "SESSIONS_DIR",
    "CALLBACK_PORT",
    "STREAM_CHUNK_SIZE",
    "MESSAGE_DEBOUNCE_SECONDS",
    "MESSAGE_DEBOUNCE_MAX_MESSAGES",
    "MESSAGE_DEBOUNCE_MAX_CHARS",
    "STATUS_HEARTBEAT_SECONDS",
    "OUTPUT_STYLE",
    "SHOW_TOOL_CALLS",
]


def _load_feishu_credentials() -> tuple[str, str]:
    app_id = os.getenv("FEISHU_APP_ID")
    app_secret = os.getenv("FEISHU_APP_SECRET")
    if app_id and app_secret:
        return app_id, app_secret

    config_path = os.path.expanduser(
        os.getenv("FEISHU_CONFIG_JSON", DEFAULT_CONFIG_JSON)
    )
    try:
        with open(config_path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        data = {}

    app_id = data.get("appId") or data.get("app_id")
    app_secret = data.get("appSecret") or data.get("app_secret")
    if app_id and app_secret:
        return app_id, app_secret

    raise RuntimeError(
        "Feishu credentials not configured. Set FEISHU_APP_ID/FEISHU_APP_SECRET "
        "or FEISHU_CONFIG_JSON."
    )


def _load_model_aliases(raw: str) -> dict[str, str]:
    # Base opus/sonnet/haiku aliases come from the registry; MODEL_ALIASES_JSON
    # still extends/overrides them.
    aliases = model_routing.base_aliases()
    raw = (raw or "").strip()
    if not raw:
        return aliases
    parsed = json.loads(raw)
    if not isinstance(parsed, dict):
        raise RuntimeError("MODEL_ALIASES_JSON must be a JSON object")
    for key, value in parsed.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise RuntimeError("MODEL_ALIASES_JSON keys and values must be strings")
        aliases[key.lower()] = value
    return aliases


# --- module-level constants (backward-compatible: other modules import these) ---
# Everything below is DERIVED from a single FeishuBridgeConfig.from_env() load, so
# the public names (imported by claude_runner / session_store / commands / main)
# keep their exact historical values and types. Credentials still use the JSON-file
# fallback via _load_feishu_credentials (raises if truly unconfigured), unchanged.
FEISHU_APP_ID, FEISHU_APP_SECRET = _load_feishu_credentials()

_CONFIG = FeishuBridgeConfig.from_env()

CLAUDE_CLI = _CONFIG.claude_cli
DEFAULT_MODEL = _CONFIG.model
DEFAULT_CWD = _CONFIG.default_cwd
PERMISSION_MODE = _CONFIG.permission_mode
FEISHU_MCP_CONFIG = _CONFIG.mcp_config
ARCHIVE_ROOT = _CONFIG.archive_root
ARCHIVE_MODE = _CONFIG.archive_mode
MODEL_ALIASES = _load_model_aliases(_CONFIG.model_aliases_json)
ALLOWED_USERS = set(_CONFIG.allowed_users)
ALLOWED_CHATS = set(_CONFIG.allowed_chats)
ADMIN_USERS = set(_CONFIG.admin_users)
REQUIRE_MENTION_IN_GROUP = _CONFIG.require_mention_in_group
SESSIONS_DIR = _CONFIG.sessions_dir
CALLBACK_PORT = _CONFIG.callback_port
STREAM_CHUNK_SIZE = _CONFIG.stream_chunk_size
MESSAGE_DEBOUNCE_SECONDS = _CONFIG.message_debounce_seconds
MESSAGE_DEBOUNCE_MAX_MESSAGES = _CONFIG.message_debounce_max_messages
MESSAGE_DEBOUNCE_MAX_CHARS = _CONFIG.message_debounce_max_chars
STATUS_HEARTBEAT_SECONDS = _CONFIG.status_heartbeat_seconds
OUTPUT_STYLE = _CONFIG.output_style
SHOW_TOOL_CALLS = _CONFIG.show_tool_calls
