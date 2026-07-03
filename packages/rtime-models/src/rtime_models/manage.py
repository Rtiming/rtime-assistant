# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K2 registry management: validate / add-remove provider / set-default / probe.

``__init__`` stays the read-only loader; every verb that CHANGES the registry or
talks to the network lives here. Pure stdlib, same as the loader, so the CLI and
the admin-api wiring can both import it anywhere the loader works.

Edit semantics: verbs take the parsed registry dict, mutate a COPY, and only
return it when the merged result passes :func:`validate_registry` — an edit can
never leave the file invalid. :func:`save_registry` writes atomically (tmp +
rename, 2-space indent to match the hand-maintained file).

Which file gets edited: :func:`rtime_models.registry_path` — the packaged repo
default, or the deployment-local copy when ``RTIME_MODEL_REGISTRY`` (config field
``models.model_registry_path``) points at one. Panel/API edits should target a
deployment-local copy; the repo default changes through git review.

Probe semantics: a provider is "ready" when (a) at least one of its
``secret_env_names`` is set (``*_FILE``/``*KEYFILE`` names must point at an
existing file) and (b) its ``base_url`` answers ANY HTTP status (401/404 still
prove the endpoint is alive). No secret value is ever read beyond "is it set",
and nothing is sent to the provider but a bare GET.
"""

from __future__ import annotations

import copy
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Mapping

from . import CAPABILITY_KEYS

__all__ = [
    "validate_registry",
    "known_model_selectors",
    "add_provider",
    "remove_provider",
    "set_default_model",
    "save_registry",
    "probe_provider",
    "probe_registry",
]


def validate_registry(reg: dict[str, Any]) -> list[str]:
    """Structural sanity check of a parsed registry dict (empty list = OK).

    Same rules the CLI ``validate`` command has always enforced; parameterised on
    the dict so edit verbs can check their merged result before saving.
    """
    errors: list[str] = []
    if reg.get("schema_version") != 1:
        errors.append(f"schema_version != 1 (got {reg.get('schema_version')!r})")
    providers = reg.get("providers", [])
    seen_ids: set[str] = set()
    for p in providers:
        pid = p.get("id")
        if not pid:
            errors.append("provider with empty id")
            continue
        if pid in seen_ids:
            errors.append(f"duplicate provider id {pid!r}")
        seen_ids.add(pid)
        for required in ("label", "protocol"):
            if required not in p:
                errors.append(f"{pid}: missing {required!r}")
        # Catalog OpenAI providers are projected with a base_url the gateway
        # rstrips; a null/empty base_url would crash config(). Guard it here.
        if p.get("base_url_cfg_key") and not (isinstance(p.get("base_url"), str) and p["base_url"]):
            errors.append(f"{pid}: base_url_cfg_key set but base_url is missing/empty")
        for m in p.get("models", []):
            mid = m.get("id")
            if mid is None:
                errors.append(f"{pid}: model with no id")
                continue
            caps = m.get("capabilities") or {}
            missing = [k for k in CAPABILITY_KEYS if k not in caps]
            if missing:
                errors.append(f"{pid}/{mid or '(default)'}: capabilities missing {missing}")
            extra = [k for k in caps if k not in CAPABILITY_KEYS]
            if extra:
                errors.append(f"{pid}/{mid or '(default)'}: unknown capability keys {extra}")
    # Aliases must be globally unambiguous (one target per alias).
    alias_target: dict[str, str] = {}
    for p in providers:
        for m in p.get("models", []):
            for alias in m.get("aliases", []):
                if alias in alias_target and alias_target[alias] != m.get("id", ""):
                    errors.append(
                        f"alias {alias!r} maps to both {alias_target[alias]!r} and {m.get('id')!r}"
                    )
                alias_target[alias] = m.get("id", "")
    # default_model must resolve to something the registry knows (or be empty =
    # route through the wrapper default).
    default = str(reg.get("default_model", ""))
    if default and default not in known_model_selectors(reg):
        errors.append(f"default_model {default!r} resolves to no model id/alias in the registry")
    return errors


def known_model_selectors(reg: dict[str, Any]) -> set[str]:
    """Every string that selects a model: model ids, aliases, catalog/routing/code
    model ids and tier targets. The validity domain for ``default_model``."""
    out: set[str] = set()
    for p in reg.get("providers", []):
        for m in p.get("models", []):
            if m.get("id"):
                out.add(str(m["id"]))
            out.update(str(a) for a in m.get("aliases", []))
        for key in ("catalog_models", "routing_models", "code_models"):
            out.update(str(x) for x in p.get(key, []))
        out.update(str(v) for v in p.get("tiers", {}).values())
    out.discard("")
    return out


def _provider_selectors(p: dict[str, Any]) -> set[str]:
    return known_model_selectors({"providers": [p]})


def add_provider(reg: dict[str, Any], provider: dict[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    """Append ``provider``; return ``(new_reg, [])`` or ``(None, errors)``.

    The input dict is never mutated. Rejects a duplicate id up front and then
    validates the WHOLE merged registry, so a malformed provider (or one whose
    aliases collide with an existing model) never lands in the file.
    """
    pid = provider.get("id")
    if not pid or not isinstance(pid, str):
        return None, ["provider needs a non-empty string id"]
    if any(p.get("id") == pid for p in reg.get("providers", [])):
        return None, [f"provider id {pid!r} already exists (remove it first to replace)"]
    merged = copy.deepcopy(reg)
    merged.setdefault("providers", []).append(copy.deepcopy(provider))
    errors = validate_registry(merged)
    return (merged, []) if not errors else (None, errors)


def remove_provider(reg: dict[str, Any], provider_id: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Remove the provider; refuse when ``default_model`` routes into it."""
    target = next((p for p in reg.get("providers", []) if p.get("id") == provider_id), None)
    if target is None:
        return None, [f"no provider with id {provider_id!r}"]
    default = str(reg.get("default_model", ""))
    if default and default in _provider_selectors(target):
        return None, [
            f"default_model {default!r} routes to provider {provider_id!r};"
            " set-default first, then remove"
        ]
    merged = copy.deepcopy(reg)
    merged["providers"] = [p for p in merged.get("providers", []) if p.get("id") != provider_id]
    errors = validate_registry(merged)
    return (merged, []) if not errors else (None, errors)


