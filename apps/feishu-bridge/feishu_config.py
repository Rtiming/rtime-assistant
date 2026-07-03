# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema-driven config for the Feishu/Lark bridge — the ``FeishuBridgeConfig`` model.

P2 config 收编 (批 1 · Lane A, see docs/design/config-full-coverage-plan-2026-07
.zh-CN.md §二 批 1 + docs/reference/config-coverage.zh-CN.md): the module-level
``os.getenv`` surface of the Feishu bridge is expressed here as ``FeishuBridgeConfig``
— a ``rtime-config`` pydantic-settings model. One source of truth for validation,
defaults, descriptions and JSON Schema (docs/config/feishu.md is generated from it),
REGISTERED into the admin-core registry as module ``feishu`` so the panel /
config-agent can manage it (全覆盖).

This module is DELIBERATELY import-safe (no credential loading, no side effects
beyond ``load_dotenv``), exactly like the qq-bridge pilot's
``qq_bridge/config.py``: admin-core lazily imports ``FeishuBridgeConfig`` from here
to register the module, and it must not raise just because Feishu credentials are
not configured in that process. The credential hard-load + the module-level
constants other bridge modules import live in ``bot_config.py``, which re-exports
this class.

This is BEHAVIOUR-PRESERVING: every field default matches the old ``bot_config``
module-level constant, every legacy env name still loads the value (via
``env_aliases`` -> ``AliasChoices``), and ``from_env`` reproduces the legacy env
parsing quirks (credential JSON fallback keys, CLI PATH lookup, ``~`` expansion,
CSV splitting, MODEL_ALIASES_JSON extension) byte-for-byte.

Secrets: ``app_id`` / ``app_secret`` are ``secret_field`` (x-secret) — the panel /
API only ever see ``***`` (admin-core redacts get_all/diff/audit). Credentials still
fall back to the ``FEISHU_CONFIG_JSON`` file exactly as before; ``config_json`` is
the file PATH (not itself a secret).

Naming fix: ``SESSIONS_DIR`` was a hard-coded ``~/.feishu-claude`` with no env
override — it now has a canonical ``FEISHU_SESSIONS_DIR`` env (defaulting to the
same path), closing the naming-inconsistency the audit flagged, without changing the
default.
"""

from __future__ import annotations

import os
import shutil
from typing import Annotated

import _shared_runtime  # noqa: F401 — side effect: put rtime_config on sys.path
import model_routing
from dotenv import load_dotenv
from pydantic import field_validator
from pydantic_settings import NoDecode, SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field, secret_field
from rtime_config.fields import Reload

load_dotenv()

# The historical hard-coded session store (no env override before this migration).
DEFAULT_SESSIONS_DIR = "~/.feishu-claude"
# Default credential JSON path when FEISHU_APP_ID/SECRET are not in env.
DEFAULT_CONFIG_JSON = "~/.config/rtime-assistant/feishu.json"
# Watchdog forced-restart default (mirrors main.DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS).
DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS = 4 * 3600


# CSV id fields are populated from a comma-separated env string, NOT JSON; NoDecode
# stops pydantic-settings from json.loads()-ing the raw value before our
# ``mode="before"`` validator splits it. (Direct construction passes a set/frozenset.)
CsvSet = Annotated[frozenset[str], NoDecode]


def _split_csv(raw: object) -> frozenset[str]:
    """Split a comma-separated id list into a set of trimmed strings.

    Reproduces the legacy ``_split_csv_env`` semantics (comma-only split, strip,
    drop empties). Accepts an already-normalized set/list/frozenset (direct
    construction / tests) and passes it through.
    """
    if isinstance(raw, frozenset):
        return raw
    if raw is None:
        return frozenset()
    if isinstance(raw, (set, list, tuple)):
        return frozenset(str(tok).strip() for tok in raw if str(tok).strip())
    return frozenset(item.strip() for item in str(raw).split(",") if item.strip())


def _parse_watchdog_seconds(raw: str | None) -> float:
    """Match main.py's tolerant parse: unset/invalid => the 4h default."""
    default = float(DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS)
    if not raw:
        return default
    try:
        return float(raw)
    except (TypeError, ValueError):
        return default


