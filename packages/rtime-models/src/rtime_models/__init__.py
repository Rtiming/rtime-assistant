# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Lightweight, pure-stdlib loader for the rtime model registry.

``packages/rtime-models/model-registry.json`` is the single non-secret source of
truth for the model directory + routing. This module reads it and exposes a small
helper API so every consumer (the Obsidian gateway catalog, the Feishu bridge,
the ``claude-rtime`` router, and the generated bash ``model-defaults.sh``) projects
*one* set of defaults instead of carrying its own drifting copy.

Design rules (see docs/maintainability-standards.zh-CN.md §三 P3):
  - Secrets never live in the registry. Providers list ``secret_env_names``; the
    live secret is read from env/keyfile by the consumer at runtime.
  - alias / model-set / tier / base_url values in the registry are DEFAULTS only.
    Each consumer keeps its env override (RTIME_*_MODELS, MODEL_ALIASES_JSON,
    RTIME_*_MODEL, the *_BASE_URL envs). The registry only supplies the fallback.
  - Pure standard library, offline, no third-party deps — so it is importable from
    the gateway (systemd, repo checkout) and the Feishu/CLI runtime (Docker) alike,
    and consumers that cannot import it (bash wrappers) read the generated
    ``model-defaults.sh`` instead.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

__all__ = [
    "CAPABILITY_KEYS",
    "registry_path",
    "load_registry",
    "providers",
    "provider",
    "catalog_providers",
    "default_model_id",
    "base_url",
    "base_url_cfg_key",
    "secret_env_names",
    "secret_file_cfg_key",
    "file_extract_provider_ids",
    "provider_supports_file_extract",
    "catalog_model_ids",
    "catalog_models_env",
    "capability_rule",
    "routing_model_ids",
    "code_model_ids",
    "code_models_with_aliases",
    "feishu_model_aliases",
    "tiers",
    "model",
    "model_capabilities",
    "alias_map",
    "render_bash_defaults",
]

# The capability schema. Kept here as the single list of capability field names so
# the Obsidian TS interface (AssistantModelCapabilities) can be drift-checked
# against it. KEEP IN SYNC: apps/obsidian-rtime-assistant/src/types.ts.
CAPABILITY_KEYS = (
    "agent_tools",
    "code",
    "chat",
    "vision",
    "file_extract",
    "long_context",
    "thinking",
)

_DEFAULT_REGISTRY = Path(__file__).resolve().parents[2] / "model-registry.json"

_CACHE: dict[str, dict] = {}


def registry_path() -> Path:
    """Path to the registry JSON. ``RTIME_MODEL_REGISTRY`` overrides the default
    (packages/rtime-models/model-registry.json, located relative to this file)."""
    override = os.environ.get("RTIME_MODEL_REGISTRY", "").strip()
    return Path(override).expanduser() if override else _DEFAULT_REGISTRY


def load_registry(force_reload: bool = False) -> dict:
    """Load + cache the registry. Cached per resolved path so a test that points
    ``RTIME_MODEL_REGISTRY`` at a fixture does not poison the real one."""
    path = str(registry_path())
    if force_reload or path not in _CACHE:
        with open(path, encoding="utf-8") as handle:
            _CACHE[path] = json.load(handle)
    return _CACHE[path]


def providers() -> list[dict]:
    return list(load_registry().get("providers", []))


def provider(provider_id: str) -> dict | None:
    for item in load_registry().get("providers", []):
        if item.get("id") == provider_id:
            return item
    return None


def catalog_providers() -> list[dict]:
    """Providers that appear in the gateway/Obsidian static catalog (catalog=true)."""
    return [p for p in load_registry().get("providers", []) if p.get("catalog")]


def default_model_id() -> str:
    """Canonical default model id. Empty string routes through the configured
    wrapper default (claude-kimi -> kimi-code)."""
    return str(load_registry().get("default_model", ""))


def base_url(provider_id: str) -> str | None:
    p = provider(provider_id)
    return p.get("base_url") if p else None


def base_url_cfg_key(provider_id: str) -> str | None:
    p = provider(provider_id)
    return p.get("base_url_cfg_key") if p else None


def secret_env_names(provider_id: str) -> list[str]:
    p = provider(provider_id)
    return list(p.get("secret_env_names", [])) if p else []


def secret_file_cfg_key(provider_id: str) -> str | None:
    """cfg key holding an extra literal keyfile path to try for this provider
    (gateway cfg default), beyond the ``*_FILE`` env vars in secret_env_names."""
    p = provider(provider_id)
    return p.get("secret_file_cfg_key") if p else None


def file_extract_provider_ids() -> set[str]:
    """Providers whose chat models support server-side file extraction (Moonshot).
    Drives the file_extract special-case without hard-coding a provider id."""
    return {p["id"] for p in load_registry().get("providers", []) if p.get("file_extract")}


def provider_supports_file_extract(provider_id: str) -> bool:
    return provider_id in file_extract_provider_ids()


def catalog_model_ids(provider_id: str) -> list[str]:
    """Default ordered model-id list shown in the gateway catalog for this provider.
    Empty when the provider has a fixed ``models`` list instead of ``catalog_models``."""
    p = provider(provider_id)
    return list(p.get("catalog_models", [])) if p else []


def catalog_models_env(provider_id: str) -> str | None:
    p = provider(provider_id)
    return p.get("catalog_models_env") if p else None


def capability_rule(provider_id: str) -> str | None:
    """Name of the capability-computation rule for env-added/dynamic models of this
    provider (the gateway keeps the rule in code; the registry only names it)."""
    p = provider(provider_id)
    return p.get("capability_rule") if p else None


