# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema-driven config for the assistant-gateway — the ``AssistantGatewayConfig`` model.

P2 config 收编 (批 2, see docs/design/config-full-coverage-plan-2026-07.zh-CN.md
§二 批 2 + docs/reference/config-coverage.zh-CN.md): the ``os.environ`` surface the
Obsidian backend gateway reads at startup (``gateway_config.load_config`` plus the
one gateway-client base URL in ``rtime_chat``) is expressed here as
``AssistantGatewayConfig`` — a ``rtime-config`` pydantic-settings model. One source
of truth for validation, defaults, descriptions and JSON Schema (docs/config/
assistant-gateway.md is generated from it), REGISTERED into the admin-core registry
as module ``assistant-gateway`` so the panel / config-agent can manage it (全覆盖).

This module is DELIBERATELY import-safe (no side effects beyond putting
rtime_config on sys.path via ``_shared_runtime``; no ``rtime_models`` import, no
``Path.home()`` resolution, no env reads at import), exactly like the feishu-bridge
pilot's ``feishu_config.py``: admin-core lazily imports ``AssistantGatewayConfig``
from here to register the module, and it must not raise or perform work at import.
The runtime ``load_config()`` dict the gateway consumes — with the ``Path``
resolution, ``rtime_models.base_url`` fallbacks, ``env_bool`` / ``sanitize_*`` /
``access_mode`` transforms — lives in the ``gateway_config`` compatibility layer,
which builds it from a single ``AssistantGatewayConfig.from_env()`` load.

This is BEHAVIOUR-PRESERVING: every field default matches the old ``load_config``
value, every legacy env name still loads the value (via ``env_aliases`` ->
``AliasChoices``), and ``from_env`` + ``gateway_config.load_config`` reproduce the
legacy env parsing quirks (``env_bool`` truthiness, ``sanitize_permission_mode``,
``access_mode``, ``rstrip('/')`` on base URLs, ``~`` / ``$HOME`` path defaults,
the ``rtime_models.base_url`` provider fallback) byte-for-byte.

Secrets: ``ustc_api_key_file`` is a ``config_field`` (the PATH is not itself a
secret — the file holds the key, read by ``_read_secret`` in ``models.py``); there
is no plaintext key field here (keys stay on the backend host in keyfiles), so this
module declares NO ``secret_field``. The gateway does not embed credentials.

