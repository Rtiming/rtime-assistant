# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Validate a proposed config state against the registry's pydantic models.

The ConfigStore holds config as a nested ``{module: {field: value}}`` dict. To
check it (whole or partial) we hand each module's slice to its pydantic-settings
model and collect the failures as structured :class:`FieldError` records, prefixed
back to dotted ``module.field`` paths.

Env independence (the real behaviour, not the old aspiration)
-------------------------------------------------------------
A pydantic-settings ``BaseSettings`` reads the *process env* even under
``model_validate`` — omitted fields are still resolved from env/dotenv/secret
sources, so a bad or unrelated env var (e.g. ``RTIME_LIBRARY_GATEWAY_HTTP_PORT=
notanint``) would leak into the validation of a slice that never mentions that
field. That silently made validation impure and coupled every module's fate to
the process env (defect #12/#10).

We fix this by validating against an *env-independent mirror* of each settings
model: a subclass whose ``settings_customise_sources`` exposes ONLY the explicitly
provided init values (no env, no dotenv, no secret-dir). Constraints, field
validators, model validators, and aliases are all inherited unchanged, so
validation is identical to the real model MINUS the env scan. Values must be
passed by instantiating (``Pure(**values)``) rather than ``model_validate`` —
``model_validate`` bypasses the settings-source pipeline entirely.

Secret redaction in errors
--------------------------
Rejected secret values must never be echoed back in a :class:`FieldError.input`.
Two subtleties handled here (defects #6/#7):
  - a model-level ``@model_validator`` failure has an empty ``loc`` and pydantic
    echoes the WHOLE input dict (which may contain plaintext secrets) — we redact
    it wholesale;
  - a value addressed by its env-alias reports ``loc[0]`` as the alias, not the
    Python field name — we resolve aliases back to field names so alias-keyed
    secret errors are still masked.
"""

from __future__ import annotations

from typing import Any

from pydantic import AliasChoices
from pydantic import ValidationError as PydanticValidationError
from pydantic_settings import BaseSettings

from .errors import FieldError
from .registry import Registry

# Cache of env-independent mirror subclasses, keyed by the source model class.
_MIRROR_CACHE: dict[type[BaseSettings], type[BaseSettings]] = {}


def _env_independent_model(model: type[BaseSettings]) -> type[BaseSettings]:
    """A subclass of ``model`` that validates ONLY explicitly provided values.

    Overriding ``settings_customise_sources`` to return just ``init_settings``
    removes the env / dotenv / secret-file sources, so validation is a pure
    function of the input. Everything else (types, constraints, field/model
    validators, aliases, ``model_config``) is inherited unchanged.
    """
    cached = _MIRROR_CACHE.get(model)
    if cached is not None:
        return cached

    class _Pure(model):  # type: ignore[valid-type, misc]
        @classmethod
        def settings_customise_sources(
            cls,
            settings_cls: type[BaseSettings],
            init_settings: Any,
            env_settings: Any,
            dotenv_settings: Any,
            file_secret_settings: Any,
        ) -> tuple[Any, ...]:
            # ONLY the values handed to __init__; no process env / dotenv / secrets.
            return (init_settings,)

    _Pure.__name__ = f"{model.__name__}Pure"
    _Pure.__qualname__ = _Pure.__name__
    _MIRROR_CACHE[model] = _Pure
    return _Pure


def _aliases_for(field: Any) -> list[str]:
    """The string alias names declared for a pydantic field (validation aliases)."""
    va = getattr(field, "validation_alias", None)
    if isinstance(va, str):
        return [va]
    if isinstance(va, AliasChoices):
        return [c for c in va.choices if isinstance(c, str)]
    return []


def _secret_maps(model: type[BaseSettings]) -> tuple[set[str], dict[str, str]]:
    """``(secret_field_names, alias_or_name -> field_name)`` for one model.

    ``secret_field_names`` are the Python field names marked ``x-secret``.
    The second map lets us resolve a ``loc[0]`` that is an env-alias back to the
    Python field name (so alias-keyed secret errors are still masked, defect #7).
    """
    schema = model.model_json_schema(by_alias=False).get("properties", {})
    secret_fields = {name for name, prop in schema.items() if prop.get("x-secret")}
    resolve: dict[str, str] = {}
    for name, field in model.model_fields.items():
        resolve[name] = name
        for alias in _aliases_for(field):
            resolve[alias] = name
    return secret_fields, resolve


def _field_errors_from_pydantic(
    module: str, model: type[BaseSettings], exc: PydanticValidationError
) -> list[FieldError]:
    secret_fields, resolve = _secret_maps(model)
    errors: list[FieldError] = []
    for err in exc.errors():
        loc = err.get("loc", ())
        raw_key = str(loc[0]) if loc else ""
        # loc[0] may be an env-alias; resolve it back to the Python field name.
        field = resolve.get(raw_key, raw_key)
        path = f"{module}.{field}" if field else module
        raw_input = err.get("input")
        errors.append(
            FieldError(
                path=path,
                message=err.get("msg", "invalid"),
                type=err.get("type"),
                input=_safe_input(raw_input, field, secret_fields),
            )
        )
    return errors


def _safe_input(raw_input: Any, field: str, secret_fields: set[str]) -> Any:
    """Never let a secret (or a collection that might carry one) escape.

    Redact when:
      - the error is a secret field (by resolved name), OR
      - the error is model-level (no field) — pydantic echoes the whole input
        dict, which may contain a plaintext secret, OR
      - the input is itself a dict / list / tuple / set that could hold a secret.
    """
    if not field or field in secret_fields:
        return "***"
    if isinstance(raw_input, (dict, list, tuple, set)):
        return "***"
    return raw_input


def validate_module(
    registry: Registry, module: str, values: dict[str, Any]
) -> list[FieldError]:
    """Validate one module's ``{field: value}`` slice; return failures (possibly []).

    Missing required fields ARE reported (this is full validation of the slice).
    Use it on the merged full state; for partial edits, merge onto current first.

    Validation is env-independent: an unrelated/bad process env var can never
    affect the result (see the module docstring).
    """
    model = registry.model(module)
    pure = _env_independent_model(model)
    try:
        # Instantiate the env-independent mirror so ONLY ``values`` are validated;
        # (model_validate would bypass the settings-source pipeline and re-scan env).
        pure(**values)
    except PydanticValidationError as exc:
        return _field_errors_from_pydantic(module, model, exc)
    return []


def validate_state(
    registry: Registry, state: dict[str, dict[str, Any]]
) -> list[FieldError]:
    """Validate a full nested ``{module: {field: value}}`` state.

    Only modules present in ``state`` are validated; an unregistered module in
    ``state`` is itself an error.
    """
    errors: list[FieldError] = []
    for module, values in state.items():
        if not registry.has(module):
            errors.append(
                FieldError(path=module, message=f"unknown config module: {module!r}")
            )
            continue
        errors.extend(validate_module(registry, module, values))
    return errors