def routing_model_ids(provider_id: str) -> list[str]:
    """Model ids treated as this provider's chat models for CLI routing."""
    p = provider(provider_id)
    return list(p.get("routing_models", [])) if p else []


def code_model_ids(provider_id: str) -> list[str]:
    """Model ids that route to this provider's code wrapper."""
    p = provider(provider_id)
    return list(p.get("code_models", [])) if p else []


def code_models_with_aliases(provider_id: str) -> set[str]:
    """code_models plus the aliases of this provider's models — the set the Feishu
    bridge uses to recognise a code model whether the stored value is a resolved
    id or an alias."""
    result = set(code_model_ids(provider_id))
    result |= set(alias_map([provider_id]).keys())
    return result


def tiers(provider_id: str) -> dict:
    p = provider(provider_id)
    return dict(p.get("tiers", {})) if p else {}


def model(provider_id: str, model_id: str) -> dict | None:
    p = provider(provider_id)
    if not p:
        return None
    for item in p.get("models", []):
        if item.get("id") == model_id:
            return item
    return None


def model_capabilities(provider_id: str, model_id: str) -> dict | None:
    item = model(provider_id, model_id)
    return dict(item.get("capabilities", {})) if item else None


def alias_map(provider_ids: list[str] | None = None) -> dict[str, str]:
    """Map alias -> target model id, gathered from every model's ``aliases``.
    ``provider_ids`` limits the gathering to the named providers (used to build the
    distinct alias layers: claude-anthropic for the Feishu base, the code/ustc
    providers for the claude-rtime router)."""
    out: dict[str, str] = {}
    for p in load_registry().get("providers", []):
        if provider_ids is not None and p.get("id") not in provider_ids:
            continue
        for item in p.get("models", []):
            target = item.get("id", "")
            for alias in item.get("aliases", []):
                out[str(alias)] = target
    return out


def feishu_model_aliases() -> dict[str, str]:
    """The canonical Feishu ``MODEL_ALIASES_JSON`` override map: every model alias
    except the opus/sonnet/haiku base (which the bridge always loads via
    bot_config). This is what the .env examples document; the drift gate asserts
    each example's MODEL_ALIASES_JSON equals this."""
    base = set(alias_map(["claude-anthropic"]))
    return {a: t for a, t in alias_map().items() if a not in base}


def render_bash_defaults() -> str:
    """Render deploy/bin/model-defaults.sh from the registry. Sourced by the bash
    wrappers (claude-deepseek/claude-qwen/claude-kimi) so their tier->model and
    base-url DEFAULTS come from the registry while their ``${ENV:-default}`` override
    semantics are preserved. Regenerate-and-diff gated by check-entrypoint-drift.py."""
    deepseek = provider("deepseek-code") or {}
    qwen = provider("qwen-code") or {}
    kimi = provider("kimi-code-wrapper") or {}
    ustc = provider("ustc-openai") or {}
    ollama = provider("ollama") or {}
    ds_tiers = deepseek.get("tiers", {})
    qwen_tiers = qwen.get("tiers", {})
    ustc_tiers = ustc.get("tiers", {})
    ollama_tiers = ollama.get("tiers", {})
    lines = [
        "#!/usr/bin/env bash",
        "# GENERATED — DO NOT EDIT. source: packages/rtime-models/model-registry.json",
        "# regen: python -m rtime_models gen-bash-defaults > deploy/bin/model-defaults.sh",
        "# Non-secret model DEFAULTS for the claude-* provider wrappers. Each wrapper",
        "# keeps its ${RTIME_*:-default} override; this file only supplies the default.",
        "",
        "# deepseek-code (claude-deepseek)",
        f"REG_DEEPSEEK_MODEL={_sh_quote(ds_tiers.get('default', ''))}",
        f"REG_DEEPSEEK_FAST_MODEL={_sh_quote(ds_tiers.get('fast', ''))}",
        f"REG_DEEPSEEK_BASE_URL={_sh_quote(deepseek.get('base_url', ''))}",
        "",
        "# qwen-code (claude-qwen)",
        f"REG_QWEN_MODEL={_sh_quote(qwen_tiers.get('default', ''))}",
        f"REG_QWEN_FAST_MODEL={_sh_quote(qwen_tiers.get('fast', ''))}",
        f"REG_QWEN_QUALITY_MODEL={_sh_quote(qwen_tiers.get('quality', ''))}",
        f"REG_QWEN_BASE_URL={_sh_quote(qwen.get('base_url', ''))}",
        "",
        "# kimi-code (claude-kimi)",
        f"REG_KIMI_MODEL={_sh_quote((kimi.get('models') or [{}])[0].get('cli_model', ''))}",
        f"REG_KIMI_BASE_URL={_sh_quote(kimi.get('base_url', ''))}",
        "",
        "# ustc-openai agent path (claude-ustc via LiteLLM; base URL is deployment",
        "# topology -> RTIME_LITELLM_BASE_URL, deliberately not a registry value)",
        f"REG_USTC_MODEL={_sh_quote(ustc_tiers.get('default', ''))}",
        "",
        "# ollama (claude-ollama)",
        f"REG_OLLAMA_MODEL={_sh_quote(ollama_tiers.get('default', ''))}",
        f"REG_OLLAMA_BASE_URL={_sh_quote(ollama.get('base_url', ''))}",
        "",
    ]
    return "\n".join(lines)


def _sh_quote(value) -> str:
    """Single-quote a value for safe bash assignment (model ids can contain [1m])."""
    text = "" if value is None else str(value)
    return "'" + text.replace("'", "'\\''") + "'"
