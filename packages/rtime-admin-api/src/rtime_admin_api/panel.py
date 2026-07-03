# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Static operator panel served BY the admin API (design §6.3, T7).

What this is
------------
A no-build single-page panel (``static/index.html`` + ``panel.js`` +
``panel.schema.js``) that a human operator opens in a browser to drive the same
``/v1`` API an ops agent uses. Native ``fetch`` + one pinned-SRI CDN dep
(DOMPurify), matching ``apps/web-chat``'s discipline — no npm, no build step.

Auth stance (deliberate, documented)
------------------------------------
The static SHELL (``GET /``, ``GET /panel``, and the ``/panel/*`` assets) is
served WITHOUT a bearer token: a browser must load the HTML/JS before the
operator can paste a token, so gating the shell would be a chicken-and-egg
lock-out. Everything that reveals or mutates config — every ``/v1/*`` endpoint —
stays behind the app-level ``_auth`` dependency exactly as before; the shell is
inert HTML/JS that carries no config data and is useless without a token pasted
at runtime. The whole service is 127.0.0.1-only regardless (see ``wiring``), so
the shell is never publicly reachable either.

The panel is app-level state (which files, at which routes) and is registered by
``create_app`` after the API routes, so it can never shadow a ``/v1`` path.
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse, Response

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# Only these files are ever served — an explicit allowlist so a mistyped or
# hostile ``/panel/<anything>`` can never escape the static dir (no traversal,
# no serving of a Python source file that happens to sit nearby).
_ASSETS: dict[str, str] = {
    "index.html": "text/html; charset=utf-8",
    "panel.js": "application/javascript; charset=utf-8",
    "panel.schema.js": "application/javascript; charset=utf-8",
}

# The exact public (auth-exempt) shell roots. ``/`` and ``/panel`` serve the
# HTML; the JS assets load from ``/`` root (``index.html`` uses relative
# ``src="panel.js"``). The ``/panel/`` sub-namespace is also public, but which
# files it will actually serve is still the explicit ``_ASSETS`` allowlist in the
# handler (an unknown ``/panel/x`` is a clean 404, not a served file). One
# predicate is the single source of truth app.py and this module share for what
# is public — nothing under ``/v1`` ever is.
_PUBLIC_EXACT: frozenset[str] = frozenset(
    {"/", "/panel"} | {f"/{name}" for name in _ASSETS}
)
_PUBLIC_PREFIX = "/panel/"


def is_public_panel_path(path: str) -> bool:
    """True iff ``path`` is an auth-exempt static-shell route.

    Public: the exact shell roots (``/``, ``/panel``, the root JS assets) and the
    ``/panel/`` sub-namespace (whose actual file set is allowlisted in the
    handler). Everything else — every ``/v1/...`` path — authenticates.
    """
    return path in _PUBLIC_EXACT or path.startswith(_PUBLIC_PREFIX)


def _asset_response(name: str) -> Response:
    media_type = _ASSETS.get(name)
    if media_type is None:
        return JSONResponse(
            status_code=404,
            content={"error": {"code": "not_found", "message": "unknown panel asset"}},
        )
    path = _STATIC_DIR / name
    if not path.is_file():
        return JSONResponse(
            status_code=404,
            content={
                "error": {
                    "code": "not_found",
                    "message": f"panel asset missing: {name}",
                }
            },
        )
    return FileResponse(
        path,
        media_type=media_type,
        # The shell is inert; letting a browser cache it is fine and keeps the
        # loopback panel snappy. It carries no secrets.
        headers={"Cache-Control": "no-cache"},
    )


def register_panel(app: FastAPI) -> None:
    """Mount the static panel on ``app``.

    Routes (all PUBLIC static — the shell only; auth is exempted for these exact
    paths in app.py via :func:`is_public_panel_path`):
        GET /                -> index.html (operator entry)
        GET /panel           -> index.html (alias)
        GET /panel.js        -> the client logic (index.html loads it relatively)
        GET /panel.schema.js -> the testable pure form logic
        GET /panel/{asset}   -> the same assets under the /panel/ prefix

    The ``/v1`` API stays fully gated. ``include_in_schema=False`` keeps these
    off any (disabled anyway) OpenAPI surface. Registered last by ``create_app``
    so a panel route can never shadow a ``/v1`` route.
    """

    # GET + HEAD on the root: a bare HEAD / (health probes, some browsers) should
    # answer 200 with headers, not 405.
    @app.api_route("/", methods=["GET", "HEAD"], include_in_schema=False)
    def _panel_root() -> Response:  # noqa: D401 - simple handler
        return _asset_response("index.html")

    @app.get("/panel", include_in_schema=False)
    def _panel_alias() -> Response:
        return _asset_response("index.html")

    # index.html uses relative script src, so the JS loads from the root path.
    @app.get("/panel.js", include_in_schema=False)
    def _panel_js() -> Response:
        return _asset_response("panel.js")

    @app.get("/panel.schema.js", include_in_schema=False)
    def _panel_schema_js() -> Response:
        return _asset_response("panel.schema.js")

    @app.get("/panel/{asset}", include_in_schema=False)
    def _panel_asset(asset: str) -> Response:
        return _asset_response(asset)
