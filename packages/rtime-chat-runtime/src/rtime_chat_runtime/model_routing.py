# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared model-routing defaults — opus/sonnet/haiku aliases + canonical default,
sourced from the rtime model registry (packages/rtime-models) with an import-guarded
fallback to identical literals.

Promoted into the shared core (channel-unification P1, decision 2): every channel
resolves /model the same way — alias-resolve a known short name, else pass the raw id
through to the claude-rtime wrapper which routes by --model. The fallback literals are
kept == the registry by scripts/check-entrypoint-drift.py so the two never diverge.
"""

from __future__ import annotations

from dataclasses import dataclass
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

# KEEP IN SYNC: packages/rtime-models/model-registry.json — identical-literal mirror
# used only when the registry package is unavailable (e.g. a stripped container).
_FALLBACK_BASE_ALIASES = {
    "opus": "claude-opus-4-6",
    "sonnet": "claude-sonnet-4-6",
    "haiku": "claude-haiku-4-5-20251001",
}
_FALLBACK_DEFAULT_MODEL = ""


@dataclass(frozen=True)
class ModelChoice:
    """One user-selectable model entry shown by channel slash commands."""

    key: str
    model: str
    label: str
    provider: str
    aliases: tuple[str, ...]
    capabilities: dict


_FALLBACK_MODEL_CHOICES = (
    ModelChoice(
        key="kimi",
        model="",
        label="Gateway default model",
        provider="gateway-default",
        aliases=("kimi",),
        capabilities={"agent_tools": True, "code": True, "chat": True, "vision": False},
    ),
    ModelChoice(
        key="ds",
        model="deepseek-v4-flash-ascend",
        label="deepseek-v4-flash-ascend",
        provider="ustc-openai",
        aliases=("ds", "deepseek"),
        capabilities={"agent_tools": True, "code": False, "chat": True, "vision": False},
    ),
    ModelChoice(
        key="qwen",
        model="qwen3.6-chat",
        label="qwen3.6-chat",
        provider="ustc-openai",
        aliases=("qwen", "qianwen", "qwen-chat"),
        capabilities={"agent_tools": True, "code": False, "chat": True, "vision": False},
    ),
    ModelChoice(
        key="opus",
        model="claude-opus-4-6",
        label="Claude Opus",
        provider="claude-anthropic",
        aliases=("opus",),
        capabilities={"agent_tools": True, "code": True, "chat": True, "vision": True},
    ),
    ModelChoice(
        key="sonnet",
        model="claude-sonnet-4-6",
        label="Claude Sonnet",
        provider="claude-anthropic",
        aliases=("sonnet",),
        capabilities={"agent_tools": True, "code": True, "chat": True, "vision": True},
    ),
)


def base_aliases() -> dict[str, str]:
    """opus/sonnet/haiku -> Claude model ids (the shared /model base alias layer)."""
    if _REG is not None:
        try:
            return dict(_REG.alias_map(["claude-anthropic"]))
        except Exception:
            pass
    return dict(_FALLBACK_BASE_ALIASES)


def default_model() -> str:
    """Canonical default model id. Empty routes through the wrapper default (kimi-code)."""
    if _REG is not None:
        try:
            return _REG.default_model_id()
        except Exception:
            pass
    return _FALLBACK_DEFAULT_MODEL


def model_choices() -> list[ModelChoice]:
    """Ordered model choices for user-facing numbered selection.

    The registry order is the display order. ``key`` is the friendly command alias
    when one exists; ``model`` is the stable CLI value to persist in the session.
    Empty ``model`` means "wrapper default" (currently Kimi).
    """
    if _REG is not None:
        try:
            choices: list[ModelChoice] = []
            for prov in _REG.providers():
                provider_id = str(prov.get("id", ""))
                for item in prov.get("models", []):
                    caps = dict(item.get("capabilities", {}))
                    # Channel slash commands drive the Claude-CLI/agent runner path.
                    # Chat-only catalog models belong to the gateway provider runner and
                    # must not be offered here until that runner is shared.
                    if not caps.get("agent_tools"):
                        continue
                    aliases = tuple(str(a) for a in item.get("aliases", []))
                    model = str(item.get("cli_model", item.get("id", "")))
                    key = aliases[0] if aliases else model
                    choices.append(
                        ModelChoice(
                            key=key,
                            model=model,
                            label=str(item.get("label", item.get("id", key))),
                            provider=provider_id,
                            aliases=aliases,
                            capabilities=caps,
                        )
                    )
            if choices:
                return choices
        except Exception:
            pass
    return list(_FALLBACK_MODEL_CHOICES)


def numbered_model_choice(number: str | int) -> ModelChoice | None:
    """Return the 1-based numbered model choice, or None for invalid input."""
    try:
        index = int(str(number).strip())
    except ValueError:
        return None
    choices = model_choices()
    if 1 <= index <= len(choices):
        return choices[index - 1]
    return None


def model_choice_by_name(name: str) -> ModelChoice | None:
    """Find a choice by alias, key, label, or stored model id."""
    needle = (name or "").strip().lower()
    if not needle:
        return None
    for choice in model_choices():
        haystack = {choice.key.lower(), choice.model.lower(), choice.label.lower()}
        haystack |= {a.lower() for a in choice.aliases}
        if needle in haystack:
            return choice
    return None


def resolve_alias(name: str, extra_aliases: dict[str, str] | None = None) -> str:
    """Resolve a short alias (opus/sonnet/…) to a model id; pass anything else through."""
    name = (name or "").strip()
    if not name:
        return name
    aliases = base_aliases()
    if extra_aliases:
        aliases.update({k.lower(): v for k, v in extra_aliases.items()})
    return aliases.get(name.lower(), name)


# Empirical override (2026-06-30): the Kimi coding endpoint (api.kimi.com/coding,
# the wrapper default) reads images fed via the agent Read tool — verified on the QQ
# bridge container — even though the registry marks kimi-code ``vision: false`` (that
# flag describes native vision-API support, not the Read-tool path the bridges use).
_VISION_OK_SUBSTRINGS = ("kimi", "opus", "sonnet", "haiku")


def model_can_see(model: str | None) -> bool:
    """Whether a model can understand an image fed via the Read tool (channel-unification
    decision 3: don't force-route on media; if the chosen model can't see, the channel
    tells the user instead of silently dropping the image).

    Empty/None => the wrapper default (kimi-code), which can see. A short alias is
    resolved first. Models known to be image-capable return True; a registry model
    explicitly marked ``vision: false`` returns False; unknown ids are optimistic (True)
    so a passthrough model is allowed to try rather than wrongly blocked.
    """
    raw = model or ""
    resolved = resolve_alias(raw)
    choice = model_choice_by_name(resolved) or model_choice_by_name(raw)
    if choice is not None:
        resolved = choice.model
    if not resolved:
        return True  # wrapper default = kimi-code (sees images via Read)
    low = resolved.lower()
    if any(s in low for s in _VISION_OK_SUBSTRINGS):
        return True
    if _REG is not None:
        try:
            for prov in _REG.providers():
                for m in prov.get("models", []):
                    if resolved in (m.get("id"), m.get("cli_model")):
                        caps = m.get("capabilities") or {}
                        return bool(caps.get("vision", False))
        except Exception:
            pass
    return True  # unknown id: don't block; let the model try
