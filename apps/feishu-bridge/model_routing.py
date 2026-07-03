# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Feishu bridge model-routing defaults, sourced from the rtime model registry
(packages/rtime-models/model-registry.json) with an import-guarded fallback.

The production Feishu image ships the registry package, so these read it live; a
stripped environment without the package falls back to identical literals. The
fallback literals are kept == the registry by scripts/check-entrypoint-drift.py,
so the two can never silently diverge. Env overrides (RTIME_USTC_MODELS,
RTIME_DEEPSEEK_CODE_MODELS, RTIME_QWEN_CODE_MODELS, MODEL_ALIASES_JSON,
DEFAULT_MODEL) still win in the consumers — these are the defaults only.
"""
from __future__ import annotations

import sys
from pathlib import Path


def _load_registry():
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "packages" / "rtime-models" / "src"
        if candidate.is_dir():
            sp = str(candidate)
            if sp not in sys.path:
                sys.path.insert(0, sp)
            break
    try:
        import rtime_models

        return rtime_models
    except Exception:
        return None


_REG = _load_registry()

# KEEP IN SYNC: packages/rtime-models/model-registry.json — these fallbacks are the
# identical-literal mirror used only when the registry package is unavailable.
# check-entrypoint-drift.py asserts each equals the registry projection.
_FALLBACK_BASE_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
_FALLBACK_DEFAULT_MODEL = ""
_FALLBACK_USTC_CHAT_MODELS = {
    "deepseek-v4-flash-ascend",
    "qwen-chat",
    "qwen-reasoner",
    "qwen3.6-chat",
    "qwen3.6-reasoner",
    "smart/default",
    "smart/reasoning",
}
_FALLBACK_DEEPSEEK_CODE_MODELS = {
    "deepseek-v4-pro",
    "deepseek-v4-pro[1m]",
    "deepseek-v4-flash",
    "deepseek-code",
    "ds-code",
    "deepseek-coder",
}
_FALLBACK_QWEN_CODE_MODELS = {
    "qwen3-coder-next",
    "qwen3-coder-plus",
    "qwen3-coder-plus-2025-09-23",
    "qwen3-coder-flash",
    "qwen-code",
    "qwen-coder",
}
_FALLBACK_OLLAMA_MODELS = {
    "qwen3.5:9b",
    "qwen2.5:3b",
}


def base_aliases() -> dict[str, str]:
    """opus/sonnet/haiku -> Claude model ids (the Feishu /model base alias layer)."""
    if _REG is not None:
        return dict(_REG.alias_map(["claude-anthropic"]))
    return dict(_FALLBACK_BASE_ALIASES)


def default_model() -> str:
    """Canonical default model. Empty routes through the wrapper default (kimi-code)."""
    if _REG is not None:
        return _REG.default_model_id()
    return _FALLBACK_DEFAULT_MODEL


def ustc_chat_models() -> set[str]:
    if _REG is not None:
        return set(_REG.routing_model_ids("ustc-openai"))
    return set(_FALLBACK_USTC_CHAT_MODELS)


def deepseek_code_models() -> set[str]:
    if _REG is not None:
        return _REG.code_models_with_aliases("deepseek-code")
    return set(_FALLBACK_DEEPSEEK_CODE_MODELS)


def qwen_code_models() -> set[str]:
    if _REG is not None:
        return _REG.code_models_with_aliases("qwen-code")
    return set(_FALLBACK_QWEN_CODE_MODELS)


def ollama_models() -> set[str]:
    """Local Ollama models routed through the claude-ollama wrapper (agent tools,
    Jetson-slow). Env override: RTIME_OLLAMA_MODELS in the consumer."""
    if _REG is not None:
        return set(_REG.routing_model_ids("ollama"))
    return set(_FALLBACK_OLLAMA_MODELS)
