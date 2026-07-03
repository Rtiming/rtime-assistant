# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Duck-typed registry helpers for the profile loader.

The loader validates the compiled layer against a *registry* of pydantic models
but must not hard-depend on rtime-admin-core (that would create a dependency
cycle: admin-core already depends on rtime-config). So these helpers speak only
the small surface a registry exposes — ``has(module)`` and ``get_schema(module)``
returning a JSON Schema dict — which rtime-admin-core's ``Registry`` satisfies.

Kept private (``_meta``) because it is an implementation detail of the loader,
not part of rtime-config's public API.
"""

from __future__ import annotations

from typing import Any


def split_module_field(path: str) -> tuple[str, str]:
    """Split ``module.field`` (exactly two segments) — mirrors admin-core.split_path."""
    module, sep, fld = path.partition(".")
    if not sep or not module or not fld or "." in fld:
        raise ValueError(f"path must be '<module>.<field>', got {path!r}")
    return module, fld


class SecretClassificationError(Exception):
    """The registry cannot classify a path as secret-or-not (module unregistered).

    The x-secret door MUST fail closed: if the module carrying a compiled key is
    not in the registry, we cannot know whether the field is a secret, so we refuse
    rather than assume "not secret" (which would fail OPEN — defect #2).
    """


def _prop(registry: Any, path: str) -> dict[str, Any] | None:
    module, fld = split_module_field(path)
    if not registry.has(module):
        raise SecretClassificationError(
            f"cannot classify {path!r}: module {module!r} not in registry — the "
            "x-secret door cannot run, refusing (fail closed). Register the module "
            "(e.g. default_registry(include_qq=True)) before loading this profile."
        )
    props = registry.get_schema(module).get("properties", {})
    return props.get(fld)


def is_secret_path(registry: Any, path: str) -> bool:
    """True if ``path``'s registry field carries ``x-secret``.

    Raises :class:`SecretClassificationError` if the module is not registered (the
    field cannot be classified) — the secret door fails CLOSED, never open. A
    field-name that is unknown WITHIN a registered module returns False: that is a
    typo which surfaces as a validation error, not a hidden secret leak.
    """
    prop = _prop(registry, path)
    return bool(prop and prop.get("x-secret"))


def module_defaults(registry: Any, module: str) -> dict[str, Any]:
    """A module's ``{field: default}`` map from its JSON Schema (default/x-default).

    Used to build a complete, validatable module slice from a partial compiled
    projection (so the merged slice validates as a whole model).
    """
    schema = registry.get_schema(module)
    out: dict[str, Any] = {}
    for fld, prop in schema.get("properties", {}).items():
        if "default" in prop:
            out[fld] = prop["default"]
        elif "x-default" in prop:
            out[fld] = prop["x-default"]
        else:
            out[fld] = None
    return out
