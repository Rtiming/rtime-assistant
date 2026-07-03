# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Request body models for the admin API (pydantic, ``extra=forbid``).

Kept in their own module so the request-validation error handler and the
endpoints share one definition; ``extra=forbid`` means a smuggled unexpected
field is a clean 422 (whose handler never echoes the offending value).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict


class ChangesBody(BaseModel):
    """``{"changes": {"module.field": value, ...}}``."""

    model_config = ConfigDict(extra="forbid")
    changes: dict[str, Any]


class PatchBody(ChangesBody):
    note: str | None = None


class RollbackBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    snapshot_id: str
