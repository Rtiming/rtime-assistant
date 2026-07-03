# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Regression tests for the 13 adversarial-review defects.

Each test FAILS on the pre-fix code and passes after the fix. Numbers match the
findings list in the task brief; each test names its defect in the docstring.
"""

from __future__ import annotations

import os
import stat

import pytest
from pydantic import model_validator
from pydantic_settings import SettingsConfigDict
from rtime_admin_core import (
    OUTCOME_ERROR,
    ConfigStore,
    FileBackend,
    FileHistory,
    InMemoryHistory,
    MemoryBackend,
    Registry,
    Snapshot,
    ValidationError,
    default_registry,
    hash_secret,
    redact_diff,
)
from rtime_admin_core.validation import validate_module
from rtime_config import RtimeBaseSettings, config_field, secret_field


def _mode(path):
    return stat.S_IMODE(os.stat(path).st_mode)


def _store(env=None, **kw):
    return ConfigStore(
        default_registry(),
        MemoryBackend(),
        InMemoryHistory(),
        env=env if env is not None else {},
        secret_salt="fixed-test-salt",
        **kw,
    )


# --- #10: a malformed env value on ANY field must not brick ALL set/apply -------
def test_defect_10_bad_unrelated_env_does_not_brick_writes():
    store = _store(env={"RTIME_LIBRARY_GATEWAY_HTTP_PORT": "70000"})
    # editing an UNTOUCHED module must succeed despite the bad env on another one
    res = store.set("models.default_model", "ds", ts="t", snapshot_id="s")
    assert res.changed == ["models.default_model"]
    assert store.get("models.default_model") == "ds"


def test_defect_10_self_violating_edit_still_rejected():
    store = _store(env={"RTIME_LIBRARY_GATEWAY_HTTP_PORT": "70000"})
    # an edit that itself violates a constraint must still be rejected
    with pytest.raises(ValidationError):
        store.set("library-gateway.http_port", 70000, ts="t", snapshot_id="s")


# --- #12: validate_module/validate_state must be env-independent ----------------
def test_defect_12_validation_is_env_independent(monkeypatch):
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_HTTP_PORT", "70000")
    reg = default_registry()
    # a bad, unrelated env var must NOT contaminate validation of this slice
    assert validate_module(reg, "library-gateway", {"idle_timeout": 100}) == []


# --- #2: rollback diff/changed/restart from the PERSISTED layer ----------------
def test_defect_2_rollback_diff_reflects_persisted_change_under_env_pin():
    sink_entries = []
    store = ConfigStore(
        default_registry(),
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=sink_entries.append,
        env={"DEFAULT_MODEL": "env-pinned"},  # get() would always return this
        secret_salt="fixed-test-salt",
    )
    # snapshot the pre-state, then persist a real change to the persisted layer
    store.snapshot("pre", ts="t0")
    store.apply(
        {
            "models.default_model": "persisted-v1",  # hot field
            "library-gateway.http_port": 9099,  # restart field
        },
        ts="t1",
        snapshot_id="s1",
    )
    rb = store.rollback("pre", ts="t2", new_snapshot_id="s2")
    # the persisted change is real even though env pins the read view
    assert "models.default_model" in rb.changed
    assert rb.diff["models.default_model"]["before"] == "persisted-v1"
    # reload classification is populated from the real persisted change:
    # the hot field lands in hot, the restart field populates restart_required
    # (the whole point — under the env pin the diff was previously empty and no
    # restart signal was emitted).
    assert "models.default_model" in rb.hot
    assert "library-gateway.http_port" in rb.restart_required
    # audit diff is non-empty and records the real persisted change
    audit = sink_entries[-1]
    assert audit.action == "rollback"
    assert audit.diff.get("models.default_model")


# --- #1: apply() atomic across config + secrets --------------------------------
class _SecretsFailBackend(MemoryBackend):
    def save_secrets(self, secrets):
        raise OSError("secrets store write failed")


def test_defect_1_apply_is_atomic_across_config_and_secrets():
    entries = []
    store = ConfigStore(
        default_registry(),
        _SecretsFailBackend(),
        InMemoryHistory(),
        audit_hook=entries.append,
        env={},
        secret_salt="fixed-test-salt",
    )
    pre_config = store.backend.load_config()
    with pytest.raises(OSError):
        store.apply(
            {"models.default_model": "ds", "models.ustc_api_key": "sk-1"},
            ts="t",
            snapshot_id="s",
        )
    # config must not be half-applied — persisted state equals pre-apply state
    assert store.backend.load_config() == pre_config
    assert store.get("models.default_model") == "claude"  # default, not "ds"
    # an error audit entry was emitted
    err = [e for e in entries if e.outcome == OUTCOME_ERROR]
    assert err and err[-1].action == "apply"


# --- #6: model-level validator must not leak the whole input (with a secret) ----
def _registry_with_crossfield_secret_module() -> Registry:
    class CrossField(RtimeBaseSettings):
        model_config = SettingsConfigDict(env_prefix="XF_")

        token: str | None = secret_field(None, description="secret token")
        floor: int = config_field(1, description="floor", ge=0)

        @model_validator(mode="after")
        def _cross(self):
            if self.floor < 1:
                raise ValueError("floor must be >= 1 when token set")
            return self

    reg = Registry()
    reg.register("xf", CrossField)
    return reg


def test_defect_6_model_level_error_does_not_echo_secret():
    reg = _registry_with_crossfield_secret_module()
    errs = validate_module(reg, "xf", {"token": "sk-PLAINTEXT-SECRET", "floor": 0})
    assert errs  # the cross-field validator fired
    blob = " ".join(str(e.input) for e in errs)
    assert "sk-PLAINTEXT-SECRET" not in blob
    for e in errs:
        assert e.input != {"token": "sk-PLAINTEXT-SECRET", "floor": 0}


# --- #7: alias-keyed secret error must still be masked --------------------------
def test_defect_7_alias_keyed_secret_masked():
    reg = default_registry()
    # address the secret by its env-alias with a bad (non-str/None) value
    errs = validate_module(reg, "models", {"RTIME_USTC_API_KEY": ["not-a-string"]})
    assert errs
    assert errs[0].path == "models.ustc_api_key"  # alias resolved to field name
    assert errs[0].input == "***"


# --- #11: secret temp file must be 0600 from CREATION (no 0644 race window) -----
def test_defect_11_secret_tmp_file_created_0600_no_widening_window(
    tmp_path, monkeypatch
):
    sec = tmp_path / "secrets.json"
    be = FileBackend(tmp_path / "config.json", sec)

    # The race is the window between a 0644 create (write_text default) and a later
    # chmod 0600. Catch it at the source: the secret temp file must be CREATED with
    # mode 0600, and the physical file must be 0600 from the moment it exists.
    creations: list[tuple[str, int]] = []
    real_open = os.open

    def spy_open(path, flags, mode=0o777, *a, **kw):
        fd = real_open(path, flags, mode, *a, **kw)
        if (flags & os.O_CREAT) and str(path).endswith(".tmp"):
            creations.append((str(path), _mode(path)))
        return fd

    # Also fail loudly if the buggy code path (pathlib write_text -> 0644) is used:
    # under the fix the tmp file is created via os.open at 0600, so write_text is
    # never called on the secret temp.
    from pathlib import Path as _P

    real_write_text = _P.write_text

    def guard_write_text(self, *a, **kw):
        if str(self).endswith(".tmp"):
            raise AssertionError(
                "secret temp written via write_text (0644 race), not os.open 0600"
            )
        return real_write_text(self, *a, **kw)

    monkeypatch.setattr(os, "open", spy_open)
    monkeypatch.setattr(_P, "write_text", guard_write_text)
    be.save_secrets({"models": {"ustc_api_key": "sk-1"}})

    assert creations, "secret temp file was not created via os.open"
    assert all(m == 0o600 for _, m in creations)
    assert _mode(sec) == 0o600


# --- #13: fsync before rename ---------------------------------------------------
def test_defect_13_fsync_before_replace(tmp_path, monkeypatch):
    cfg = tmp_path / "config.json"
    be = FileBackend(cfg, tmp_path / "secrets.json")
    calls = []
    real_fsync = os.fsync
    real_replace = os.replace

    monkeypatch.setattr(
        os, "fsync", lambda fd: (calls.append(("fsync", fd)), real_fsync(fd))[1]
    )
    monkeypatch.setattr(
        os,
        "replace",
        lambda s, d: (calls.append(("replace", s)), real_replace(s, d))[1],
    )
    be.save_config({"models": {"default_model": "ds"}})
    kinds = [c[0] for c in calls]
    assert "fsync" in kinds
    # at least one fsync happened before the first replace
    assert kinds.index("fsync") < kinds.index("replace")
    # and the data round-trips
    assert be.load_config() == {"models": {"default_model": "ds"}}


# --- #3: FileHistory eviction preserves insertion order for equal ts -----------
def test_defect_3_equal_ts_prune_drops_oldest_added_not_lexical(tmp_path):
    hist = FileHistory(tmp_path / "hist")
    hist.add(Snapshot(id="zzz", ts="2026-07-02T00:00:00Z"))
    hist.add(Snapshot(id="aaa", ts="2026-07-02T00:00:00Z"))  # same ts, added later
    dropped = hist.prune(1)
    remaining = [s.id for s in hist.list()]
    assert dropped == ["zzz"]  # the OLDEST-added dropped, not the lexical-later
    assert remaining == ["aaa"]  # the newest-added survives


# --- #8: redact_diff fails CLOSED on metadata lookup failure -------------------
def test_defect_8_redact_diff_fails_closed_on_unknown_path():
    reg = default_registry()
    raw = {"ghost.field": {"before": "raw-secret-A", "after": "raw-secret-B"}}
    red = redact_diff(reg, raw, salt="fixed-test-salt")
    assert "raw-secret-A" not in str(red)
    assert "raw-secret-B" not in str(red)
    assert red["ghost.field"]["before"].startswith("hmac:")


# --- #9: hash_secret is keyed/salted, not a bare sha256 prefix -----------------
def test_defect_9_hash_is_salted_and_not_bare_sha256():
    # same value + same salt -> equal (change detection within a deployment)
    assert hash_secret("v", salt="s1") == hash_secret("v", salt="s1")
    # different salts -> different digests (not enumerable across deployments)
    assert hash_secret("v", salt="s1") != hash_secret("v", salt="s2")
    # salted digest is keyed (hmac:), not a bare sha256 prefix
    assert hash_secret("v", salt="s1").startswith("hmac:")


def test_defect_9_store_uses_its_salt_and_generates_when_absent():
    s1 = _store()
    assert s1.secret_salt == "fixed-test-salt"  # injectable for tests
    # a store with no injected salt generates one (generate-once, non-empty)
    gen = ConfigStore(default_registry(), MemoryBackend(), InMemoryHistory(), env={})
    assert gen.secret_salt and gen.secret_salt == gen.secret_salt


# --- #4: max_history is clamped so the returned snapshot_id is rollback-able ----
@pytest.mark.parametrize("max_history", [0, 1])
def test_defect_4_returned_snapshot_is_immediately_rollbackable(max_history):
    store = ConfigStore(
        default_registry(),
        MemoryBackend(),
        InMemoryHistory(),
        env={},
        max_history=max_history,
        secret_salt="fixed-test-salt",
    )
    res = store.apply({"models.default_model": "ds"}, ts="t", snapshot_id="s1")
    # the snapshot apply returned must still exist for rollback
    rb = store.rollback(res.snapshot_id, ts="t2", new_snapshot_id="s2")
    assert rb.snapshot_id == "s2"
    assert store.get("models.default_model") == "claude"  # rolled back to pre-state


# --- #5: duplicate snapshot_id in apply() emits an error audit entry ------------
def test_defect_5_duplicate_snapshot_id_emits_error_audit():
    entries = []
    store = ConfigStore(
        default_registry(),
        MemoryBackend(),
        InMemoryHistory(),
        audit_hook=entries.append,
        env={},
        secret_salt="fixed-test-salt",
    )
    store.apply({"models.default_model": "a"}, ts="t1", snapshot_id="dup")
    n_before = len(entries)
    with pytest.raises(ValueError):
        store.apply({"models.default_model": "b"}, ts="t2", snapshot_id="dup")
    # the second apply raised AND recorded an error audit line
    assert len(entries) == n_before + 1
    assert entries[-1].outcome == OUTCOME_ERROR
    assert entries[-1].snapshot_id is None


def test_empty_env_value_does_not_shadow_profile_layer():
    """Regression: an empty/whitespace env value must be treated as UNSET.

    Deployment bug (studentunion cutover): compose injects ``${QQ_X:-}`` empties
    for keys the docker.env no longer sets; those empties must NOT shadow the git
    profile layer (they would fall the field back to its schema default). env only
    overrides when it carries a real value.
    """
    from rtime_admin_core import ConfigStore, InMemoryHistory, MemoryBackend
    from rtime_admin_core.registry import default_registry

    reg = default_registry()
    profile = {"models.default_model": "ds"}
    # empty env → profile wins
    s_empty = ConfigStore(
        reg, MemoryBackend(), InMemoryHistory(),
        env={"DEFAULT_MODEL": ""}, profile_layer=profile,
    )
    assert s_empty.get("models.default_model") == "ds"
    # whitespace-only env → profile wins
    s_ws = ConfigStore(
        reg, MemoryBackend(), InMemoryHistory(),
        env={"DEFAULT_MODEL": "   "}, profile_layer=profile,
    )
    assert s_ws.get("models.default_model") == "ds"
    # real env value → env still wins
    s_set = ConfigStore(
        reg, MemoryBackend(), InMemoryHistory(),
        env={"DEFAULT_MODEL": "kimi"}, profile_layer=profile,
    )
    assert s_set.get("models.default_model") == "kimi"
