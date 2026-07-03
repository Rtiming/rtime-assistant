# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared fixtures for rtime-admin-api tests.

Bootstraps ``sys.path`` so the suite runs directly
(``pytest packages/rtime-admin-api/tests``) without an editable install —
same belt-and-suspenders as the admin-core suite; the top-level
``tests/conftest.py`` covers repo-root runs.

Everything is offline and in-memory: MemoryBackend + InMemoryHistory +
InMemoryAuditSink, ``env={}`` (no process-env leakage), fixed ``secret_salt``
(deterministic ETags), and starlette's TestClient (no socket binding).
"""

from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PKGS = _HERE.parents[1]
for _name in ("rtime-admin-api", "rtime-admin-core", "rtime-config"):
    _src = _PKGS / _name / "src"
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))
# T8 profile-reload tests project onto the REAL qq module (default_registry(
# include_qq=True) imports qq_bridge.config, which depends only on rtime-config —
# NOT the chat runtime). Add the qq-bridge app root best-effort so the reload suite
# can validate a compiled qq.* profile layer; the tests guard with find_spec and
# skip cleanly if it is unavailable.
_REPO = _PKGS.parent  # repo root (packages/.. )
_QQ_ROOT = _REPO / "apps" / "qq-bridge"
if _QQ_ROOT.is_dir() and str(_QQ_ROOT) not in sys.path:
    sys.path.insert(0, str(_QQ_ROOT))
# make `import _helpers` deterministic for this suite's modules
if str(_HERE) not in sys.path:
    sys.path.insert(0, str(_HERE))

import pytest  # noqa: E402  (path bootstrap above must precede package imports)
from _helpers import (  # noqa: E402
    ADMIN_TOKEN,
    FIELD_SCOPES,
    PLAIN_TOKEN,
    READER_TOKEN,
    WRITER_TOKEN,
)
from fastapi.testclient import TestClient  # noqa: E402
from pydantic_settings import SettingsConfigDict  # noqa: E402
from rtime_admin_api import ApiKey, create_app  # noqa: E402
from rtime_admin_core import (  # noqa: E402
    ConfigStore,
    InMemoryAuditSink,
    InMemoryHistory,
    MemoryBackend,
    default_registry,
)
from rtime_config import RtimeBaseSettings, config_field, secret_field  # noqa: E402
from rtime_config.fields import Reload  # noqa: E402


class SandboxConfig(RtimeBaseSettings):
    """Scope-less test module: exercises the 'field without x-scope is open to
    plain write' default, plus hot/restart and secret redaction on such fields."""

    model_config = SettingsConfigDict(env_prefix="RTIME_SANDBOX_")

    greeting: str = config_field(
        "hello", description="scope-less hot field", reload=Reload.HOT
    )
    knob: int = config_field(1, description="scope-less restart field", ge=0)
    credential: str | None = secret_field(
        None, description="scope-less secret field", reload=Reload.HOT
    )


@pytest.fixture
def registry():
    reg = default_registry()
    reg.register("sandbox", SandboxConfig)
    return reg


@pytest.fixture
def sink():
    return InMemoryAuditSink()


@pytest.fixture
def store(registry, sink):
    return ConfigStore(
        registry,
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=sink,
        max_history=10,
        env={},
        secret_salt="test-salt-0001",
    )


def _api_keys() -> list[ApiKey]:
    return [
        ApiKey(
            name="admin",
            key=ADMIN_TOKEN,
            scopes=frozenset(("read", "write", "read:sensitive", *FIELD_SCOPES)),
        ),
        ApiKey(
            name="writer",
            key=WRITER_TOKEN,
            scopes=frozenset(("read", "write", *FIELD_SCOPES)),
        ),
        ApiKey(
            name="plain-writer", key=PLAIN_TOKEN, scopes=frozenset(("read", "write"))
        ),
        ApiKey(name="reader", key=READER_TOKEN, scopes=frozenset(("read",))),
    ]


@pytest.fixture
def api_keys():
    return _api_keys()


@pytest.fixture
def app(store, api_keys, sink):
    return create_app(
        store,
        api_keys=api_keys,
        audit_reader=lambda: [e.to_dict() for e in sink.entries],
        version="0.0-test",
    )


@pytest.fixture
def client(app):
    return TestClient(app)
