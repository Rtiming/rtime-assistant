# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Path addressing: get / set / get_all resolution + precedence."""

from __future__ import annotations

import pytest
from rtime_admin_core import (
    ConfigStore,
    InMemoryHistory,
    MemoryBackend,
    UnknownPathError,
)
from rtime_admin_core.metadata import split_path


def test_get_returns_schema_default(store):
    assert store.get("models.default_model") == "claude"
    assert store.get("channel-common.read_only") is False
    assert store.get("library-gateway.http_port") == 8780


def test_set_then_get_roundtrip(store):
    store.set("models.default_model", "ds", ts="t", snapshot_id="s")
    assert store.get("models.default_model") == "ds"


def test_stored_beats_default(store):
    store.set("library-gateway.http_port", 9100, ts="t", snapshot_id="s")
    assert store.get("library-gateway.http_port") == 9100


def test_env_overrides_stored_and_default(registry):
    backend = MemoryBackend(config={"models": {"default_model": "stored"}})
    store = ConfigStore(
        registry, backend, InMemoryHistory(), env={"DEFAULT_MODEL": "from-env"}
    )
    # env wins over the stored value
    assert store.get("models.default_model") == "from-env"


def test_env_is_type_coerced(registry):
    store = ConfigStore(
        registry,
        MemoryBackend(),
        InMemoryHistory(),
        env={"RTIME_LIBRARY_GATEWAY_HTTP_PORT": "8888", "RTIME_CHAT_READ_ONLY": "true"},
    )
    assert store.get("library-gateway.http_port") == 8888
    assert store.get("library-gateway.http_port").__class__ is int
    assert store.get("channel-common.read_only") is True


def test_env_alias_case_insensitive(registry):
    store = ConfigStore(
        registry, MemoryBackend(), InMemoryHistory(), env={"default_model": "lower"}
    )
    assert store.get("models.default_model") == "lower"


def test_get_all_flat_keys(store):
    all_v = store.get_all(redact=True)
    assert "models.default_model" in all_v
    assert "library-gateway.http_port" in all_v
    assert "channel-common.read_only" in all_v


def test_get_unknown_module_raises(store):
    with pytest.raises(UnknownPathError):
        store.get("nope.field")


def test_get_unknown_field_raises(store):
    with pytest.raises(UnknownPathError):
        store.get("models.no_such_field")


@pytest.mark.parametrize("bad", ["", "nodot", ".field", "module.", "a.b.c"])
def test_split_path_rejects_malformed(bad):
    with pytest.raises(ValueError):
        split_path(bad)


def test_env_empty_dict_disables_overlay(registry, monkeypatch):
    # even with a real env var set, env={} means no overlay -> default returned
    monkeypatch.setenv("DEFAULT_MODEL", "leak")
    store = ConfigStore(registry, MemoryBackend(), InMemoryHistory(), env={})
    assert store.get("models.default_model") == "claude"


def test_none_env_uses_process_env(registry, monkeypatch):
    monkeypatch.setenv("DEFAULT_MODEL", "proc")
    store = ConfigStore(registry, MemoryBackend(), InMemoryHistory(), env=None)
    assert store.get("models.default_model") == "proc"
