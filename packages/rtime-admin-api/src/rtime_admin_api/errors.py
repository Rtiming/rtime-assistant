# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The one HTTP-error shape this API speaks.

Every non-2xx response is ``{"error": {"code", "message"[, "errors"]}}`` so an
ops agent can branch on a stable machine-readable ``code`` instead of parsing
prose. Raised anywhere in the request path and translated by the exception
handler installed in :func:`rtime_admin_api.app.create_app`.
"""

from __future__ import annotations

from typing import Any


class ApiError(Exception):
    """A structured HTTP error (status + stable code + human message).

    ``errors`` optionally carries a list of field-level dicts (e.g.
    ``FieldError.to_dict()`` from the core) for 422 validation failures.
    ``www_authenticate`` adds the ``WWW-Authenticate: Bearer`` header (401s).
    """

    def __init__(
        self,
        status: int,
        code: str,
        message: str,
        *,
        errors: list[dict[str, Any]] | None = None,
        www_authenticate: bool = False,
    ) -> None:
        self.status = status
        self.code = code
        self.message = message
        self.errors = errors
        self.www_authenticate = www_authenticate
        super().__init__(f"{status} {code}: {message}")

    def body(self) -> dict[str, Any]:
        err: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.errors is not None:
            err["errors"] = self.errors
        return {"error": err}
