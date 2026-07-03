# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime-admin-api — P3 L2 management HTTP API (thin layer over rtime-admin-core).

The HTTP face of the admin core for ONE deployment instance. All config logic
(validation, redaction, diff, snapshot/rollback, audit) lives in
``rtime_admin_core``; this package only adds transport concerns:

  - JSON endpoints under ``/v1`` (schema / config / validate / diff / apply /
    history / rollback / audit / health);
  - Caddy-style optimistic concurrency: mutations REQUIRE ``If-Match`` with the
    current config ETag (428 when missing, 412 on mismatch);
  - scoped bearer-key auth (``read`` / ``write`` / ``read:sensitive`` + per-field
    ``x-scope`` strings), constant-time compare, keys from an env-pointed file;
  - the clock/id source: the core never generates ``ts``/``snapshot_id`` — this
    layer injects UTC ISO-8601 timestamps and uuid4-hex snapshot ids;
  - in-process "pending restart" tracking surfaced via ``GET /v1/health``.

Deployment stance: intranet/localhost ONLY (bound to 127.0.0.1 by default,
reachable over Tailscale). It must NEVER be exposed publicly and stays a
separate service from any user-facing chat entry. Localhost bind does NOT waive
auth — every endpoint authenticates (defense in depth).

Primary consumer is an external ops agent; the human panel (L4) will later be a
plain client of this same API, so the surface cannot drift between the two.

Run it:  ``python -m rtime_admin_api``  (see ``wiring`` for the env contract).
"""

from __future__ import annotations

from .app import create_app
from .auth import (
    SCOPE_READ,
    SCOPE_READ_SENSITIVE,
    SCOPE_WRITE,
    ApiKey,
    load_api_keys,
    require_capability,
    require_scope,
)
from .wiring import app_from_env, build_store, make_profile_loader

# KEEP IN SYNC: version in pyproject.toml.
__version__ = "0.1.0"

__all__ = [
    "create_app",
    "app_from_env",
    "build_store",
    "make_profile_loader",
    "ApiKey",
    "load_api_keys",
    "require_capability",
    "require_scope",
    "SCOPE_READ",
    "SCOPE_WRITE",
    "SCOPE_READ_SENSITIVE",
    "__version__",
]