Naming note: ``env_prefix=""`` on purpose — every field declares its COMPLETE set
of accepted env names via ``env_aliases`` (mirrors QQBridgeConfig / FeishuBridge
Config / WebChatConfig), so the accepted surface equals exactly what is declared.
"""

from __future__ import annotations

import os

import _shared_runtime  # noqa: F401 — side effect: put rtime_config on sys.path
from pydantic_settings import SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field

# --- legacy defaults, copied verbatim from gateway_config.load_config() --------
# Only the CONSTANT (non-path-relative) defaults live here as field defaults. The
# HOME-/brain_root-/log_dir-relative path defaults are intentionally NOT modelled as
# field defaults — those fields use an empty sentinel and the compat layer
# (gateway_config.load_config) resolves them against Path.home() etc., keeping THIS
# module import-safe (no Path.home() at import) with ONE owner for each tail string.
DEFAULT_BRAIN_ROOT = "/mnt/brain"
DEFAULT_BIND = "127.0.0.1"
# KEEP IN SYNC: default port 8765 is also hard-coded in the Obsidian plugin
# (apps/obsidian-rtime-assistant — src/settings.ts, dev/*.mjs), the deploy env
# (deploy/env/assistant-gateway.env.example,
# deploy/launchd/install-mac-obsidian-gateway.sh), and the docs. See
# docs/maintainability-standards.zh-CN.md before changing the default.
DEFAULT_PORT = 8765
# The gateway client base URL default (rtime_chat.DEFAULT_ENDPOINT).
DEFAULT_GATEWAY_URL = "http://127.0.0.1:8765"


class AssistantGatewayConfig(RtimeBaseSettings):
    # env_prefix="" (not "GATEWAY_"): fields carry mixed prefixes (GATEWAY_* /
    # CLAUDE_* / MEMORY_* / QUEUE_* / INDEX_* / RTIME_* / BRAIN_ROOT / HISTORY_*),
    # so each declares its COMPLETE accepted env name(s) via env_aliases and the
    # accepted surface equals exactly what x-env-aliases documents — no implicit
    # prefix-derived names silently widening it. Behaviour-preserving.
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    # --- transport / bind -----------------------------------------------------
    bind: str = config_field(
        default=DEFAULT_BIND,
        description="Listen address. Bind the Tailscale address only in prod; never "
        "0.0.0.0 on a public host.",
        scope="write:channel",
        env_aliases=["GATEWAY_BIND"],
    )
    port: int = config_field(
        default=DEFAULT_PORT,
        description="Listen port. KEEP IN SYNC with the Obsidian plugin / deploy env "
        "/ docs default 8765 (see maintainability-standards.zh-CN.md §三).",
        ge=1,
        le=65535,
        scope="write:channel",
        env_aliases=["GATEWAY_PORT"],
    )

    # --- brain / index paths (deploy-injected roots; kept env-overridable) -----
    brain_root: str = config_field(
        default=DEFAULT_BRAIN_ROOT,
        description="Brain library root the gateway reads (memory, context sources, "
        "relations default under it).",
        scope="write:library",
        env_aliases=["BRAIN_ROOT"],
    )
    index_pythonpath: str = config_field(
        default="",
        description="PYTHONPATH shim for the in-process brain index "
        "(packages/brain-library/src). Empty => $HOME/rtime-assistant/packages/"
        "brain-library/src (from_env).",
        scope="write:library",
        env_aliases=["INDEX_PYTHONPATH"],
    )
    index_db: str = config_field(
        default="",
        description="Brain index sqlite path. Empty => "
        "$HOME/.local/state/rtime-assistant/brain-library/brain-library.sqlite.",
        scope="write:library",
        env_aliases=["INDEX_DB"],
    )
    log_dir: str = config_field(
        default="",
        description="Gateway run-log / state dir. Empty => "
        "$HOME/.local/state/rtime-assistant/assistant-gateway.",
        scope="write:channel",
        env_aliases=["GATEWAY_LOG_DIR"],
    )

    # --- claude CLI + timeouts ------------------------------------------------
    claude_bin: str = config_field(
        default="",
        description="claude CLI / claude-kimi wrapper path. Empty => "
        "$HOME/.local/bin/claude-kimi (from_env).",
        scope="write:channel",
        env_aliases=["CLAUDE_BIN"],
    )
    claude_timeout: int = config_field(
        default=110,
        description="Per-request model wall-clock timeout (seconds).",
        ge=1,
        scope="write:channel",
        env_aliases=["CLAUDE_TIMEOUT"],
    )
    claude_max_turns: str = config_field(
        default="",
        description="Global tool-turn cap. Empty/0 = UNCAPPED (bounded by "
        "claude_timeout); a low fixed cap truncated multi-PDF/dedup answers. Opt-in "
        "only. Per-request options.max_tool_turns overrides.",
        scope="write:channel",
        env_aliases=["CLAUDE_MAX_TURNS"],
    )
    claude_investigation_timeout: int = config_field(
        default=180,
        description="Longer timeout for duplicate-check / multi-file PDF/slide scans.",
        ge=1,
        scope="write:channel",
        env_aliases=["CLAUDE_INVESTIGATION_TIMEOUT"],
    )
    claude_web_timeout: int = config_field(
        default=170,
        description="Longer timeout for public web/search requests "
        "(search + fetch + final answer).",
        ge=1,
        scope="write:channel",
        env_aliases=["CLAUDE_WEB_TIMEOUT"],
    )
    claude_runtime_diag_timeout: int = config_field(
        default=90,
        description="Runtime-error follow-ups call the model on redacted gateway logs.",
        ge=1,
        scope="write:channel",
        env_aliases=["CLAUDE_RUNTIME_DIAG_TIMEOUT"],
    )
    claude_bare: bool = config_field(
        default=True,
        description="Claude Code startup trims for the Obsidian gateway. Auto-skipped "
        "by the gateway when extra_allowed_tools contains mcp__.",
        scope="write:channel",
        env_aliases=["CLAUDE_BARE"],
    )
    claude_no_session_persistence: bool = config_field(
        default=True,
        description="Disable Claude Code session persistence for gateway runs.",
        scope="write:channel",
        env_aliases=["CLAUDE_NO_SESSION_PERSISTENCE"],
    )
    claude_exclude_dynamic_sections: bool = config_field(
        default=True,
        description="Exclude dynamic CLAUDE.md sections at startup for the gateway.",
        scope="write:channel",
        env_aliases=["CLAUDE_EXCLUDE_DYNAMIC_SECTIONS"],
    )
    claude_permission_mode: str = config_field(
        default="dontAsk",
        description="Default Claude Code permission mode for tool-capable routes. "
        "Sanitized to a known mode; per-request may override in readonly; full mode "
        "forces bypassPermissions.",
        scope="write:channel",
        env_aliases=["CLAUDE_PERMISSION_MODE"],
    )

    # --- access / tools -------------------------------------------------------
    approval_forwarding_enabled: bool = config_field(
        default=True,
        description="Forward tool-approval prompts to the client.",
        scope="write:channel",
        env_aliases=["GATEWAY_APPROVAL_FORWARDING"],
    )
    gateway_access_mode: str = config_field(
        default="readonly",
        description="Access mode. Default readonly; 'full' only for trusted, "
        "owner-operated endpoints (normalized via access_mode()).",
        scope="write:channel",
        env_aliases=["GATEWAY_ACCESS_MODE"],
    )
    web_tools_enabled: bool = config_field(
        default=True,
        description="Enable Claude Code built-in WebSearch/WebFetch + the controlled "
        "read-only rtime-web-fetch fallback.",
        scope="write:channel",
        env_aliases=["GATEWAY_WEB_TOOLS_ENABLED"],
    )
    extra_allowed_tools: str = config_field(
        default="",
        description="Comma-separated extra Claude Code tool names or MCP tool globs "
        "(e.g. mcp__browser__*). Only names implemented by the live runner/MCP config "
        "are installed.",
        scope="write:channel",
        env_aliases=["GATEWAY_EXTRA_ALLOWED_TOOLS"],
    )

    # --- memory loop ----------------------------------------------------------
    memory_capture_enabled: bool = config_field(
        default=False,
        description="M9 memory-loop capture. Off by default; run-03 enables after "
        "backup + health checks.",
        env_aliases=["MEMORY_CAPTURE_ENABLED"],
    )
    memory_failed_query_log_enabled: bool = config_field(
        default=False,
        description="Log failed/zero-hit queries for the memory loop. Off by default.",
        env_aliases=["MEMORY_FAILED_QUERY_LOG_ENABLED"],
    )
    memory_capture_max_chars: int = config_field(
        default=800,
        description="Max chars captured per memory-loop record.",
        ge=1,
        env_aliases=["MEMORY_CAPTURE_MAX_CHARS"],
    )
    memory_injection_enabled: bool = config_field(
        default=True,
        description="Approved-memory prompt injection. Reads only brain/memory/cards/ "
        "normal, non-inferred, unexpired cards; review queue is never injected.",
        env_aliases=["MEMORY_INJECTION_ENABLED"],
    )
    memory_root: str = config_field(
        default="",
        description="Memory store root. Empty => brain_root/memory (from_env).",
        scope="write:library",
        env_aliases=["MEMORY_ROOT"],
    )
    memory_injection_max_cards: int = config_field(
        default=3,
        description="Max memory cards injected into a prompt.",
        ge=0,
        env_aliases=["MEMORY_INJECTION_MAX_CARDS"],
    )
    memory_injection_max_chars: int = config_field(
        default=1200,
        description="Max total chars of injected memory cards.",
        ge=1,
        env_aliases=["MEMORY_INJECTION_MAX_CHARS"],
    )
    memory_access_log_enabled: bool = config_field(
        default=True,
        description="Log memory-card access (audit).",
        env_aliases=["MEMORY_ACCESS_LOG_ENABLED"],
    )

    # --- dynamic context sources ---------------------------------------------
    context_sources_enabled: bool = config_field(
        default=True,
        description="Dynamic context sources. Registry is metadata-only JSONL; source "
        "bodies read from safe brain-relative paths only when relevant.",
        env_aliases=["GATEWAY_CONTEXT_SOURCES_ENABLED"],
    )
    context_sources_path: str = config_field(
        default="",
        description="Context-source registry JSONL. Empty => "
        "brain_root/_system/rtime-context-sources.jsonl.",
        scope="write:library",
        env_aliases=["GATEWAY_CONTEXT_SOURCES_PATH"],
    )
    context_sources_max_items: int = config_field(
        default=3,
        description="Max context-source items resolved per request.",
        ge=0,
        env_aliases=["GATEWAY_CONTEXT_SOURCES_MAX_ITEMS"],
    )
    context_sources_max_chars: int = config_field(
        default=5000,
        description="Max total chars of resolved context sources.",
        ge=1,
        env_aliases=["GATEWAY_CONTEXT_SOURCES_MAX_CHARS"],
    )

    # --- memory candidate write (review queue) --------------------------------
    memory_candidate_write_enabled: bool = config_field(
        default=True,
        description="'remember this' intents write review-queue candidates only; they "
        "do not merge into long-term memory/cards without a separate review flow.",
        env_aliases=["GATEWAY_MEMORY_CANDIDATE_WRITE_ENABLED"],
    )
    memory_candidate_review_dir: str = config_field(
        default="",
        description="Review-queue dir for memory candidates. Empty => "
        "brain_root/memory/review-queue.",
        scope="write:library",
        env_aliases=["GATEWAY_MEMORY_CANDIDATE_REVIEW_DIR"],
    )

    # --- derived relation prefetch -------------------------------------------
    relations_path: str = config_field(
        default="",
        description="Relation index JSONL for related/citation-review prefetch. "
        "Empty => brain_root/_indexes/relations.jsonl.",
        scope="write:library",
        env_aliases=["GATEWAY_RELATIONS_PATH"],
    )
    related_prefetch_limit: int = config_field(
        default=5,
        description="Max related items prefetched.",
        ge=0,
        env_aliases=["GATEWAY_RELATED_PREFETCH_LIMIT"],
    )
    related_prefetch_max_chars: int = config_field(
        default=1200,
        description="Max total chars of prefetched related items.",
        ge=1,
        env_aliases=["GATEWAY_RELATED_PREFETCH_MAX_CHARS"],
    )

    # --- queue (v0.3 session protocol) ----------------------------------------
    queue_max: int = config_field(
        default=2,
        description="FIFO queue capacity for busy requests; only a full queue "
        "rejects (503).",
        ge=0,
        scope="write:channel",
        env_aliases=["QUEUE_MAX"],
    )
    queue_wait_timeout: float = config_field(
        default=30.0,
        description="Max seconds a queued request waits before timing out.",
        ge=0,
        scope="write:channel",
        env_aliases=["QUEUE_WAIT_TIMEOUT"],
    )
    queue_heartbeat_secs: float = config_field(
        default=3.0,
        description="Streaming waiters get 排队中… heartbeats this often.",
        ge=0,
        scope="write:channel",
        env_aliases=["QUEUE_HEARTBEAT_SECS"],
    )

    # --- prepare cache + prewarm ----------------------------------------------
    prepare_cache_ttl: int = config_field(
        default=180,
        description="/api/obsidian/prepare short-lived resolved-context TTL (seconds).",
        ge=0,
        env_aliases=["GATEWAY_PREPARE_CACHE_TTL"],
    )
    prepare_cache_max: int = config_field(
        default=64,
        description="Max entries in the prepare cache.",
        ge=1,
        env_aliases=["GATEWAY_PREPARE_CACHE_MAX"],
    )
    prewarm_enabled: bool = config_field(
        default=True,
        description="Optional model prewarm during prepare (plugin must send "
        "options.prewarm_model=true; prepare still returns immediately).",
        env_aliases=["GATEWAY_PREWARM_ENABLED"],
    )
    live_prewarm_enabled: bool = config_field(
        default=True,
        description="Single-use live-stdin prewarm process for tool-capable routes "
        "(preserves model/tool quality). 0 => legacy short prewarm call.",
        env_aliases=["GATEWAY_LIVE_PREWARM_ENABLED"],
    )
    live_prewarm_idle_seconds: int = config_field(
        default=240,
        description="Idle ceiling before a live-prewarm process is recycled.",
        ge=0,
        env_aliases=["GATEWAY_LIVE_PREWARM_IDLE_SECONDS"],
    )
    prewarm_ttl_seconds: int = config_field(
        default=240,
        description="Legacy short-call prewarm TTL (chat-only OpenAI routes + fallback "
        "when live prewarm disabled).",
        ge=0,
        env_aliases=["GATEWAY_PREWARM_TTL_SECONDS"],
    )
    prewarm_timeout: int = config_field(
        default=30,
        description="Legacy short-call prewarm timeout (seconds).",
        ge=1,
        env_aliases=["GATEWAY_PREWARM_TIMEOUT"],
    )

    # --- history / streaming --------------------------------------------------
    history_max_chars: int = config_field(
        default=4000,
        description="context.history is clipped to this many chars (newest turns kept).",
        ge=1,
        env_aliases=["HISTORY_MAX_CHARS"],
    )
    stream_trace_enabled: bool = config_field(
        default=True,
        description="Emit gateway stream-trace diagnostics.",
        env_aliases=["GATEWAY_STREAM_TRACE"],
    )

    # --- intake / file extraction ---------------------------------------------
    intake_max_mb: int = config_field(
        default=64,
        description="Max inbound intake payload size (MB).",
        ge=1,
        env_aliases=["GATEWAY_INTAKE_MAX_MB"],
    )
    file_extract_max_files: int = config_field(
        default=4,
        description="Max attached files whose text is extracted per request.",
        ge=0,
        env_aliases=["GATEWAY_FILE_EXTRACT_MAX_FILES"],
    )
    file_extract_max_chars: int = config_field(
        default=80000,
        description="Max extracted text chars per request.",
        ge=1,
        env_aliases=["GATEWAY_FILE_EXTRACT_MAX_CHARS"],
    )

    # --- notify / reminder ----------------------------------------------------
    notify_target: str = config_field(
        default="",
        description="Optional notify target (feishu open_id / channel) for gateway "
        "alerts.",
        scope="write:channel",
        env_aliases=["GATEWAY_NOTIFY_TARGET"],
    )
    reminder_register: str = config_field(
        default="",
        description="rtime-reminder-register CLI path. Empty => "
        "$HOME/.local/bin/rtime-reminder-register.",
        scope="write:channel",
        env_aliases=["GATEWAY_REMINDER_REGISTER"],
    )

    # --- Obsidian model catalog + plugin release ------------------------------
    model_catalog_path: str = config_field(
        default="",
        description="Obsidian model-catalog cache path. Empty => "
        "log_dir/model-catalog.json.",
        scope="write:models",
        env_aliases=["GATEWAY_MODEL_CATALOG_PATH"],
    )
    plugin_release_dir: str = config_field(
        default="",
        description="Private Obsidian plugin release folder served at "
        "/api/obsidian/plugin-release/. Empty => "
        "$HOME/.local/share/rtime-assistant/plugin-release/rtime-assistant.",
        scope="write:channel",
        env_aliases=["GATEWAY_PLUGIN_RELEASE_DIR"],
    )
    model_refresh_timeout: float = config_field(
        default=8.0,
        description="Model catalog refresh timeout (seconds).",
        ge=0,
        scope="write:models",
        env_aliases=["GATEWAY_MODEL_REFRESH_TIMEOUT"],
    )

    # --- provider base URLs (keys live in keyfiles, not here) -----------------
    moonshot_base_url: str = config_field(
        default="",
        description="Moonshot/Kimi OpenAI base URL. Empty => the rtime-models registry "
        "base_url('moonshot-openai'); trailing slash stripped (from_env).",
        scope="write:models",
        env_aliases=["RTIME_MOONSHOT_BASE_URL"],
    )
    ustc_base_url: str = config_field(
        default="",
        description="USTC OpenAI base URL. Empty => the rtime-models registry "
        "base_url('ustc-openai'); trailing slash stripped (from_env).",
        scope="write:models",
        env_aliases=["RTIME_USTC_BASE_URL"],
    )
    ustc_api_key_file: str = config_field(
        default="",
        description="Path to the USTC API keyfile (the PATH is not secret; the file "
        "holds the key, read by models._read_secret). Empty => "
        "$HOME/.config/rtime-assistant/ustc-api-key.",
        scope="write:models",
        env_aliases=["RTIME_USTC_API_KEY_FILE"],
    )

    # --- gateway client base URL (rtime_chat.py) ------------------------------
    gateway_url: str = config_field(
        default=DEFAULT_GATEWAY_URL,
        description="Base URL the rtime_chat client posts to (default the Tailscale "
        "gateway address).",
        scope="write:channel",
        env_aliases=["RTIME_GATEWAY_URL"],
    )

    @classmethod
    def from_env(cls) -> "AssistantGatewayConfig":
        """Load from process env, reproducing the legacy ``load_config`` parsing.

        Only the LITERAL string-valued env reads are handled by the settings model
        (via env_aliases). The remaining legacy transforms — ``env_bool`` truthiness,
        the ``rtime_models.base_url`` provider fallbacks and ``rstrip('/')`` — are
        applied here so the constructed model is byte-identical to the pre-migration
        values. Path defaults (Path.home()/brain_root joins) stay empty in the model
        and are resolved in ``gateway_config.load_config`` (which owns Path typing),
        keeping this module import-safe (no Path.home()/rtime_models at import).
        """
        import rtime_models  # local: keep the module import-safe (no top-level dep)
        from _common import env_bool

        moonshot_base_url = os.environ.get(
            "RTIME_MOONSHOT_BASE_URL", rtime_models.base_url("moonshot-openai")
        ).rstrip("/")
        ustc_base_url = os.environ.get(
            "RTIME_USTC_BASE_URL", rtime_models.base_url("ustc-openai")
        ).rstrip("/")
        return cls(
            bind=os.environ.get("GATEWAY_BIND", DEFAULT_BIND),
            port=int(os.environ.get("GATEWAY_PORT", str(DEFAULT_PORT))),
            brain_root=os.environ.get("BRAIN_ROOT", DEFAULT_BRAIN_ROOT),
            index_pythonpath=os.environ.get("INDEX_PYTHONPATH", ""),
            index_db=os.environ.get("INDEX_DB", ""),
            log_dir=os.environ.get("GATEWAY_LOG_DIR", ""),
            claude_bin=os.environ.get("CLAUDE_BIN", ""),
            claude_timeout=int(os.environ.get("CLAUDE_TIMEOUT", "110")),
            claude_max_turns=os.environ.get("CLAUDE_MAX_TURNS", ""),
            claude_investigation_timeout=int(
                os.environ.get("CLAUDE_INVESTIGATION_TIMEOUT", "180")
            ),
            claude_web_timeout=int(os.environ.get("CLAUDE_WEB_TIMEOUT", "170")),
            claude_runtime_diag_timeout=int(
                os.environ.get("CLAUDE_RUNTIME_DIAG_TIMEOUT", "90")
            ),
            claude_bare=env_bool("CLAUDE_BARE", "1"),
            claude_no_session_persistence=env_bool(
                "CLAUDE_NO_SESSION_PERSISTENCE", "1"
            ),
            claude_exclude_dynamic_sections=env_bool(
                "CLAUDE_EXCLUDE_DYNAMIC_SECTIONS", "1"
            ),
            claude_permission_mode=os.environ.get("CLAUDE_PERMISSION_MODE", "") or "",
            approval_forwarding_enabled=env_bool("GATEWAY_APPROVAL_FORWARDING", "1"),
            gateway_access_mode=os.environ.get("GATEWAY_ACCESS_MODE", ""),
            web_tools_enabled=env_bool("GATEWAY_WEB_TOOLS_ENABLED", "1"),
            extra_allowed_tools=os.environ.get("GATEWAY_EXTRA_ALLOWED_TOOLS", ""),
            memory_capture_enabled=env_bool("MEMORY_CAPTURE_ENABLED"),
            memory_failed_query_log_enabled=env_bool("MEMORY_FAILED_QUERY_LOG_ENABLED"),
            memory_capture_max_chars=int(
                os.environ.get("MEMORY_CAPTURE_MAX_CHARS", "800")
            ),
            memory_injection_enabled=env_bool("MEMORY_INJECTION_ENABLED", "1"),
            memory_root=os.environ.get("MEMORY_ROOT", ""),
            memory_injection_max_cards=int(
                os.environ.get("MEMORY_INJECTION_MAX_CARDS", "3")
            ),
            memory_injection_max_chars=int(
                os.environ.get("MEMORY_INJECTION_MAX_CHARS", "1200")
            ),
            memory_access_log_enabled=env_bool("MEMORY_ACCESS_LOG_ENABLED", "1"),
            context_sources_enabled=env_bool("GATEWAY_CONTEXT_SOURCES_ENABLED", "1"),
            context_sources_path=os.environ.get("GATEWAY_CONTEXT_SOURCES_PATH", ""),
            context_sources_max_items=int(
                os.environ.get("GATEWAY_CONTEXT_SOURCES_MAX_ITEMS", "3")
            ),
            context_sources_max_chars=int(
                os.environ.get("GATEWAY_CONTEXT_SOURCES_MAX_CHARS", "5000")
            ),
            memory_candidate_write_enabled=env_bool(
                "GATEWAY_MEMORY_CANDIDATE_WRITE_ENABLED", "1"
            ),
            memory_candidate_review_dir=os.environ.get(
                "GATEWAY_MEMORY_CANDIDATE_REVIEW_DIR", ""
            ),
            relations_path=os.environ.get("GATEWAY_RELATIONS_PATH", ""),
            related_prefetch_limit=int(
                os.environ.get("GATEWAY_RELATED_PREFETCH_LIMIT", "5")
            ),
            related_prefetch_max_chars=int(
                os.environ.get("GATEWAY_RELATED_PREFETCH_MAX_CHARS", "1200")
            ),
            queue_max=int(os.environ.get("QUEUE_MAX", "2")),
            queue_wait_timeout=float(os.environ.get("QUEUE_WAIT_TIMEOUT", "30")),
            queue_heartbeat_secs=float(os.environ.get("QUEUE_HEARTBEAT_SECS", "3")),
            prepare_cache_ttl=int(os.environ.get("GATEWAY_PREPARE_CACHE_TTL", "180")),
            prepare_cache_max=int(os.environ.get("GATEWAY_PREPARE_CACHE_MAX", "64")),
            prewarm_enabled=env_bool("GATEWAY_PREWARM_ENABLED", "1"),
            live_prewarm_enabled=env_bool("GATEWAY_LIVE_PREWARM_ENABLED", "1"),
            live_prewarm_idle_seconds=int(
                os.environ.get("GATEWAY_LIVE_PREWARM_IDLE_SECONDS", "240")
            ),
            prewarm_ttl_seconds=int(
                os.environ.get("GATEWAY_PREWARM_TTL_SECONDS", "240")
            ),
            prewarm_timeout=int(os.environ.get("GATEWAY_PREWARM_TIMEOUT", "30")),
            history_max_chars=int(os.environ.get("HISTORY_MAX_CHARS", "4000")),
            stream_trace_enabled=env_bool("GATEWAY_STREAM_TRACE", "1"),
            intake_max_mb=int(os.environ.get("GATEWAY_INTAKE_MAX_MB", "64")),
            file_extract_max_files=int(
                os.environ.get("GATEWAY_FILE_EXTRACT_MAX_FILES", "4")
            ),
            file_extract_max_chars=int(
                os.environ.get("GATEWAY_FILE_EXTRACT_MAX_CHARS", "80000")
            ),
            notify_target=os.environ.get("GATEWAY_NOTIFY_TARGET", ""),
            reminder_register=os.environ.get("GATEWAY_REMINDER_REGISTER", ""),
            model_catalog_path=os.environ.get("GATEWAY_MODEL_CATALOG_PATH", ""),
            plugin_release_dir=os.environ.get("GATEWAY_PLUGIN_RELEASE_DIR", ""),
            model_refresh_timeout=float(
                os.environ.get("GATEWAY_MODEL_REFRESH_TIMEOUT", "8")
            ),
            moonshot_base_url=moonshot_base_url,
            ustc_base_url=ustc_base_url,
            ustc_api_key_file=os.environ.get("RTIME_USTC_API_KEY_FILE", ""),
            gateway_url=os.environ.get("RTIME_GATEWAY_URL", DEFAULT_GATEWAY_URL),
        )
