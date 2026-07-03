# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared fixtures for rtime-admin-core tests.

Also makes ``rtime_admin_core`` (and its ``rtime-config`` dependency) importable
when these tests are run directly (``pytest packages/rtime-admin-core/tests``)
without an editable install — belt-and-suspenders for ad-hoc local runs; the
top-level ``tests/conftest.py`` and the module-submit gate set the path too.
"""

from __future__ import annotations

import sys
from pathlib import Path

_PKGS = Path(__file__).resolve().parents[2]
for _name in ("rtime-admin-core", "rtime-config"):
    _src = _PKGS / _name / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

import pytest  # noqa: E402  (path bootstrap above must precede package imports)
from rtime_admin_core import (  # noqa: E402
    ConfigStore,
    InMemoryAuditSink,
    InMemoryHistory,
    MemoryBackend,
    default_registry,
)


@pytest.fixture
def registry():
    return default_registry()


@pytest.fixture
def sink():
    return InMemoryAuditSink()


@pytest.fixture
def store(registry, sink):
    """A ConfigStore with in-memory everything and env overlay disabled (env={}).

    env={} keeps tests deterministic — no leakage from the process env into
    ``get`` — so assertions on defaults/stored values are stable across machines.
    """
    return ConfigStore(
        registry,
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=sink,
        max_history=5,
        env={},
    )
