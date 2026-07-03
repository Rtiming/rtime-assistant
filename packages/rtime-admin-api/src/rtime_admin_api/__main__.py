# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""``python -m rtime_admin_api`` — run the admin API under uvicorn.

Env contract lives in :mod:`rtime_admin_api.wiring`. Misconfiguration exits 2
with one actionable line on stderr instead of a traceback.
"""

from __future__ import annotations

import sys


def main() -> int:
    from .wiring import LOOPBACK_HOSTS, app_from_env, host_port_from_env

    try:
        # host/port validated FIRST (incl. the non-loopback opt-in gate, defect
        # #13) so a bad bind fails before we build the app / touch the store.
        host, port = host_port_from_env()
        app = app_from_env()
    except ValueError as exc:
        print(f"rtime-admin-api: {exc}", file=sys.stderr)
        return 2

    if host not in LOOPBACK_HOSTS:
        # Reaching here means the operator explicitly opted in
        # (RTIME_ADMIN_API_ALLOW_NONLOOPBACK); still say it loudly.
        print(
            f"rtime-admin-api: WARNING binding non-loopback host {host!r} "
            "(opted in) — this API must never be reachable from the public "
            "internet",
            file=sys.stderr,
        )

    import uvicorn

    uvicorn.run(app, host=host, port=port, log_level="info")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
