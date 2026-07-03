# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Profile loader — parse, merge (single-level extends), resolve refs, compile.

The pipeline (design §2.1-2.5):

    load(profile_dir)
      1. parse the profile.yaml (+ its single ``extends`` parent) as raw dicts;
      2. MERGE parent <- child: whole-value replacement per top-level field, with
         dict DEEP-merge ONLY for ``model.params`` (OpenClaw semantics, §2.2);
         a parent that itself declares ``extends`` is a hard error (single level);
      3. validate the merged dict into a :class:`ProfileConfig`;
      4. resolve file references (system_prompt_file -> content;
         direct_rules_file -> validated existing path);
      5. PROJECT to a flat ``{module.field: value}`` dict via the explicit mapping
         table (sparse: unset profile keys contribute nothing);
      6. reject any compiled key whose registry field metadata is ``x-secret``
         (load FAILURE, not a warning — §2.5 door #1);
      7. (optional) validate the compiled layer against a registry, env-independent.

The result is a :class:`CompiledProfile` carrying the flat layer, the resolved
``ProfileConfig``, and provenance (profile id, source file, parent id).

This module depends on ``pyyaml`` (added to rtime-config). Registry validation
(step 7) is optional and injected: the loader takes a ``registry`` and a
``validate_state``-shaped callable so rtime-config does NOT hard-depend on
rtime-admin-core (avoids a dependency cycle); the caller wires admin-core in.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .mapping import MCP_SERVERS_TARGET, PROJECTIONS
from .schema import SUPPORTED_SCHEMA_VERSION, ProfileConfig


class ProfileError(Exception):
    """A profile failed to load (parse / extends / ref / secret / validation)."""


class ProfileSecretError(ProfileError):
    """A compiled profile key maps to an x-secret field (§2.5 door #1)."""


@dataclass(frozen=True)
class CompiledProfile:
    """The output of :func:`load_profile`.

    ``layer`` is the flat ``{module.field: value}`` map to inject as the store's
    profile layer. ``config`` is the resolved :class:`ProfileConfig`. The rest is
    provenance for audit/panel.
    """

    layer: dict[str, Any]
    config: ProfileConfig
    profile_id: str
    source: str  # path of the profile.yaml
    parent_id: str | None = None
    files: dict[str, str] = field(default_factory=dict)  # resolved file refs


# --- parsing --------------------------------------------------------------------


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise ProfileError(f"profile file not found: {path}")
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except yaml.YAMLError as exc:
        raise ProfileError(f"invalid YAML in {path}: {exc}") from exc
    if data is None:
        data = {}
    if not isinstance(data, dict):
        raise ProfileError(f"profile must be a mapping at top level: {path}")
    return data


def _resolve_extends_path(profiles_root: Path, ref: str) -> Path:
    """Map an ``extends`` ref like ``_base/qq`` to ``<root>/_base/qq.yaml``.

    ``ref`` may already end in ``.yaml``. It must stay within ``profiles_root``
    (no ``..`` traversal out of the profiles tree).
    """
    rel = ref if ref.endswith((".yaml", ".yml")) else f"{ref}.yaml"
    candidate = (profiles_root / rel).resolve()
    root = profiles_root.resolve()
    if root not in candidate.parents and candidate != root:
        raise ProfileError(f"extends escapes profiles root: {ref!r}")
    return candidate


# --- merge (single-level extends) -----------------------------------------------

# Only these dict-typed profile params deep-merge across extends; everything else
# is whole-value replacement (design §2.2). Expressed as dotted section.key.
_DEEP_MERGE_KEYS = frozenset({"model.params"})


def _merge(parent: dict[str, Any], child: dict[str, Any]) -> dict[str, Any]:
    """Merge ``child`` over ``parent`` with whole-value replacement per top-level
    field, EXCEPT the allow-listed dict params which deep-merge one level.

    Lists are NOT deep-merged (whole replacement) — predictable + testable.
    """
    out: dict[str, Any] = dict(parent)
    for key, cval in child.items():
        if (
            isinstance(cval, dict)
            and isinstance(out.get(key), dict)
            and key in {"model"}
        ):
            # section-level dict: replace scalars wholesale, deep-merge only the
            # allow-listed nested dict params (model.params).
            merged_section = dict(out[key])
            for sk, sv in cval.items():
                dotted = f"{key}.{sk}"
                if (
                    dotted in _DEEP_MERGE_KEYS
                    and isinstance(sv, dict)
                    and isinstance(merged_section.get(sk), dict)
                ):
                    merged = dict(merged_section[sk])
                    merged.update(sv)
                    merged_section[sk] = merged
                else:
                    merged_section[sk] = sv
            out[key] = merged_section
        else:
            out[key] = cval
    return out


# --- projection -----------------------------------------------------------------


def _get_nested(obj: Any, dotted: str) -> Any:
    """Attribute-walk ``dotted`` into a pydantic model; None if any hop is None."""
    cur = obj
    for part in dotted.split("."):
        if cur is None:
            return None
        cur = getattr(cur, part, None)
    return cur


# Credential-looking keys that must never be INLINED in a git profile's
# mcp_servers (same spirit as the x-secret door #1: profiles carry references, not
# secret values). Matched case-insensitively as a substring of any key at any
# depth of a server spec; a header named Authorization/auth is caught too.
_CREDENTIAL_KEY_MARKERS = (
    "token",
    "secret",
    "password",
    "passwd",
    "apikey",
    "api_key",
    "api-key",
    "authorization",
    "auth",
    "credential",
    "bearer",
    "access_key",
    "private_key",
)


def _scan_for_inline_credentials(name: str, spec: Any, _path: str = "") -> list[str]:
    """Return credential-looking key paths found inside one mcp server spec.

    Recurses dicts/lists so a nested ``headers: {Authorization: "Bearer …"}`` or
    ``env: {API_KEY: …}`` is caught, not just top-level keys.
    """
    hits: list[str] = []
    if isinstance(spec, dict):
        for k, v in spec.items():
            key_l = str(k).lower()
            here = f"{_path}.{k}" if _path else str(k)
            if any(marker in key_l for marker in _CREDENTIAL_KEY_MARKERS):
                hits.append(f"{name}:{here}")
            hits.extend(_scan_for_inline_credentials(name, v, here))
    elif isinstance(spec, list):
        for i, item in enumerate(spec):
            hits.extend(_scan_for_inline_credentials(name, item, f"{_path}[{i}]"))
    return hits


def _mcp_config_json(mcp_servers: dict[str, Any] | None) -> str:
    """Serialize ``plugins.mcp_servers`` to the qq.mcp_config JSON string.

    Drops entries with ``enabled=false``; an all-empty result becomes the QQ
    "no MCP" sentinel ``{"mcpServers": {}}`` (§2.3).

    Rejects (raises :class:`ProfileSecretError`) any ENABLED server whose spec
    inlines a credential-looking key (token/key/secret/password/authorization/…,
    including nested headers/env) — a git profile must carry references, not secret
    values, same door as x-secret (defect #3). Disabled servers are dropped before
    scanning (they contribute nothing to the compiled config).
    """
    servers: dict[str, Any] = {}
    offenders: list[str] = []
    for name, spec in (mcp_servers or {}).items():
        if isinstance(spec, dict) and spec.get("enabled") is False:
            continue
        cleaned = {k: v for k, v in dict(spec or {}).items() if k != "enabled"}
        offenders.extend(_scan_for_inline_credentials(name, cleaned))
        servers[name] = cleaned
    if offenders:
        raise ProfileSecretError(
            "profile mcp_servers inlines credential-looking value(s) (use a "
            "reference / env, never a secret in git): " + ", ".join(sorted(offenders))
        )
    return json.dumps({"mcpServers": servers}, ensure_ascii=False, sort_keys=True)


def _project(
    config: ProfileConfig, *, profile_dir: Path, files: dict[str, str]
) -> dict[str, Any]:
    """Compile a resolved ProfileConfig to the flat ``{module.field: value}`` layer.

    Sparse: an unset (None) profile value contributes nothing. File refs are
    resolved here (content or validated path), recorded into ``files``.
    """
    layer: dict[str, Any] = {}
    for proj in PROJECTIONS:
        raw = _get_nested(config, proj.profile_path)
        if raw is None:
            continue  # sparse: unset -> falls through to schema default

        if proj.needs_file_content:
            content = _read_ref_file(profile_dir, raw, want_content=True)
            files[proj.profile_path] = str((profile_dir / raw))
            layer[proj.target] = content
            continue
        if proj.needs_file_path:
            resolved = _read_ref_file(profile_dir, raw, want_content=False)
            files[proj.profile_path] = resolved
            layer[proj.target] = resolved
            continue
        if proj.target == MCP_SERVERS_TARGET:
            layer[proj.target] = _mcp_config_json(raw)
            continue

        layer[proj.target] = proj.transform(raw) if proj.transform else raw
    return layer


def _read_ref_file(profile_dir: Path, ref: str, *, want_content: bool) -> str:
    """Resolve a file reference relative to the profile dir.

    ``want_content=True`` returns the file text; else returns the resolved path as
    a string. Missing file is a hard error either way (§2.3: direct_rules_file path
    must exist). No traversal outside the profile dir.
    """
    p = (profile_dir / ref).resolve()
    if profile_dir.resolve() not in p.parents:
        raise ProfileError(f"file ref escapes profile dir: {ref!r}")
    if not p.is_file():
        raise ProfileError(f"referenced file not found: {p}")
    return p.read_text(encoding="utf-8") if want_content else str(p)


# --- secret rejection + validation ----------------------------------------------


def _reject_secrets(layer: dict[str, Any], registry: Any) -> None:
    """Fail if any compiled key maps to an x-secret field (§2.5 door #1).

    Fails CLOSED: if a key's module is unregistered so it cannot be classified,
    ``is_secret_path`` raises and we translate it to a ``ProfileSecretError`` (a
    load failure), never silently treating the unclassifiable key as non-secret
    (defect #2).
    """
    from rtime_config.profile._meta import (  # local import: optional dep
        SecretClassificationError,
        is_secret_path,
    )

    offenders: list[str] = []
    for path in layer:
        try:
            if is_secret_path(registry, path):
                offenders.append(path)
        except SecretClassificationError as exc:
            raise ProfileSecretError(str(exc)) from exc
    if offenders:
        raise ProfileSecretError(
            "profile compiled to secret field(s) (secrets never belong in a "
            f"profile): {', '.join(sorted(offenders))}"
        )


# --- public API -----------------------------------------------------------------


def load_profile(
    profile_dir: str | Path,
    *,
    registry: Any,
    profiles_root: str | Path | None = None,
    validate: Callable[[Any, dict[str, dict[str, Any]]], list] | None = None,
) -> CompiledProfile:
    """Load, merge, resolve, and compile the profile in ``profile_dir``.

    ``profile_dir`` holds a ``profile.yaml`` (+ referenced prompt/rule files).
    ``profiles_root`` is the top of the profiles tree used to resolve ``extends``
    (defaults to ``profile_dir.parent``, i.e. sibling of ``_base``).

    ``registry`` is REQUIRED (a module registry exposing ``has``/``get_schema``,
    e.g. ``rtime_admin_core.default_registry(include_qq=True)``). It is what the
    x-secret compile-time door (§2.5 door #1) needs to classify every compiled key,
    so making it optional would let the whole secret gate be skipped — a fail-OPEN
    default (defect #1). ``None`` raises ``ProfileError`` rather than silently
    returning a layer that was never secret-checked. Wiring the registry in from the
    caller keeps rtime-config free of a hard admin-core dependency.

    When ``validate`` is also given (pass ``rtime_admin_core.validate_state``) the
    compiled layer is additionally validated against the registry models
    (env-independent). The x-secret door runs regardless.
    """
    if registry is None:
        raise ProfileError(
            "load_profile requires a registry: the x-secret compile-time door "
            "cannot run without one, and skipping it would fail open (defect #1). "
            "Pass rtime_admin_core.default_registry(include_qq=True)."
        )
    profile_dir = Path(profile_dir)
    root = Path(profiles_root) if profiles_root is not None else profile_dir.parent

    child_raw = _read_yaml(profile_dir / "profile.yaml")
    parent_id: str | None = None

    extends = (child_raw.get("profile") or {}).get("extends")
    if extends:
        parent_path = _resolve_extends_path(root, extends)
        parent_raw = _read_yaml(parent_path)
        # single-level only: the parent must NOT itself extend.
        parent_extends = (parent_raw.get("profile") or {}).get("extends")
        if parent_extends:
            raise ProfileError(
                f"extends is single-level only: parent {extends!r} itself extends "
                f"{parent_extends!r} (design §2.2)"
            )
        parent_id = (parent_raw.get("profile") or {}).get("id")
        merged = _merge(parent_raw, child_raw)
        # the merged doc's profile block should carry the CHILD's id + extends.
        merged.setdefault("profile", {})
        merged["profile"] = dict(child_raw.get("profile") or {})
    else:
        merged = child_raw

    try:
        config = ProfileConfig.model_validate(merged)
    except Exception as exc:  # pydantic ValidationError -> ProfileError
        raise ProfileError(f"invalid profile {profile_dir}: {exc}") from exc

    if config.schema_version != SUPPORTED_SCHEMA_VERSION:
        raise ProfileError(
            f"unsupported profile schema_version {config.schema_version} "
            f"(supported: {SUPPORTED_SCHEMA_VERSION})"
        )

    files: dict[str, str] = {}
    layer = _project(config, profile_dir=profile_dir, files=files)

    # x-secret door ALWAYS runs (registry is required) — never fail open.
    _reject_secrets(layer, registry)
    if validate is not None:
        _validate_layer(layer, registry, validate)

    return CompiledProfile(
        layer=layer,
        config=config,
        profile_id=config.profile.id,
        source=str((profile_dir / "profile.yaml").resolve()),
        parent_id=parent_id,
        files=files,
    )


def _validate_layer(
    layer: dict[str, Any],
    registry: Any,
    validate: Callable[[Any, dict[str, dict[str, Any]]], list],
) -> None:
    """Validate the compiled layer against the registry models (env-independent).

    Groups the flat layer by module and hands the touched modules to the injected
    ``validate_state``. Merges each module's compiled values onto that module's
    schema defaults so a partial projection still validates as a complete slice.
    """
    from rtime_config.profile._meta import module_defaults, split_module_field

    by_module: dict[str, dict[str, Any]] = {}
    for path, value in layer.items():
        module, fld = split_module_field(path)
        by_module.setdefault(module, {})[fld] = value

    state: dict[str, dict[str, Any]] = {}
    for module, values in by_module.items():
        base = module_defaults(registry, module)
        base.update(values)
        state[module] = base

    errors = validate(registry, state)
    if errors:
        detail = "; ".join(
            f"{getattr(e, 'path', '?')}: {getattr(e, 'message', e)}" for e in errors
        )
        raise ProfileError(f"compiled profile failed validation: {detail}")
