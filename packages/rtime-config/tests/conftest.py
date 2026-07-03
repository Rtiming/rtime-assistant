# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Test bootstrap for rtime-config.

Puts rtime-config on the path (for direct ``pytest packages/rtime-config/tests``
runs without an editable install), and — for the profile tests that project onto
the REAL qq module — best-effort adds rtime-admin-core and the qq-bridge app so
``default_registry(include_qq=True)`` works. Tests that need those skip cleanly if
they are unavailable (see the ``qq_registry`` fixture).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_PKGS = Path(__file__).resolve().parents[2]
_REPO = _PKGS.parent

for _src in (
    _PKGS / "rtime-config" / "src",
    _PKGS / "rtime-admin-core" / "src",
    _REPO / "apps" / "qq-bridge",
):
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))


@pytest.fixture
def qq_registry():
    """The admin-core registry INCLUDING the real qq module, or skip if unavailable."""
    try:
        from rtime_admin_core import default_registry
    except Exception:  # pragma: no cover - admin-core not on path
        pytest.skip("rtime-admin-core not importable")
    try:
        return default_registry(include_qq=True)
    except ModuleNotFoundError:  # pragma: no cover - qq-bridge app not on path
        pytest.skip("qq-bridge app not importable (qq module unavailable)")


@pytest.fixture
def validate_state_fn():
    from rtime_admin_core import validate_state

    return validate_state