def set_default_model(reg: dict[str, Any], model_or_alias: str) -> tuple[dict[str, Any] | None, list[str]]:
    """Set registry-level ``default_model`` ('' = wrapper default). NOTE: the live
    routing default is the CONFIG field ``models.default_model`` (env/store/panel);
    this registry value is only the fallback the loader reports."""
    merged = copy.deepcopy(reg)
    merged["default_model"] = str(model_or_alias)
    errors = validate_registry(merged)
    return (merged, []) if not errors else (None, errors)


def save_registry(reg: dict[str, Any], path: str | Path) -> None:
    """Atomic write (tmp + rename in the same directory), 2-space indent +
    trailing newline to match the hand-maintained file."""
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(reg, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    tmp.replace(target)


# ------------------------------------------------------------------- probe

def probe_provider(
    p: dict[str, Any],
    *,
    env: Mapping[str, str] | None = None,
    timeout: float = 3.0,
    check_url: bool = True,
) -> dict[str, Any]:
    """One provider's readiness: secret present? endpoint alive?

    ``reachable`` is None when there is nothing to check (no base_url, or
    ``check_url=False``); any HTTP status counts as reachable — the point is
    "does the endpoint exist", not "is the key valid" (that would spend quota
    and send a secret, which a probe must never do).
    """
    environ: Mapping[str, str] = os.environ if env is None else env
    secret_names = [str(n) for n in p.get("secret_env_names", [])]
    found: list[str] = []
    keyfile_missing: list[str] = []
    for name in secret_names:
        value = (environ.get(name) or "").strip()
        if not value:
            continue
        if name.endswith(("_FILE", "KEYFILE")):
            if Path(value).expanduser().is_file():
                found.append(name)
            else:
                keyfile_missing.append(name)
        else:
            found.append(name)
    result: dict[str, Any] = {
        "id": p.get("id", ""),
        "label": p.get("label", ""),
        "secret_required": bool(secret_names),
        "secret_present": (bool(found) if secret_names else None),
        "secret_env_found": found,
        "keyfile_missing": keyfile_missing,
        "base_url": p.get("base_url"),
        "reachable": None,
        "http_status": None,
        "error": None,
    }
    url = p.get("base_url")
    if check_url and isinstance(url, str) and url:
        request = urllib.request.Request(url, headers={"User-Agent": "rtime-models-probe"})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as resp:  # noqa: S310 — URL来自registry数据文件,非用户输入
                result["reachable"] = True
                result["http_status"] = int(resp.status)
        except urllib.error.HTTPError as exc:
            result["reachable"] = True
            result["http_status"] = int(exc.code)
        except Exception as exc:  # URLError/socket timeout/ssl — endpoint dead
            result["reachable"] = False
            result["error"] = f"{type(exc).__name__}: {exc}"
    return result


def probe_registry(
    reg: dict[str, Any],
    *,
    provider_id: str | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 3.0,
    check_url: bool = True,
) -> list[dict[str, Any]]:
    """Probe every provider (or just ``provider_id``); unknown id returns []."""
    providers = reg.get("providers", [])
    if provider_id is not None:
        providers = [p for p in providers if p.get("id") == provider_id]
    return [
        probe_provider(p, env=env, timeout=timeout, check_url=check_url)
        for p in providers
    ]
