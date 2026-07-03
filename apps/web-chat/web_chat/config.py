# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Configuration for the web-chat entry — schema-driven (pydantic-settings).

Two layers of config, kept apart on purpose:

  - ``WebChatConfig`` (this file) is the PROCESS-wide config: bind/port, the model
    CLI + defaults, the state dir, timeouts. It is a ``rtime-config`` pydantic-
    settings model (T5b coverage lane): one source of truth for validation,
    defaults, descriptions and JSON Schema, and REGISTERED into the admin-core
    registry as module ``web-chat`` so the panel can manage it (全覆盖). This is
    BEHAVIOUR-PRESERVING — every field default matches the old dataclass, every
    legacy env name (``WEB_CHAT_*`` / the shared ``CLAUDE_CLI_PATH`` /
    ``DEFAULT_MODEL`` / ``PERMISSION_MODE`` / ``DEFAULT_CWD``) still loads the value,
    and ``from_env`` / attribute access / the constructor are unchanged.

  - Per-PROFILE behavior (system prompt, read-only hard door, library scope, MCP
    gateway) is NOT here — it lives in the git profiles and is resolved per web
    session by ``web_chat.profiles.load_profiles`` (the real profile loader). The
    one bridge between them is ``read_only``: this config carries a process-wide
    ``WEB_CHAT_READ_ONLY`` env door that can only STRENGTHEN a profile's read-only
    (fail-closed union — env can ADD read_only to every profile, NEVER remove a
    profile's own; the same monotonic-restriction rule as the QQ bridge, so the
    env=0 downgrade bug can't come back).

Naming follows the design doc (§5.3): ``WEB_CHAT_BIND`` / port 8788 /
``RTIME_WEB_CHAT_STATE_DIR``. Shared knobs reuse the bridge-wide names
(``CLAUDE_CLI_PATH`` / ``DEFAULT_MODEL`` / ``PERMISSION_MODE`` / ``DEFAULT_CWD``) so
compose maps ``WEB_CHAT_*`` vars onto them exactly like qq-bridge does.
"""

from __future__ import annotations

import os
import shutil

from pydantic import field_validator
from pydantic_settings import SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field
from rtime_config.fields import Reload

# Where the compose bind-mounts the git ``profiles/`` tree read-only (design §2.6).
# Unlike the QQ bridge, web-chat is NOT bound to a single ``RTIME_PROFILE``: it lists
# EVERY web-enabled profile under this root and selects one per request (the page's
# dropdown, design §5.2). So there is no process-level RTIME_PROFILE here.
PROFILES_ROOT_ENV = "RTIME_PROFILES_ROOT"
DEFAULT_PROFILES_ROOT = "/etc/rtime/profiles"

# Process-wide read-only env door (fail-closed union): "1" forces read-only ON for
# every web profile; "0"/unset can NEVER pull a profile's own read_only down (that
# was the QQ env=0 downgrade bug — do not reintroduce it). Consumed by
# ``web_chat.profiles`` when it resolves each profile's effective read_only.
READ_ONLY_ENV = "WEB_CHAT_READ_ONLY"


def read_only_env_forces() -> bool:
    """Whether ``WEB_CHAT_READ_ONLY`` env asserts the (monotonic) read-only door.

    Only ``"1"`` STRENGTHENS. Any other value ("0"/unset/garbage) contributes
    nothing — it can never disable a profile's own read_only (fail-closed union).
    """
    return os.getenv(READ_ONLY_ENV, "").strip() == "1"


class WebChatConfig(RtimeBaseSettings):
    # env_prefix="" (not "WEB_CHAT_") on purpose: every field declares its COMPLETE
    # set of accepted env names via env_aliases, so the accepted env surface equals
    # exactly what is declared (and what x-env-aliases documents) — no implicit
    # prefix-derived names silently widening it. Mirrors QQBridgeConfig.
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    bind: str = config_field(
        default="127.0.0.1",
        description="HTTP bind host. 127.0.0.1 (default) = internal only; tailnet "
        "exposure = 0.0.0.0 + Tailscale ACL (design §5.3).",
        env_aliases=["WEB_CHAT_BIND"],
    )
    port: int = config_field(
        default=8788,
        description="HTTP bind port (design §5.3).",
        env_aliases=["WEB_CHAT_PORT"],
    )
    state_dir: str = config_field(
        default="",
        description="Session store + any future per-channel state dir. from_env "
        "default = ~/.local/state/rtime-assistant/web-chat (expanded).",
        env_aliases=["RTIME_WEB_CHAT_STATE_DIR"],
    )
    claude_cli: str = config_field(
        default="",
        description="The claude CLI / claude-rtime wrapper. Empty + not on PATH => "
        "model disabled (echo only). from_env resolves CLAUDE_CLI_PATH then PATH.",
        env_aliases=["CLAUDE_CLI_PATH"],
    )
    model: str = config_field(
        default="",
        description='Default model; "" => wrapper default (kimi-code).',
        reload=Reload.HOT,
        env_aliases=["DEFAULT_MODEL"],
    )
    permission_mode: str = config_field(
        default="default",
        description="Model CLI permission mode for NON-read-only profiles. A "
        "read_only profile forces READONLY_PERMISSION_MODE in code regardless.",
        env_aliases=["PERMISSION_MODE"],
    )
    default_cwd: str = config_field(
        default="",
        description='Where the model runs; "" => $HOME. ~ expanded.',
        env_aliases=["DEFAULT_CWD"],
    )
    archive_root: str | None = config_field(
        default=None,
        description="通道无关聊天归档根(design chat-archive-storage §1):设置即启用 "
        "rtime_chat_runtime.archive 的按日分片 envelope 归档(<root>/raw/web/YYYY/MM/DD/"
        "events.jsonl);None=web 零归档(现状)。重启级。",
        env_aliases=["WEB_CHAT_ARCHIVE_ROOT", "RTIME_CHAT_ARCHIVE_ROOT"],
    )
    archive_mode: str = config_field(
        default="events",
        description="归档模式 off|events|full:off=不落盘;events=raw envelope 层;"
        "full=预留(A2 transcript)。仅当 archive_root 设置时生效。重启级。",
        env_aliases=["WEB_CHAT_ARCHIVE_MODE"],
    )
    mcp_config: str | None = config_field(
        default='{"mcpServers": {}}',
        description="Process-default MCP config for the model CLI (inline JSON or a "
        "path). Default / empty => no MCP servers (skips ~1.4s cold-start). A "
        "profile's channels.web.mcp_servers OVERRIDES this per web session (web is a "
        "gateway-only consumer: no /mnt/brain mount, design §5.3). 重启级。",
        env_aliases=["WEB_CHAT_MCP_CONFIG"],
    )
    show_tool_calls: bool = config_field(
        default=False,
        description="Reveal which tools ran. Off => a single generic “查阅中…” "
        "status frame while streaming (mirrors QQ_SHOW_TOOL_CALLS).",
        env_aliases=["WEB_CHAT_SHOW_TOOL_CALLS"],
    )
    run_timeout_seconds: float = config_field(
        default=600.0,
        description="Hard wall-clock ceiling per model run; a hung run is killed + "
        "reported.",
        env_aliases=["WEB_CHAT_RUN_TIMEOUT_SECONDS"],
    )
    read_only: bool = config_field(
        default=False,
        description="Process-wide read-only door (WEB_CHAT_READ_ONLY=1). MONOTONIC: "
        "True forces read-only ON for EVERY web profile; it can only STRENGTHEN a "
        "profile's own read_only, never weaken it (fail-closed union). 重启级,不热切。",
        env_aliases=[READ_ONLY_ENV],
    )
    log_level: str = config_field(
        default="INFO",
        description="Log level. WEB_CHAT_DEBUG=1 forces DEBUG (overrides "
        "WEB_CHAT_LOG_LEVEL) in from_env.",
        env_aliases=["WEB_CHAT_LOG_LEVEL"],
    )

    @field_validator("read_only", mode="before")
    @classmethod
    def _coerce_read_only(cls, v: object) -> bool:
        # Match the QQ semantics: only "1" is truthy; "0"/""/unset => False. (Direct
        # construction with a bool passes through.)
        if isinstance(v, bool):
            return v
        if v is None:
            return False
        return str(v).strip() == "1"

    @property
    def model_enabled(self) -> bool:
        return bool(self.claude_cli)

    @classmethod
    def from_env(cls) -> "WebChatConfig":
        """Load from process env, reproducing the legacy parsing exactly.

        pydantic-settings handles the plain WEB_CHAT_* / alias reads; the handful of
        transforms it cannot express declaratively (PATH lookup for the CLI,
        WEB_CHAT_DEBUG->DEBUG override, ~ expansion, the state-dir default, the
        empty-mcp sentinel) are applied here so behaviour is byte-identical to the
        old dataclass ``from_env``.
        """
        claude_cli = (
            os.getenv("CLAUDE_CLI_PATH", "").strip() or shutil.which("claude") or ""
        )
        return cls(
            bind=os.getenv("WEB_CHAT_BIND", "127.0.0.1").strip() or "127.0.0.1",
            port=int(os.getenv("WEB_CHAT_PORT", "8788")),
            state_dir=os.path.expanduser(
                os.getenv("RTIME_WEB_CHAT_STATE_DIR", "").strip()
                or "~/.local/state/rtime-assistant/web-chat"
            ),
            claude_cli=claude_cli,
            model=os.getenv("DEFAULT_MODEL", "").strip(),
            permission_mode=os.getenv("PERMISSION_MODE", "default").strip()
            or "default",
            default_cwd=os.path.expanduser(os.getenv("DEFAULT_CWD", "").strip())
            if os.getenv("DEFAULT_CWD", "").strip()
            else "",
            mcp_config=(
                os.getenv("WEB_CHAT_MCP_CONFIG", "").strip() or '{"mcpServers": {}}'
            ),
            archive_root=(
                os.getenv("WEB_CHAT_ARCHIVE_ROOT", "").strip()
                or os.getenv("RTIME_CHAT_ARCHIVE_ROOT", "").strip()
                or None
            ),
            archive_mode=(
                os.getenv("WEB_CHAT_ARCHIVE_MODE", "").strip().lower() or "events"
            ),
            show_tool_calls=os.getenv("WEB_CHAT_SHOW_TOOL_CALLS", "0") != "0",
            run_timeout_seconds=float(os.getenv("WEB_CHAT_RUN_TIMEOUT_SECONDS", "600")),
            # fail-closed union: only "1" turns it on; env can never disable a profile.
            read_only=read_only_env_forces(),
            log_level=(
                "DEBUG"
                if os.getenv("WEB_CHAT_DEBUG", "0") != "0"
                else os.getenv("WEB_CHAT_LOG_LEVEL", "INFO").strip().upper() or "INFO"
            ),
        )
