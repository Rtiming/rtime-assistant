# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Field helpers that stamp rtime's config metadata vocabulary onto ``Field``.

The metadata lands in the field's ``json_schema_extra`` so it survives into
``model_json_schema()`` and is visible to every downstream consumer (docs, the
future admin API, the panel). Keys are ``x-`` prefixed by JSON Schema convention
for vendor extensions.

    x-secret       : bool        this value is a credential -> never render in
                                 plaintext docs/panels; loaded from a secret
                                 file, not the config YAML.
    x-reload       : str         "hot"     = applyable without a restart, or
                                 "restart" = the process must restart to pick up.
    x-scope        : str         optional admin-API write scope guarding this
                                 field (e.g. "write:qq"); omitted = module scope.
    x-env-aliases  : list[str]   the env var name(s) that populate this field, in
                                 resolution order (new preferred name first,
                                 legacy names after). See the compatibility note.

Legacy env-name compatibility (P2 stage ①): pass ``env_aliases=[...]``. The
helper wires those into the field's ``validation_alias`` (an ``AliasChoices``, so
ALL of them keep loading the value) AND records them under ``x-env-aliases`` for
docs. This is the ONE place old names are declared, so "old names never break" is
mechanically enforced by the golden schema test. When a field's env name is just
``<PREFIX><FIELD>`` you can omit ``env_aliases`` — pydantic-settings derives it.

Rendering note: emit schemas with ``model_json_schema(by_alias=False)`` so the
property KEYS stay the stable Python field names (an AliasChoices would otherwise
rename the key to its first choice and drop the field name). The alias list lives
in ``x-env-aliases``, not in the key.
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import AliasChoices, Field


class Reload(str, Enum):
    """Whether a field can be applied hot or needs a process restart."""

    HOT = "hot"
    RESTART = "restart"


def _merge_extra(
    json_schema_extra: dict[str, Any] | None,
    *,
    secret: bool,
    reload: Reload | str,
    scope: str | None,
    env_aliases: list[str] | None,
) -> dict[str, Any]:
    extra: dict[str, Any] = dict(json_schema_extra or {})
    if secret:
        extra["x-secret"] = True
    extra["x-reload"] = reload.value if isinstance(reload, Reload) else str(reload)
    if scope is not None:
        extra["x-scope"] = scope
    if env_aliases:
        extra["x-env-aliases"] = list(env_aliases)
    return extra


def _build(
    default: Any,
    *,
    secret: bool,
    description: str,
    reload: Reload | str,
    scope: str | None,
    env_aliases: list[str] | None,
    json_schema_extra: dict[str, Any] | None,
    kwargs: dict[str, Any],
) -> Any:
    if env_aliases:
        if "validation_alias" in kwargs:
            raise ValueError("pass env_aliases OR validation_alias, not both")
        # AliasChoices: every listed name populates the field; order is priority.
        kwargs["validation_alias"] = AliasChoices(*env_aliases)
    extra = _merge_extra(
        json_schema_extra,
        secret=secret,
        reload=reload,
        scope=scope,
        env_aliases=env_aliases,
    )
    # pydantic forbids passing both a positional default and default_factory; when
    # a factory is given, drop the (sentinel) positional default entirely.
    if "default_factory" in kwargs:
        # A default_factory value is NOT emitted as `default` in the JSON schema,
        # so docs/panels would mislabel the field "required". Materialise the
        # factory's value once at definition time and stash it under x-default so
        # the schema still carries the effective default (these factories — e.g.
        # frozenset — are pure/argument-free).
        try:
            extra.setdefault("x-default", sorted(kwargs["default_factory"]()))
        except Exception:  # pragma: no cover - non-iterable factory
            pass
        return Field(description=description, json_schema_extra=extra, **kwargs)
    return Field(
        default,
        description=description,
        json_schema_extra=extra,
        **kwargs,
    )


def config_field(
    default: Any = ...,
    *,
    description: str,
    reload: Reload | str = Reload.RESTART,
    scope: str | None = None,
    env_aliases: list[str] | None = None,
    json_schema_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """A non-secret config field carrying rtime metadata.

    ``reload`` defaults to RESTART because the audit found ~220 config points are
    all read once at startup (docs/audit/codebase-audit-2026-07.zh-CN.md §二).
    Mark the genuinely hot ones (keyfiles, campus URL table) explicitly.
    """
    return _build(
        default,
        secret=False,
        description=description,
        reload=reload,
        scope=scope,
        env_aliases=env_aliases,
        json_schema_extra=json_schema_extra,
        kwargs=kwargs,
    )


def secret_field(
    default: Any = ...,
    *,
    description: str,
    reload: Reload | str = Reload.RESTART,
    scope: str | None = None,
    env_aliases: list[str] | None = None,
    json_schema_extra: dict[str, Any] | None = None,
    **kwargs: Any,
) -> Any:
    """A credential field (marked ``x-secret``); docs/panels never show its value."""
    return _build(
        default,
        secret=True,
        description=description,
        reload=reload,
        scope=scope,
        env_aliases=env_aliases,
        json_schema_extra=json_schema_extra,
        kwargs=kwargs,
    )
