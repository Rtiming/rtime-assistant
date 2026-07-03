# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T8: ``POST /v1/profiles/{id}:reload`` — atomic validate-then-swap of the git
profile layer, hot/restart classification, and the security/error contract
(design §2.10 + §6.2).

Everything is in-memory (MemoryBackend + InMemoryHistory + InMemoryAuditSink,
``env={}``, fixed salt) and offline — the ``profile_loader`` is a STUB returning a
canned compiled layer, so these tests are about the ENDPOINT contract (auth, hot vs
restart, atomicity, audit, error mapping), not the rtime-config loader (its own suite
covers compilation). One test exercises the real ``make_profile_loader`` end to end.
"""

from __future__ import annotations

import importlib.util

import pytest
from _helpers import ADMIN_TOKEN, READER_TOKEN, WRITER_TOKEN, auth
from fastapi.testclient import TestClient
from rtime_admin_api import ApiKey, create_app
from rtime_admin_core import (
    ConfigStore,
    InMemoryAuditSink,
    InMemoryHistory,
    MemoryBackend,
    default_registry,
)

# The reload endpoint projects onto the REAL qq module; skip if qq-bridge's config
# (the projection target) is not importable in this environment.
pytestmark = pytest.mark.skipif(
    importlib.util.find_spec("qq_bridge") is None,
    reason="qq_bridge (projection target for qq.* profile paths) not importable",
)

# read_only is a RESTART-level field (x-reload != hot); system_prompt / model /
# user lists are HOT — the qq module declares these (see qq-bridge config.py).
HOT_PATH = "qq.system_prompt"
RESTART_PATH = "qq.read_only"


def _keys() -> list[ApiKey]:
    return [
        ApiKey(
            name="admin",
            key=ADMIN_TOKEN,
            scopes=frozenset(("read", "write", "read:sensitive")),
        ),
        ApiKey(name="writer", key=WRITER_TOKEN, scopes=frozenset(("read", "write"))),
        ApiKey(name="reader", key=READER_TOKEN, scopes=frozenset(("read",))),
    ]


def _registry():
    return default_registry(include_qq=True)


def _store():
    sink = InMemoryAuditSink()
    store = ConfigStore(
        _registry(),
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=sink,
        max_history=10,
        env={},
        secret_salt="test-salt-reload",
        # start with a profile layer so a reload shows a DIFF against it.
        profile_layer={HOT_PATH: "第一版", RESTART_PATH: False},
    )
    return store, sink


def _app(store, sink, *, loader):
    return create_app(
        store,
        api_keys=_keys(),
        audit_reader=lambda: [e.to_dict() for e in sink.entries],
        version="0.0-test",
        profile_loader=loader,
    )


def _client(loader):
    store, sink = _store()
    app = _app(store, sink, loader=loader)
    return TestClient(app), store, sink


# =====================================================================
# happy path: hot vs restart classification + audit
# =====================================================================
def test_reload_swaps_layer_and_classifies_hot_vs_restart():
    """A reload that changes a HOT field and a RESTART field returns both, correctly
    partitioned, and the store's live layer is swapped."""
    new_layer = {HOT_PATH: "第二版", RESTART_PATH: True}
    client, store, sink = _client(lambda pid: dict(new_layer))

    resp = client.post("/v1/profiles/studentunion:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["ok"] is True
    assert body["profile_id"] == "studentunion"
    assert HOT_PATH in body["hot"]
    assert RESTART_PATH in body["restart_required"]
    assert set(body["changed"]) == {HOT_PATH, RESTART_PATH}
    # the store actually swapped:
    assert store.profile_layer == new_layer
    assert store.get(HOT_PATH) == "第二版"  # hot value live immediately

    # one profile_reload audit entry (actor = key name, source http).
    reloads = [e for e in sink.entries if e.action == "profile_reload"]
    assert len(reloads) == 1
    assert reloads[0].actor == "writer" and reloads[0].source == "http"
    assert reloads[0].outcome == "ok"


def test_restart_required_surfaces_in_health():
    """A restart-level change from a reload adds to the pending-restart set (health)."""
    client, store, sink = _client(lambda pid: {RESTART_PATH: True})
    client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN))
    health = client.get("/v1/health", headers=auth(READER_TOKEN)).json()
    assert RESTART_PATH in health["needs_restart"]


