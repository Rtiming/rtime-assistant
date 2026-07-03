# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Gateway configuration loader — compatibility layer over the schema model.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Builds the per-process config dict the gateway consumes from environment variables.

P2 config 收编 批 2 (see docs/design/config-full-coverage-plan-2026-07.zh-CN.md
§二 批 2 + docs/reference/assistant-gateway-config.zh-CN.md): the env surface is now
the single source of truth ``AssistantGatewayConfig`` (gateway_config_schema.py, an
import-safe rtime-config pydantic-settings model registered as admin-core module
``assistant-gateway``). ``load_config`` is now a thin adapter: it loads ONE
``AssistantGatewayConfig.from_env()`` and re-assembles the historical dict shape —
same keys, same ``Path`` typing, same ``rtime_models.base_url`` fallbacks, same
``sanitize_permission_mode`` / ``access_mode`` transforms. Behaviour is unchanged:
every entry equals what the old ``os.environ.get`` block produced, byte-for-byte.

The ``Path.home()`` / brain_root / log_dir joins that give empty schema fields their
concrete default live HERE (not in the schema module) so that module stays strictly
import-safe (no ``Path.home()`` / ``rtime_models`` at import) — admin-core imports it
lazily to register the module and must not perform work.
"""

from __future__ import annotations

from pathlib import Path

import _shared_runtime  # noqa: F401 — side effect: put rtime_config/rtime_models on path
from _common import (
    DEFAULT_PERMISSION_MODE,
    access_mode,
    sanitize_permission_mode,
)
from gateway_config_schema import AssistantGatewayConfig

# The historical HOME-relative log_dir tail, used when GATEWAY_LOG_DIR is unset.
_DEFAULT_LOG_DIR_TAIL = ".local/state/rtime-assistant/assistant-gateway"


def load_config() -> dict:
    home = Path.home()
    cfg = AssistantGatewayConfig.from_env()
    brain_root = Path(cfg.brain_root)
    log_dir = Path(cfg.log_dir) if cfg.log_dir else home / _DEFAULT_LOG_DIR_TAIL
    return {
        "bind": cfg.bind,
        # KEEP IN SYNC: default port 8765 is also hard-coded in the Obsidian
        # plugin (apps/obsidian-rtime-assistant — src/settings.ts, dev/*.mjs),
        # the deploy env (deploy/env/assistant-gateway.env.example,
        # deploy/launchd/install-mac-obsidian-gateway.sh), and the docs. See
        # docs/maintainability-standards.zh-CN.md before changing the default.
        "port": cfg.port,
        "brain_root": brain_root,
        "claude_bin": cfg.claude_bin or str(home / ".local/bin/claude-kimi"),
        "claude_timeout": cfg.claude_timeout,
        # Tool turns are UNCAPPED by default (bounded by claude_timeout). A low
        # fixed cap truncated multi-PDF/dedup answers, so there is no default
        # --max-turns. A cap is opt-in: globally via CLAUDE_MAX_TURNS, or
        # per-request via options.max_tool_turns (plugin setting). Empty/0 = no cap.
        "claude_max_turns": cfg.claude_max_turns,
        # Per-tier *timeouts* still apply (turn count is never limited here).
        "claude_investigation_timeout": cfg.claude_investigation_timeout,
        "claude_web_timeout": cfg.claude_web_timeout,
        "claude_runtime_diag_timeout": cfg.claude_runtime_diag_timeout,
        "claude_bare": cfg.claude_bare,
        "claude_no_session_persistence": cfg.claude_no_session_persistence,
        "claude_exclude_dynamic_sections": cfg.claude_exclude_dynamic_sections,
        "claude_permission_mode": sanitize_permission_mode(
            cfg.claude_permission_mode or None, DEFAULT_PERMISSION_MODE
        ),
        "approval_forwarding_enabled": cfg.approval_forwarding_enabled,
        "gateway_access_mode": access_mode(cfg.gateway_access_mode),
        "web_tools_enabled": cfg.web_tools_enabled,
        "extra_allowed_tools": cfg.extra_allowed_tools,
        "index_pythonpath": (
            cfg.index_pythonpath
            or str(home / "rtime-assistant/packages/brain-library/src")
        ),
        "index_db": (
            cfg.index_db
            or str(
                home / ".local/state/rtime-assistant/brain-library/brain-library.sqlite"
            )
        ),
        "log_dir": log_dir,
        "memory_capture_enabled": cfg.memory_capture_enabled,
        "memory_failed_query_log_enabled": cfg.memory_failed_query_log_enabled,
        "memory_capture_max_chars": cfg.memory_capture_max_chars,
        "memory_injection_enabled": cfg.memory_injection_enabled,
        "memory_root": Path(cfg.memory_root)
        if cfg.memory_root
        else brain_root / "memory",
        "memory_injection_max_cards": cfg.memory_injection_max_cards,
        "memory_injection_max_chars": cfg.memory_injection_max_chars,
        "memory_access_log_enabled": cfg.memory_access_log_enabled,
        "context_sources_enabled": cfg.context_sources_enabled,
        "context_sources_path": (
            Path(cfg.context_sources_path)
            if cfg.context_sources_path
            else brain_root / "_system/rtime-context-sources.jsonl"
        ),
        "context_sources_max_items": cfg.context_sources_max_items,
        "context_sources_max_chars": cfg.context_sources_max_chars,
        "memory_candidate_write_enabled": cfg.memory_candidate_write_enabled,
        "memory_candidate_review_dir": (
            Path(cfg.memory_candidate_review_dir)
            if cfg.memory_candidate_review_dir
            else brain_root / "memory/review-queue"
        ),
        "relations_path": (
            Path(cfg.relations_path)
            if cfg.relations_path
            else brain_root / "_indexes/relations.jsonl"
        ),
        "related_prefetch_limit": cfg.related_prefetch_limit,
        "related_prefetch_max_chars": cfg.related_prefetch_max_chars,
        "queue_max": cfg.queue_max,
        "queue_wait_timeout": cfg.queue_wait_timeout,
        "queue_heartbeat_secs": cfg.queue_heartbeat_secs,
        "prepare_cache_ttl": cfg.prepare_cache_ttl,
        "prepare_cache_max": cfg.prepare_cache_max,
        "prewarm_enabled": cfg.prewarm_enabled,
        "live_prewarm_enabled": cfg.live_prewarm_enabled,
        "live_prewarm_idle_seconds": cfg.live_prewarm_idle_seconds,
        "prewarm_ttl_seconds": cfg.prewarm_ttl_seconds,
        "prewarm_timeout": cfg.prewarm_timeout,
        "history_max_chars": cfg.history_max_chars,
        "stream_trace_enabled": cfg.stream_trace_enabled,
        "intake_max_mb": cfg.intake_max_mb,
        "file_extract_max_files": cfg.file_extract_max_files,
        "file_extract_max_chars": cfg.file_extract_max_chars,
        "notify_target": cfg.notify_target,
        "reminder_register": (
            cfg.reminder_register or str(home / ".local/bin/rtime-reminder-register")
        ),
        "model_catalog_path": (
            Path(cfg.model_catalog_path)
            if cfg.model_catalog_path
            else log_dir / "model-catalog.json"
        ),
        "plugin_release_dir": (
            Path(cfg.plugin_release_dir)
            if cfg.plugin_release_dir
            else home / ".local/share/rtime-assistant/plugin-release/rtime-assistant"
        ),
        "model_refresh_timeout": cfg.model_refresh_timeout,
        "moonshot_base_url": cfg.moonshot_base_url,
        "ustc_base_url": cfg.ustc_base_url,
        "ustc_api_key_file": Path(
            cfg.ustc_api_key_file or str(home / ".config/rtime-assistant/ustc-api-key")
        ),
    }
