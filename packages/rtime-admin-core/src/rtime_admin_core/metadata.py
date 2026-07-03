# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Field-metadata lookups over a registry's schemas.

The ConfigStore addresses config by dotted path ``<module>.<field>`` (Caddy
style). To redact, classify reload semantics, or validate a path, the store needs
per-field metadata pulled from the registered JSON Schemas. This module is the one
place that reads ``x-secret`` / ``x-reload`` / ``x-scope`` out of a schema, so the
vocabulary is interpreted consistently everywhere.

Paths here are two-segment: ``module.field``. The sample schemas are flat (no
nested pydantic models), which matches the audit's finding that the real config is
overwhelmingly flat env vars. Nested support can extend ``split_path`` /
``field_prop`` later without changing callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .registry import Registry

REDACTED_PLACEHOLDER = "***"


@dataclass(frozen=True)
class FieldMeta:
    """Resolved metadata for one config path."""

    module: str
    field: str
    path: str
    secret: bool
    reload: str  # "hot" | "restart"
    scope: str | None


def split_path(path: str) -> tuple[str, str]:
    """Split ``module.field`` into its two segments.

    Raises ``ValueError`` on a malformed path (empty, no dot, empty segment).
    """
    if not isinstance(path, str) or not path:
        raise ValueError("path must be a non-empty string")
    module, sep, field = path.partition(".")
    if not sep or not module or not field:
        raise ValueError(f"path must be '<module>.<field>', got {path!r}")
    if "." in field:
        raise ValueError(f"nested paths are not supported yet: {path!r}")
    return module, field


def field_prop(registry: Registry, path: str) -> dict[str, Any]:
    """The JSON-Schema property dict for ``path`` (raises ``KeyError`` if absent)."""
    module, field = split_path(path)
    schema = registry.get_schema(module)  # raises KeyError for unknown module
    props = schema.get("properties", {})
    if field not in props:
        raise KeyError(f"unknown field: {path!r}")
    return props[field]


def field_meta(registry: Registry, path: str) -> FieldMeta:
    """Resolve the rtime metadata for ``path``."""
    module, field = split_path(path)
    prop = field_prop(registry, path)
    return FieldMeta(
        module=module,
        field=field,
        path=path,
        secret=bool(prop.get("x-secret", False)),
        reload=str(prop.get("x-reload", "restart")),
        scope=prop.get("x-scope"),
    )


def is_secret(registry: Registry, path: str) -> bool:
    return field_meta(registry, path).secret


def secret_paths(registry: Registry) -> set[str]:
    """Every ``module.field`` marked ``x-secret`` across all registered modules."""
    out: set[str] = set()
    for module in registry.list_modules():
        props = registry.get_schema(module).get("properties", {})
        for field, prop in props.items():
            if prop.get("x-secret"):
                out.add(f"{module}.{field}")
    return out


def all_paths(registry: Registry) -> list[str]:
    """Every addressable ``module.field`` path, sorted."""
    out: list[str] = []
    for module in registry.list_modules():
        props = registry.get_schema(module).get("properties", {})
        out.extend(f"{module}.{field}" for field in props)
    return sorted(out)