def test_reload_with_only_hot_change_needs_no_restart():
    client, store, sink = _client(
        lambda pid: {HOT_PATH: "热改", RESTART_PATH: False}  # restart unchanged
    )
    body = client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN)).json()
    assert body["hot"] == [HOT_PATH]
    assert body["restart_required"] == []
    health = client.get("/v1/health", headers=auth(READER_TOKEN)).json()
    assert health["needs_restart"] == []


# =====================================================================
# atomicity: a validation failure keeps the OLD layer active (no partial swap)
# =====================================================================
def test_validation_failure_keeps_old_layer():
    """An invalid new layer -> 422, and the store keeps the PREVIOUS profile layer."""
    # read_only must be a bool; a string fails validation.
    client, store, sink = _client(lambda pid: {RESTART_PATH: "not-a-bool"})
    before = store.profile_layer
    resp = client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 422, resp.text
    assert resp.json()["error"]["code"] == "profile_validation_failed"
    # OLD layer still active (Caddy /load: never a partial swap).
    assert store.profile_layer == before
    # the failure is audited too.
    reloads = [e for e in sink.entries if e.action == "profile_reload"]
    assert reloads and reloads[-1].outcome == "error"


def test_unknown_path_in_layer_is_422():
    client, store, sink = _client(lambda pid: {"nope.field": 1})
    resp = client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 422


# =====================================================================
# loader errors map to 4xx, never 500
# =====================================================================
def test_missing_profile_is_404():
    def _loader(pid):
        raise FileNotFoundError("no such profile")

    client, store, sink = _client(_loader)
    resp = client.post("/v1/profiles/ghost:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 404
    assert resp.json()["error"]["code"] == "unknown_profile"


def test_loader_failure_is_422_not_500():
    def _loader(pid):
        raise ValueError("bad YAML / inlined secret / projection error")

    client, store, sink = _client(_loader)
    resp = client.post("/v1/profiles/broken:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 422
    assert resp.json()["error"]["code"] == "profile_load_failed"


# =====================================================================
# feature gate: no loader wired -> 501
# =====================================================================
def test_no_loader_returns_501():
    client, store, sink = _client(None)
    resp = client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 501
    assert resp.json()["error"]["code"] == "profile_reload_unavailable"


# =====================================================================
# auth: reload requires WRITE scope
# =====================================================================
def test_reader_cannot_reload():
    client, store, sink = _client(lambda pid: {HOT_PATH: "x"})
    resp = client.post("/v1/profiles/su:reload", headers=auth(READER_TOKEN))
    assert resp.status_code == 403


def test_unauthenticated_cannot_reload():
    client, store, sink = _client(lambda pid: {HOT_PATH: "x"})
    resp = client.post("/v1/profiles/su:reload")
    assert resp.status_code == 401


# =====================================================================
# real loader end-to-end (make_profile_loader over an on-disk profile)
# =====================================================================
def test_real_profile_loader_reload_end_to_end(tmp_path):
    """The wired loader compiles a real profile.yaml and the reload takes effect."""
    from rtime_admin_api import make_profile_loader

    root = tmp_path / "profiles"
    (root / "_base" / "prompts").mkdir(parents=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text("b\n", encoding="utf-8")
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\n", encoding="utf-8"
    )
    pdir = root / "su"
    (pdir / "prompts").mkdir(parents=True)
    (pdir / "prompts" / "system.md").write_text("学生会提示词\n", encoding="utf-8")
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        "profile:\n  id: su\n  extends: _base/qq\n"
        "identity:\n  system_prompt_file: prompts/system.md\n"
        "model:\n  default: ds\n"
        "permissions:\n  read_only: true\n",
        encoding="utf-8",
    )

    store, sink = _store()
    loader = make_profile_loader(str(root), store)
    app = _app(store, sink, loader=loader)
    client = TestClient(app)

    resp = client.post("/v1/profiles/su:reload", headers=auth(WRITER_TOKEN))
    assert resp.status_code == 200, resp.text
    body = resp.json()
    # system_prompt (hot) reached the live layer; read_only (restart) is classified.
    assert store.get(HOT_PATH).strip() == "学生会提示词"
    assert HOT_PATH in body["hot"]
    assert RESTART_PATH in body["restart_required"]

    # a bad profile id 404s through the real loader.
    assert (
        client.post("/v1/profiles/ghost:reload", headers=auth(WRITER_TOKEN)).status_code
        == 404
    )
