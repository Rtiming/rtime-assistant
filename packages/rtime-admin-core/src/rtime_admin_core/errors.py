# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Admin-core exceptions + the structured validation-error record.

Upper layers (L2 HTTP, L2' MCP) translate these to their own transport shapes
(HTTP 4xx, MCP error content). Keeping them here means the *categories* of failure
are defined once in the pure core, so every layer reports the same set.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


class AdminCoreError(Exception):
    """Base for all admin-core errors."""


class UnknownPathError(AdminCoreError, KeyError):
    """A dotted path names a module or field that is not registered."""


class ValidationError(AdminCoreError):
    """A proposed config change failed schema validation.

    Carries a list of :class:`FieldError` so a caller (and eventually a form) can
    show per-field messages instead of one opaque string.
    """

    def __init__(self, errors: list["FieldError"]):
        self.errors = errors
        super().__init__(
            "config validation failed: "
            + "; ".join(f"{e.path}: {e.message}" for e in errors)
        )


class SnapshotNotFoundError(AdminCoreError, KeyError):
    """``rollback`` / lookup referenced a snapshot id that does not exist."""


@dataclass(frozen=True)
class FieldError:
    """One field-level validation failure (JSON-serialisable)."""

    path: str  # "module.field", or "module" for whole-model errors
    message: str
    type: str | None = None  # pydantic error type, when available
    input: Any = None  # the rejected input (redacted upstream if secret)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