class FeishuBridgeConfig(RtimeBaseSettings):
    # env_prefix="" (not "FEISHU_") on purpose: every field declares its COMPLETE set
    # of accepted env names via env_aliases, so the accepted env surface equals
    # exactly what is declared (and what x-env-aliases documents) — no implicit
    # prefix-derived names silently widening it. This keeps the migration strictly
    # behaviour-preserving: the legacy FEISHU_* / shared unprefixed (DEFAULT_MODEL,
    # PERMISSION_MODE, ALLOWED_USERS, …) names each load as before, and nothing new
    # is accepted unless explicitly listed. (Mirrors QQBridgeConfig / WebChatConfig.)
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    # --- credentials (x-secret) ---
    app_id: str | None = secret_field(
        default=None,
        description="Feishu/Lark app id. Secret. Falls back to config_json's "
        '"appId"/"app_id" when unset (bot_config._load_feishu_credentials).',
        scope="write:channel",
        env_aliases=["FEISHU_APP_ID"],
    )
    app_secret: str | None = secret_field(
        default=None,
        description="Feishu/Lark app secret. Secret. Falls back to config_json's "
        '"appSecret"/"app_secret" when unset.',
        scope="write:channel",
        env_aliases=["FEISHU_APP_SECRET"],
    )
    config_json: str = config_field(
        default=DEFAULT_CONFIG_JSON,
        description="Path to the credential JSON file read when FEISHU_APP_ID/"
        "FEISHU_APP_SECRET are unset. The path itself is not secret; the file holds "
        "the (secret) appId/appSecret.",
        scope="write:channel",
        env_aliases=["FEISHU_CONFIG_JSON"],
    )
    # --- model CLI + shared cross-channel knobs (env_aliases reuse channel-common) ---
    claude_cli: str = config_field(
        default="claude",
        description="The claude CLI / claude-rtime wrapper. from_env resolves "
        "CLAUDE_CLI_PATH then PATH then the literal 'claude'.",
        scope="write:channel",
        env_aliases=["CLAUDE_CLI_PATH"],
    )
    model: str = config_field(
        default="",
        description='Default model; "" => model_routing.default_model() '
        "(wrapper default = kimi-code). Production sets DEFAULT_MODEL explicitly.",
        reload=Reload.HOT,
        scope="write:models",
        env_aliases=["DEFAULT_MODEL"],
    )
    default_cwd: str = config_field(
        default="~",
        description="Where the model runs. ~ expanded (default = $HOME).",
        scope="write:channel",
        env_aliases=["DEFAULT_CWD"],
    )
    permission_mode: str = config_field(
        default="default",
        description="Model CLI permission mode "
        "(default / acceptEdits / bypassPermissions / plan).",
        scope="write:channel",
        env_aliases=["PERMISSION_MODE"],
    )
    mcp_config: str | None = config_field(
        default=None,
        description="MCP config passed to the claude CLI (inline JSON or a path). "
        "Empty/unset => None => the CLI keeps using ~/.claude.json mcpServers + the "
        "/mnt/brain mount (no behaviour change). Set it to opt into "
        "--strict-mcp-config (IGNORES ~/.claude.json). 重启级。",
        scope="write:channel",
        env_aliases=["FEISHU_MCP_CONFIG"],
    )
    archive_root: str | None = config_field(
        default=None,
        description="通道无关聊天归档根(design chat-archive-storage §1):设置即启用 "
        "rtime_chat_runtime.archive 的按日分片 envelope 归档(<root>/raw/feishu/YYYY/MM/"
        "DD/events.jsonl);None=飞书零归档(现状)。重启级。",
        env_aliases=["FEISHU_ARCHIVE_ROOT", "RTIME_CHAT_ARCHIVE_ROOT"],
    )
    archive_mode: str = config_field(
        default="events",
        description="归档模式 off|events|full(design 配置面):off=不落盘;events=raw "
        "envelope 层;full=预留(A2 transcript)。仅当 archive_root 设置时生效。重启级。",
        env_aliases=["FEISHU_ARCHIVE_MODE"],
    )
    model_aliases_json: str = config_field(
        default="",
        description="Extra model aliases as a JSON object, extending/overriding the "
        "registry base aliases (model_routing.base_aliases()). Empty => base only.",
        scope="write:models",
        env_aliases=["MODEL_ALIASES_JSON"],
    )
    # --- access control (CSV id sets) ---
    allowed_users: CsvSet = config_field(
        default_factory=frozenset,
        description="Comma-separated Feishu open_id whitelist for private messages.",
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["ALLOWED_USERS"],
    )
    allowed_chats: CsvSet = config_field(
        default_factory=frozenset,
        description="Comma-separated group chat_id whitelist.",
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["ALLOWED_CHATS"],
    )
    admin_users: CsvSet = config_field(
        default_factory=frozenset,
        description="Comma-separated admin open_id set. Empty => defaults to "
        "allowed_users (向后兼容, matches the legacy `or set(ALLOWED_USERS)`).",
        reload=Reload.HOT,
        scope="write:channel",
        env_aliases=["ADMIN_USERS"],
    )
    require_mention_in_group: bool = config_field(
        default=True,
        description="Require an @-mention of the bot to answer in group chats "
        "(REQUIRE_MENTION_IN_GROUP=0 disables).",
        scope="write:channel",
        env_aliases=["REQUIRE_MENTION_IN_GROUP"],
    )
    owner_personal_library_access: bool = config_field(
        default=False,
        description="Allow the owner's personal-data library subtree in tool scope "
        "(tool_policy). Off by default. FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS=1 opts in.",
        scope="write:channel",
        env_aliases=["FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS"],
    )
    # --- session store (naming fix: was a hard-coded path, now env-overridable) ---
    sessions_dir: str = config_field(
        default=DEFAULT_SESSIONS_DIR,
        description="Session-id store; independent from the QQ bridge. ~ expanded. "
        "Was a hard-coded ~/.feishu-claude before this migration (default unchanged).",
        scope="write:channel",
        env_aliases=["FEISHU_SESSIONS_DIR"],
    )
    # --- transport / callback ---
    callback_port: int = config_field(
        default=9981,
        description="Optional HTTP callback/health-check port; card buttons prefer "
        "the Feishu WebSocket event channel.",
        ge=1,
        le=65535,
        env_aliases=["CALLBACK_PORT"],
    )
    # --- streaming / debounce / heartbeat ---
    stream_chunk_size: int = config_field(
        default=20,
        description="Streaming card update: push once per this many accumulated chars.",
        ge=1,
        env_aliases=["STREAM_CHUNK_SIZE"],
    )
    message_debounce_seconds: float = config_field(
        default=0.0,
        description="Burst debounce window: merge near-simultaneous text messages in "
        "one chat before invoking the model. 0 = off (the Python default; enable in "
        "prod env/Compose).",
        env_aliases=["MESSAGE_DEBOUNCE_SECONDS"],
    )
    message_debounce_max_messages: int = config_field(
        default=20,
        description="Max messages merged in one debounce window.",
        ge=1,
        env_aliases=["MESSAGE_DEBOUNCE_MAX_MESSAGES"],
    )
    message_debounce_max_chars: int = config_field(
        default=12000,
        description="Max chars merged in one debounce window.",
        ge=1,
        env_aliases=["MESSAGE_DEBOUNCE_MAX_CHARS"],
    )
    status_heartbeat_seconds: float = config_field(
        default=6.0,
        description="While the model is silent, refresh the placeholder card this "
        "often so users can tell model-wait from a dead bridge. 0 disables.",
        env_aliases=["STATUS_HEARTBEAT_SECONDS"],
    )
    # --- output policy ---
    output_style: str = config_field(
        default="segmented",
        description="User-facing output policy. 'segmented' sends assistant text as "
        "separate Feishu messages at natural boundaries and hides tool-call details.",
        env_aliases=["OUTPUT_STYLE"],
    )
    show_tool_calls: bool = config_field(
        default=False,
        description="Reveal which tools/commands ran (SHOW_TOOL_CALLS=1).",
        env_aliases=["SHOW_TOOL_CALLS"],
    )
    # --- outbound attachments (bridge_runner) ---
    outbound_attachment_max_bytes: int = config_field(
        default=30 * 1024 * 1024,
        description="Per outbound attachment size cap (bytes) for model-emitted "
        "[[rtime-send-file:…]] / [[rtime-send-image:…]] uploads.",
        ge=1,
        env_aliases=["FEISHU_OUTBOUND_ATTACHMENT_MAX_BYTES"],
    )
    # --- handover CLI default model (handover.py) ---
    handover_model: str = config_field(
        default="claude-opus-4-6",
        description="Default model recorded in the handover deep-link (handover.py).",
        scope="write:models",
        env_aliases=["CLAUDE_MODEL"],
    )
    # --- watchdog / dev tunnel (main.py) ---
    watchdog_max_uptime_seconds: float = config_field(
        default=float(DEFAULT_WATCHDOG_MAX_UPTIME_SECONDS),
        description="Forced-restart ceiling: the watchdog restarts the process after "
        "this uptime (seconds). 0 disables forced restarts.",
        ge=0,
        env_aliases=["WATCHDOG_MAX_UPTIME_SECONDS"],
    )
    ngrok_domain: str = config_field(
        default="",
        description="Dev ngrok tunnel domain for the HTTP callback (local dev only; "
        "prod uses the Feishu WebSocket channel).",
        env_aliases=["NGROK_DOMAIN"],
    )

    @field_validator(
        "allowed_users",
        "allowed_chats",
        "admin_users",
        mode="before",
    )
    @classmethod
    def _coerce_csv(cls, v: object) -> frozenset[str]:
        return _split_csv(v)

    @property
    def model_enabled(self) -> bool:
        return bool(self.claude_cli)

    @classmethod
    def from_env(cls) -> "FeishuBridgeConfig":
        """Load from process env, reproducing the legacy parsing exactly.

        The transforms pydantic-settings cannot express declaratively (credential
        JSON fallback, CLI PATH lookup, ``~`` expansion, MODEL_ALIASES_JSON default,
        admin->allowed fallback, empty-mcp sentinel) are applied here so behaviour is
        byte-identical to the pre-migration module-level constants.
        """
        app_id = os.getenv("FEISHU_APP_ID") or None
        app_secret = os.getenv("FEISHU_APP_SECRET") or None
        claude_cli = os.getenv("CLAUDE_CLI_PATH") or shutil.which("claude") or "claude"
        allowed_users = _split_csv(os.getenv("ALLOWED_USERS", ""))
        admin_users = _split_csv(os.getenv("ADMIN_USERS", "")) or allowed_users
        return cls(
            app_id=app_id,
            app_secret=app_secret,
            config_json=os.getenv("FEISHU_CONFIG_JSON", DEFAULT_CONFIG_JSON),
            claude_cli=claude_cli,
            model=os.getenv("DEFAULT_MODEL", model_routing.default_model()),
            default_cwd=os.path.expanduser(os.getenv("DEFAULT_CWD", "~")),
            permission_mode=os.getenv("PERMISSION_MODE", "default"),
            mcp_config=(os.getenv("FEISHU_MCP_CONFIG", "").strip() or None),
            archive_root=(
                os.getenv("FEISHU_ARCHIVE_ROOT", "").strip()
                or os.getenv("RTIME_CHAT_ARCHIVE_ROOT", "").strip()
                or None
            ),
            archive_mode=(
                os.getenv("FEISHU_ARCHIVE_MODE", "").strip().lower() or "events"
            ),
            model_aliases_json=os.getenv("MODEL_ALIASES_JSON", "").strip(),
            allowed_users=allowed_users,
            allowed_chats=_split_csv(os.getenv("ALLOWED_CHATS", "")),
            admin_users=admin_users,
            require_mention_in_group=os.getenv("REQUIRE_MENTION_IN_GROUP", "1") != "0",
            owner_personal_library_access=(
                os.getenv("FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS", "0") == "1"
            ),
            sessions_dir=os.path.expanduser(
                os.getenv("FEISHU_SESSIONS_DIR", DEFAULT_SESSIONS_DIR)
            ),
            callback_port=int(os.getenv("CALLBACK_PORT", "9981")),
            stream_chunk_size=int(os.getenv("STREAM_CHUNK_SIZE", "20")),
            message_debounce_seconds=float(os.getenv("MESSAGE_DEBOUNCE_SECONDS", "0")),
            message_debounce_max_messages=int(
                os.getenv("MESSAGE_DEBOUNCE_MAX_MESSAGES", "20")
            ),
            message_debounce_max_chars=int(
                os.getenv("MESSAGE_DEBOUNCE_MAX_CHARS", "12000")
            ),
            status_heartbeat_seconds=float(os.getenv("STATUS_HEARTBEAT_SECONDS", "6")),
            output_style=os.getenv("OUTPUT_STYLE", "segmented"),
            show_tool_calls=os.getenv("SHOW_TOOL_CALLS", "0") == "1",
            outbound_attachment_max_bytes=int(
                os.getenv("FEISHU_OUTBOUND_ATTACHMENT_MAX_BYTES", str(30 * 1024 * 1024))
            ),
            handover_model=os.environ.get("CLAUDE_MODEL", "claude-opus-4-6"),
            watchdog_max_uptime_seconds=_parse_watchdog_seconds(
                os.getenv("WATCHDOG_MAX_UPTIME_SECONDS")
            ),
            ngrok_domain=os.environ.get("NGROK_DOMAIN", ""),
        )
