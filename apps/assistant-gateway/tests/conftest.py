# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Test bootstrap for the assistant-gateway: make the flat app modules importable
and put the sibling in-repo packages (rtime-config / rtime-models / rtime-admin-core)
on sys.path for a direct ``pytest apps/assistant-gateway/tests`` run without an
editable install. Mirrors apps/qq-bridge/tests/conftest.py.
"""

import sys
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parents[1]  # apps/assistant-gateway
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

_REPO = APP_ROOT.parents[1]  # repo root
for _src in (
    _REPO / "packages" / "rtime-admin-core" / "src",
    _REPO / "packages" / "rtime-config" / "src",
    _REPO / "packages" / "rtime-models" / "src",
):
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

import _shared_runtime  # noqa: E402,F401 — side effect: put rtime_config/rtime_models on path
