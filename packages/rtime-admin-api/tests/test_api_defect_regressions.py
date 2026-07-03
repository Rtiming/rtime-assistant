# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Regression tests for the 13 adversarial-review defects (feat/p3-admin-api).

One test (or small cluster) per defect, named ``test_defect_N_*``. The three
HIGH-severity defects (#1, #2, #6) reproduce the reviewer's exact live PoC
scenario and assert it is now closed. Each fix is generalised to its class;
the class-level assertion is noted in the test docstring.
"""

from __future__ import annotations

import json
import stat

import pytest
from _helpers import (
    ADMIN_TOKEN,
    PLAIN_TOKEN,
    READER_TOKEN,
    WRITER_TOKEN,
    auth,
    get_etag,
    patch_config,
    rollback,
)
from fastapi.testclient import TestClient
from pydantic_settings import SettingsConfigDict
from rtime_admin_api import ApiKey, create_app
from rtime_admin_core import (
    ConfigStore,
    FileHistory,
    InMemoryAuditSink,
    InMemoryHistory,
    JsonlAuditSink,
    MemoryBackend,
    default_registry,
)
from rtime_config import RtimeBaseSettings, config_field, secret_field
from rtime_config.fields import Reload

SECRET = "sk-live-oracle-target-abcdef123456"


# =============================================================== HIGH #1 (oracle)
def _diff(client, token, changes):
    return client.post(
        "/v1/config/diff", json={"changes": changes}, headers=auth(token)
    )


def test_defect_1_diff_is_not_a_secret_equality_oracle(client):
    """HIGH #1 PoC: a read-scoped caller probes a secret via /v1/config/diff.

    Reviewer PoC: store a secret, then diff it with (a) the TRUE value and (b) a
    WRONG value. Before the fix, (a) dropped the path (noop) while (b) returned
    ``hmac(salt, guess)`` — the response distinguished a correct guess from a
    wrong one, and even leaked a stable per-value hash. After: BOTH return the
    exact same constant ``{***, ***}`` with the path present, so the response is
    identical whether or not the guess is right — zero value information.
    """
    # admin sets the secret
    assert (
        patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": SECRET}).status_code
        == 200
    )

    right = _diff(client, READER_TOKEN, {"models.ustc_api_key": SECRET})
    wrong = _diff(client, READER_TOKEN, {"models.ustc_api_key": "totally-wrong"})
    assert right.status_code == wrong.status_code == 200
    # path present in BOTH, identical constant content -> not distinguishable
    assert right.json()["diff"]["models.ustc_api_key"] == {
        "before": "***",
        "after": "***",
    }
    assert wrong.json()["diff"] == right.json()["diff"]
    assert SECRET not in right.text and SECRET not in wrong.text


def test_defect_1_class_validate_and_apply_also_not_oracles(client):
    """Class: EVERY endpoint reflecting a secret-derived state is plugged.

    validate and the PATCH apply-diff must not distinguish a correct secret
    guess either — same constant redaction, no salted hmac.
    """
    assert (
        patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": SECRET}).status_code
        == 200
    )
    # apply-diff of a rotation to a non-sensitive writer: constant ***, no hmac
    resp = patch_config(client, WRITER_TOKEN, {"models.ustc_api_key": SECRET + "-2"})
    assert resp.status_code == 200
    assert resp.json()["diff"]["models.ustc_api_key"] == {
        "before": "***",
        "after": "***",
    }
    assert "hmac:" not in resp.text


def test_defect_1_reveal_scope_still_gets_the_real_diff(client):
    """A read:sensitive caller keeps the informative (hashed) diff via ?reveal=1."""
    assert (
        patch_config(client, ADMIN_TOKEN, {"models.ustc_api_key": SECRET}).status_code
        == 200
    )
    resp = client.post(
        "/v1/config/diff",
        params={"reveal": 1},
        json={"changes": {"models.ustc_api_key": "new-value"}},
        headers=auth(ADMIN_TOKEN),
    )
    assert resp.status_code == 200
    change = resp.json()["diff"]["models.ustc_api_key"]
    assert change["before"].startswith("hmac:")
    assert change["after"].startswith("hmac:")
    # a non-sensitive caller passing reveal=1 is 403, learns nothing
    denied = client.post(
        "/v1/config/diff",
        params={"reveal": 1},
        json={"changes": {"models.ustc_api_key": "x"}},
        headers=auth(READER_TOKEN),
    )
    assert denied.status_code == 403


# ========================================================== HIGH #2 (rollback scope)
class _EnvPinConfig(RtimeBaseSettings):
    """A module whose fields declare x-scope, for rollback-scope PoC."""

    model_config = SettingsConfigDict(env_prefix="RTIME_ROLLBACKSCOPE_")

    guarded: str = config_field(
        "base", description="scoped field", scope="write:special"
    )


def _scoped_app():
    reg = default_registry()
    reg.register("rbscope", _EnvPinConfig)
    sink = InMemoryAuditSink()
    store = ConfigStore(
        reg,
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=sink,
        env={},
        secret_salt="rb-salt",
    )
    keys = [
        # holds write:special (can create the state)
        ApiKey(
            name="special",
            key="special-token-000000000000",
            scopes=frozenset(("read", "write", "write:special")),
        ),
        # write but NOT write:special (the attacker in the PoC)
        ApiKey(
            name="plain",
            key="plain-token-0000000000000",
            scopes=frozenset(("read", "write")),
        ),
    ]
    return TestClient(
        create_app(store, api_keys=keys, audit_reader=lambda: [], version="rb")
    ), store


def test_defect_2_rollback_enforces_field_scope(client):
    """HIGH #2 PoC: a plain-write key changes a scoped field via rollback.

    Reviewer PoC: an admin changes models.default_model (x-scope=write:models)
    to create snapshots, then a key holding only plain ``write`` (no
    write:models) calls /v1/rollback to move that scoped field — before the fix
    rollback checked only ``write`` and let it through, bypassing per-field
    scope. After: rollback computes the x-scope union over the paths it would
    change and 403s the plain key.
    """
    # admin (has write:models) creates two snapshots moving the scoped field
    assert (
        patch_config(client, ADMIN_TOKEN, {"models.default_model": "kimi"}).status_code
        == 200
    )
    r2 = patch_config(client, ADMIN_TOKEN, {"models.default_model": "deepseek"})
    assert r2.status_code == 200
    # plain writer tries to roll back the scoped field -> 403 (lacks write:models)
    resp = rollback(client, PLAIN_TOKEN, r2.json()["snapshot_id"])
    assert resp.status_code == 403
    assert "write:models" in resp.json()["error"]["message"]
    # nothing changed
    got = client.get("/v1/config/models.default_model", headers=auth(ADMIN_TOKEN))
    assert got.json()["value"] == "deepseek"


def test_defect_2_rollback_allowed_when_scope_held(client):
    """The holder of the field scope CAN roll it back (fix is not over-broad)."""
    assert (
        patch_config(client, WRITER_TOKEN, {"models.default_model": "kimi"}).status_code
        == 200
    )
    r2 = patch_config(client, WRITER_TOKEN, {"models.default_model": "deepseek"})
    assert r2.status_code == 200
    resp = rollback(client, WRITER_TOKEN, r2.json()["snapshot_id"])
    assert resp.status_code == 200
    got = client.get("/v1/config/models.default_model", headers=auth(WRITER_TOKEN))
    assert got.json()["value"] == "kimi"


def test_defect_2_rollback_scopeless_change_needs_only_write(client):
    """A rollback that touches only scope-less fields needs just ``write``."""
    step = patch_config(client, PLAIN_TOKEN, {"sandbox.greeting": "changed"})
    assert step.status_code == 200
    back = rollback(client, PLAIN_TOKEN, step.json()["snapshot_id"])
    assert back.status_code == 200


# ================================================================ HIGH #6 (ETag)
class _EnvPinnedSecret(RtimeBaseSettings):
    """A field that is ALSO pinned by an env var, so the resolved (env-merged)
    view never moves — only the persisted layer does."""

    model_config = SettingsConfigDict(env_prefix="RTIME_PIN_")

    pinned: str = config_field(
        "default",
        description="env-pinned field",
        reload=Reload.HOT,
        env_aliases=["RTIME_PIN_PINNED"],
    )


def _pinned_app(env):
    reg = default_registry()
    reg.register("pin", _EnvPinnedSecret)
    store = ConfigStore(
        reg,
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=InMemoryAuditSink(),
        env=env,
        secret_salt="pin-salt",
    )
    keys = [
        ApiKey(
            name="admin",
            key=ADMIN_TOKEN,
            scopes=frozenset(("read", "write", "read:sensitive")),
        )
    ]
    return TestClient(
        create_app(store, api_keys=keys, audit_reader=lambda: [], version="pin")
    ), store


def test_defect_6_etag_moves_on_env_pinned_persisted_write():
    """HIGH #6 PoC: a write to an env-pinned field is silently dropped.

    Reviewer PoC: field ``pin.pinned`` is pinned by env (resolved value is
    always the env value). Client A GETs the ETag, writes ``pin.pinned`` (a real
    persisted change, but the resolved get_all view does not move because env
    wins), then client B — holding the SAME pre-write ETag — writes again.
    Before the fix the ETag was computed over get_all (env-merged), so it did
    NOT change across A's write, and B's stale ETag still "matched" → B's write
    silently clobbered A's (lost update). After: the ETag is computed over the
    PERSISTED layer, so A's write moves it and B gets 412.
    """
    client, store = _pinned_app({"RTIME_PIN_PINNED": "ENVWINS"})
    # resolved value is env-pinned regardless of persisted writes
    assert store.get("pin.pinned") == "ENVWINS"

    etag0 = get_etag(client, ADMIN_TOKEN)
    a = patch_config(client, ADMIN_TOKEN, {"pin.pinned": "A-writes"}, etag=etag0)
    assert a.status_code == 200
    # resolved view unchanged (env still wins) ...
    assert store.get("pin.pinned") == "ENVWINS"
    # ... but the ETag MUST have moved because the persisted layer changed
    assert a.json()["etag"] != etag0
    # B replays the stale pre-write ETag -> 412 (no silent lost update)
    b = patch_config(client, ADMIN_TOKEN, {"pin.pinned": "B-writes"}, etag=etag0)
    assert b.status_code == 412
    # A's persisted value survived
    assert store.persisted_flat()["pin.pinned"] == "A-writes"


def test_defect_6_etag_is_over_persisted_layer(client, store):
    """Class: ETag reflects the persisted layer, so any persisted write moves
    it even when the resolved view would not."""
    from rtime_admin_api.app import compute_etag

    before = compute_etag(store)
    store.apply(
        {"channel-common.read_only": True},
        ts="t",
        snapshot_id="s-etag",
        actor="t",
        source="t",
    )
    assert compute_etag(store) != before


# ================================================================= MED #5 (msg)
class _SecretValidated(RtimeBaseSettings):
    """A secret field whose validator error message echoes the value — the
    class of leak where the message (not just input/ctx) carries the secret."""

    model_config = SettingsConfigDict(env_prefix="RTIME_SV_")

    token: str | None = secret_field(
        None,
        description="secret with a min length so a short value is rejected",
        min_length=8,
    )


def _secretmsg_app():
    reg = default_registry()
    reg.register("sv", _SecretValidated)
    store = ConfigStore(
        reg,
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=InMemoryAuditSink(),
        env={},
        secret_salt="sv-salt",
    )
    keys = [
        ApiKey(
            name="admin",
            key=ADMIN_TOKEN,
            scopes=frozenset(("read", "write", "read:sensitive")),
        ),
        ApiKey(name="writer", key=WRITER_TOKEN, scopes=frozenset(("read", "write"))),
    ]
    return TestClient(
        create_app(store, api_keys=keys, audit_reader=lambda: [], version="sv")
    )


def test_defect_5_secret_field_error_message_is_redacted():
    """MED #5: pydantic error MESSAGE (not just input) can echo the secret.

    Every error FACE (message/input/ctx) is scrubbed for a secret field for a
    non-sensitive caller — via both validate and PATCH.
    """
    app = _secretmsg_app()
    short_secret = "shortXY"  # < min_length 8 -> rejected, may appear in msg/ctx

    # validate: writer (no read:sensitive)
    v = app.post(
        "/v1/config/validate",
        json={"changes": {"sv.token": short_secret}},
        headers=auth(WRITER_TOKEN),
    )
    assert v.status_code == 200
    body = v.json()
    assert body["ok"] is False
    err = next(e for e in body["errors"] if e["path"] == "sv.token")
    assert err["message"] == "invalid value for secret field (redacted)"
    assert err.get("input") == "***"
    assert "ctx" not in err
    assert short_secret not in v.text

    # PATCH: same field, 422 body must also be scrubbed
    etag = get_etag(app, WRITER_TOKEN)
    p = app.patch(
        "/v1/config",
        json={"changes": {"sv.token": short_secret}},
        headers={**auth(WRITER_TOKEN), "If-Match": etag},
    )
    assert p.status_code == 422
    assert short_secret not in p.text
    perr = next(e for e in p.json()["error"]["errors"] if e["path"] == "sv.token")
    assert perr["message"] == "invalid value for secret field (redacted)"


# ================================================================ MED #7 (torn)
def test_defect_7_config_values_and_etag_are_self_consistent(client):
    """MED #7: the (values, etag) pair from GET /v1/config must describe ONE
    state — the etag returned is exactly the one a subsequent PATCH will accept.

    (Race timing is hard to force deterministically in a unit test; we assert
    the invariant that the returned etag round-trips: GET etag -> PATCH with it
    succeeds against the state GET reported.)
    """
    resp = client.get("/v1/config", headers=auth(READER_TOKEN))
    etag = resp.json()["etag"]
    ok = patch_config(client, ADMIN_TOKEN, {"sandbox.greeting": "sync"}, etag=etag)
    assert ok.status_code == 200


# ============================================================ MED #10 / LOW #11 (modes)
def test_defect_10_audit_log_is_0600(tmp_path):
    """MED #10: the JSONL audit log must be created owner-only (0600)."""
    sink = JsonlAuditSink(tmp_path / "audit.jsonl")
    from rtime_admin_core import AuditEntry

    sink(AuditEntry(ts="t", actor="a", source="s", action="apply", outcome="ok"))
    mode = stat.S_IMODE((tmp_path / "audit.jsonl").stat().st_mode)
    assert mode == 0o600
    # parent dir tightened to 0700
    assert stat.S_IMODE(tmp_path.stat().st_mode) == 0o700


def test_defect_10_audit_log_existing_0644_is_tightened(tmp_path):
    """Class: a legacy world-readable log is re-chmod'd to 0600 on next append."""
    p = tmp_path / "audit.jsonl"
    p.write_text("", encoding="utf-8")
    p.chmod(0o644)
    from rtime_admin_core import AuditEntry

    sink = JsonlAuditSink(p)
    sink(AuditEntry(ts="t", actor="a", source="s", action="apply", outcome="ok"))
    assert stat.S_IMODE(p.stat().st_mode) == 0o600


def test_defect_11_history_dir_is_0700(tmp_path):
    """LOW #11: the snapshot history directory must be 0700 (holds secrets)."""
    from rtime_admin_core import Snapshot

    hist = FileHistory(tmp_path / "history")
    hist.add(Snapshot(id="s1", ts="2026-01-01T00:00:00+00:00", config={}, secrets={}))
    assert stat.S_IMODE((tmp_path / "history").stat().st_mode) == 0o700


def test_defect_11_store_dir_is_0700(tmp_path):
    """LOW #11 (class): build_store creates the whole store dir 0700."""
    from rtime_admin_api.wiring import build_store

    store_dir = tmp_path / "store"
    build_store(store_dir, env={})
    assert stat.S_IMODE(store_dir.stat().st_mode) == 0o700


# ================================================================= LOW #3 (auth-first)
def test_defect_3_malformed_body_unauthenticated_is_401_not_422(client):
    """LOW #3: auth runs BEFORE body parsing, so an unauthenticated caller
    sending a malformed body gets 401, not a 422 that reveals the endpoint
    parses bodies pre-auth."""
    # no token + body that would fail schema validation
    resp = client.patch("/v1/config", json={"changes": "not-an-object"})
    assert resp.status_code == 401
    # bad token, same body -> still 401
    resp2 = client.patch(
        "/v1/config",
        json={"changes": 12345},
        headers=auth("wrong-token-00000000"),
    )
    assert resp2.status_code == 401
    # authenticated caller with the same malformed body -> 422 (body reached)
    etag = get_etag(client, ADMIN_TOKEN)
    resp3 = client.patch(
        "/v1/config",
        json={"changes": "not-an-object"},
        headers={**auth(ADMIN_TOKEN), "If-Match": etag},
    )
    assert resp3.status_code == 422


# ================================================================== LOW #4 (docs)
@pytest.mark.parametrize("path", ["/openapi.json", "/docs", "/redoc"])
def test_defect_4_openapi_docs_are_disabled(client, path):
    """LOW #4: the schema/docs surfaces are OFF — even field names are not
    exposed to an unauthenticated caller. Authenticated /v1/schema is the way."""
    resp = client.get(path)
    assert resp.status_code == 404
    # and the authenticated schema endpoint still works
    ok = client.get("/v1/schema", headers=auth(READER_TOKEN))
    assert ok.status_code == 200


# ============================================================= LOW #8 (If-Match list)
def test_defect_8_if_match_list_matches_any_member(client):
    """LOW #8: a comma-separated If-Match list passes if ANY tag matches
    (RFC 7232); before, the whole list string was compared as one opaque tag
    and always 412'd."""
    etag = get_etag(client, ADMIN_TOKEN)
    header = f'"deadbeef", "{etag}", "cafebabe"'
    resp = client.patch(
        "/v1/config",
        json={"changes": {"sandbox.greeting": "listed"}},
        headers={**auth(ADMIN_TOKEN), "If-Match": header},
    )
    assert resp.status_code == 200, resp.text


def test_defect_8_if_match_list_all_wrong_is_412(client):
    etag = get_etag(client, ADMIN_TOKEN)  # noqa: F841 (drift the state below)
    resp = client.patch(
        "/v1/config",
        json={"changes": {"sandbox.greeting": "x"}},
        headers={**auth(ADMIN_TOKEN), "If-Match": '"aaa", "bbb"'},
    )
    assert resp.status_code == 412


# ================================================================= LOW #9 (flock)
def test_defect_9_cross_process_flock_wraps_mutation(tmp_path):
    """LOW #9: mutations take a cross-process advisory flock on <store>/.lock.

    We can't spawn a second process in a unit test cheaply, but we CAN assert
    the lock file is created and that a held flock blocks a second acquisition
    (the mechanism the mutation guard relies on)."""
    import fcntl  # POSIX; skip elsewhere

    from rtime_admin_api.locking import FileLock

    lock = FileLock(tmp_path)
    with lock:
        assert (tmp_path / ".lock").exists()
        # a second, independent fd cannot take the exclusive lock (non-blocking)
        fd = __import__("os").open(str(tmp_path / ".lock"), __import__("os").O_RDWR)
        try:
            with pytest.raises(BlockingIOError):
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        finally:
            __import__("os").close(fd)
    # released after the with-block: now acquirable
    fd2 = __import__("os").open(str(tmp_path / ".lock"), __import__("os").O_RDWR)
    try:
        fcntl.flock(fd2, fcntl.LOCK_EX | fcntl.LOCK_NB)  # no raise
        fcntl.flock(fd2, fcntl.LOCK_UN)
    finally:
        __import__("os").close(fd2)


def test_defect_9_file_backed_app_mutations_hold_the_flock(tmp_path):
    """Class: the file-backed app (via wiring) passes lock_dir so the guard
    engages; a full apply round-trips and the lock file exists afterwards."""
    keys_file = tmp_path / "keys.json"
    keys_file.write_text(
        json.dumps(
            [
                {
                    "name": "admin",
                    "key": ADMIN_TOKEN,
                    "scopes": ["read", "write", "write:models"],
                }
            ]
        ),
        encoding="utf-8",
    )
    from rtime_admin_api.wiring import app_from_env

    app = app_from_env(
        {
            "RTIME_ADMIN_STORE_DIR": str(tmp_path / "store"),
            "RTIME_ADMIN_API_KEYS": str(keys_file),
        }
    )
    client = TestClient(app)
    resp = patch_config(client, ADMIN_TOKEN, {"models.default_model": "kimi"})
    assert resp.status_code == 200, resp.text
    assert (tmp_path / "store" / ".lock").exists()


# ================================================================= LOW #12 (500)
def test_defect_12_unhandled_error_keeps_error_envelope(store, api_keys):
    """LOW #12: an unexpected exception is wrapped in the {error:...} envelope
    (500) rather than a raw traceback/text, and never leaks the exception str."""

    def _boom():
        raise RuntimeError("secret-bearing internal detail should-not-leak")

    app = create_app(store, api_keys=api_keys, audit_reader=_boom, version="boom")
    # do not let the TestClient re-raise server exceptions; assert the response
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/v1/audit", headers=auth(READER_TOKEN))
    assert resp.status_code == 500
    body = resp.json()
    assert body["error"]["code"] == "internal_error"
    assert body["error"]["message"] == "internal server error"
    assert "should-not-leak" not in resp.text


# ================================================================= LOW #13 (bind)
def test_defect_13_nonloopback_bind_refused_without_optin():
    """LOW #13: a non-loopback HOST is refused at startup unless opted in."""
    from rtime_admin_api.wiring import host_port_from_env

    with pytest.raises(ValueError, match="not a loopback"):
        host_port_from_env({"RTIME_ADMIN_API_HOST": "0.0.0.0"})
    # opt-in permits it
    assert host_port_from_env(
        {
            "RTIME_ADMIN_API_HOST": "0.0.0.0",
            "RTIME_ADMIN_API_ALLOW_NONLOOPBACK": "1",
        }
    ) == ("0.0.0.0", 8790)
    # loopback never needs the flag
    assert host_port_from_env({"RTIME_ADMIN_API_HOST": "127.0.0.1"}) == (
        "127.0.0.1",
        8790,
    )
