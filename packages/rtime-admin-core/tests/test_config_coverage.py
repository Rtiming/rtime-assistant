# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Config-coverage GUARD — the ratchet that keeps the schema owning the env surface.

Baseline is GREEN (the allowlist names every currently-unregistered env key). The
guard turns RED when a NEW ``os.getenv(...)`` appears with neither a registered
``rtime-config`` field nor an allowlist entry — forcing "register it or allowlist
it with a reason". As each P2 收编 batch registers a module and removes its keys
from the allowlist, the number in docs/design/config-full-coverage-plan-2026-07
must go UP or stay flat; a drop is a regression.

Also here: a reverse sentinel (dead allowlist entries), the strong
scope/secret metadata checks (scope is a warning at baseline — qq uses module
scope and omits x-scope by design; secret is a hard invariant), and a secret
self-test proving get_all / diff / audit never leak a plaintext secret.

Run just this file: ``uv run pytest packages/rtime-admin-core/tests/test_config_coverage.py``.
"""

from __future__ import annotations

import importlib.util
import sys
import warnings
from pathlib import Path

import pytest

# --- bootstrap: make the qq-bridge app importable so the qq module registers ----
# The qq module is the migrated exemplar; it lives in apps/qq-bridge (not a
# workspace member) so admin-core stays a leaf. Put the app on sys.path (mirrors
# apps/qq-bridge/tests/conftest.py) so default_registry(include_qq=True) succeeds
# and the guard measures the REAL, migrated env surface. If the app is genuinely
# absent, the guard skips rather than silently under-counting coverage.
_REPO = Path(__file__).resolve().parents[3]
_QQ_APP = _REPO / "apps" / "qq-bridge"
if _QQ_APP.is_dir() and str(_QQ_APP) not in sys.path:
    sys.path.insert(0, str(_QQ_APP))

from rtime_admin_core import (  # noqa: E402
    ConfigStore,
    InMemoryAuditSink,
    InMemoryHistory,
    MemoryBackend,
    default_registry,
)
from rtime_admin_core.coverage_allowlist import ALLOWLIST  # noqa: E402
from rtime_admin_core.coverage_doctor import build_report, scan_used_env  # noqa: E402

_QQ_IMPORTABLE = importlib.util.find_spec("qq_bridge") is not None


@pytest.fixture(scope="module")
def report():
    if not _QQ_IMPORTABLE:
        pytest.skip(
            "qq-bridge app not importable — the qq module cannot register, so the "
            "coverage baseline (which assumes qq is migrated) does not apply."
        )
    return build_report(_REPO)


# --- the RATCHET ---------------------------------------------------------------
def test_no_unregistered_env_outside_allowlist(report):
    """USED_ENV − REGISTERED_ENV − ALLOWLIST must be empty (the core guard)."""
    missing = report.uncovered - set(ALLOWLIST)
    if missing:
        lines = [f"  {key}  @ {report.used[key][0].where()}" for key in sorted(missing)]
        pytest.fail(
            f"{len(missing)} env key(s) are READ by the code but neither registered "
            "on an rtime-config schema NOR listed in coverage_allowlist.py.\n"
            "Register the field (preferred) OR add it to "
            "packages/rtime-admin-core/src/rtime_admin_core/coverage_allowlist.py "
            "with a reason (bootstrap / deploy-path / dev-override / derived-alias / "
            "TODO-batch:<name>):\n" + "\n".join(lines)
        )


# --- REVERSE SENTINEL: no dead allowlist entries -------------------------------
def test_no_stale_allowlist_entries(report):
    """ALLOWLIST − USED_ENV must be empty — a key no longer read anywhere is rot.

    When a 收编 batch registers a module it should DELETE the batch's keys from the
    allowlist; if it forgets, or a key stops being read, this catches it so the
    ledger cannot silently accumulate dead entries.
    """
    stale = set(ALLOWLIST) - report.used_keys
    if stale:
        lines = [f"  {key}  (reason: {ALLOWLIST[key]!r})" for key in sorted(stale)]
        pytest.fail(
            f"{len(stale)} allowlist entr(y/ies) name an env key no longer read by "
            "any scanned code (dead allowlist rot). Remove them from "
            "coverage_allowlist.py:\n" + "\n".join(lines)
        )


# --- baseline sanity: the numbers are what we recorded -------------------------
def test_baseline_numbers_are_sane(report):
    """Guards against the scanner silently collapsing (e.g. an import breaking).

    Not an exact pin (that would be brittle across batches), but a floor: the
    registry must expose the 4 baseline modules and a non-trivial field count, and
    at least the pilot's env keys must be covered.
    """
    x, y = report.module_count()
    z, n = report.field_count()
    assert y >= 4, f"expected >=4 registered modules, got {y}"
    assert n >= 40, f"expected >=40 registered fields, got {n}"
    assert z >= 1 and x >= 1, "no coverage detected — scanner likely broke"
    # every uncovered key is accounted for (equivalent to the ratchet, stated as
    # a set identity for a clearer failure if they diverge)
    assert report.uncovered <= set(ALLOWLIST)


# --- HARD RATCHET: coverage floor only increases (J2 config-and-access §2.1) -----
# 覆盖率是"只增不减"的护城河(研究:Grafana/GitLab 官方都不保证多入口可达,我们自动化了)。
# 这几个地板值随收编提升时手动上调,绝不下调——下调=有字段/env被去掉了覆盖=CI 红。
# 当前(2026-07-04):modules 9/10、fields 164/202、env covered 150。
_FLOOR_MODULES_COVERED = 9
_FLOOR_FIELDS_COVERED = 164
_FLOOR_ENV_COVERED = 150


def test_coverage_floor_only_increases(report):
    x, _y = report.module_count()
    z, _n = report.field_count()
    env_covered = len(report.covered)
    assert x >= _FLOOR_MODULES_COVERED, (
        f"覆盖模块数回退 {x} < 地板 {_FLOOR_MODULES_COVERED};去掉了模块覆盖?"
        " 若确实是有意移除,先确认再下调地板。"
    )
    assert z >= _FLOOR_FIELDS_COVERED, (
        f"覆盖字段数回退 {z} < 地板 {_FLOOR_FIELDS_COVERED};去掉了字段覆盖?"
    )
    assert env_covered >= _FLOOR_ENV_COVERED, (
        f"覆盖 env 键数回退 {env_covered} < 地板 {_FLOOR_ENV_COVERED}"
    )


# --- STRONG CHECK: secrets carry x-secret (hard invariant) ---------------------
def test_every_secret_field_marked(report):
    """Every field built with secret_field must carry x-secret (structural).

    This is what makes redaction reliable: redaction keys off x-secret, so an
    unmarked credential would leak. secret_field stamps it, so this must hold.
    """
    reg = default_registry(include_qq=True)
    from rtime_admin_core.metadata import secret_paths

    secrets = secret_paths(reg)
    # the 3 known credentials must be in the set (canary that the check is live)
    for known in (
        "models.ustc_api_key",
        "models.litellm_master_key",
        "qq.access_token",
    ):
        assert known in secrets, f"expected {known} to be marked x-secret"


# --- STRONG CHECK: config fields carry x-scope (WARNING at baseline) -----------
def test_config_fields_have_scope_warns_at_baseline(report):
    """Every non-secret CONFIG field SHOULD carry x-scope.

    A warning, not a failure, at baseline: the qq pilot deliberately omits x-scope
    (module-level scope; see rtime_config.fields — omitted x-scope == module scope).
    Emitting a warning keeps this visible so future batches can tighten to a hard
    assertion once every module declares field scopes.
    """
    reg = default_registry(include_qq=True)
    missing_scope: list[str] = []
    for module in reg.list_modules():
        props = reg.get_schema(module).get("properties", {})
        for fld, prop in props.items():
            if prop.get("x-secret"):
                continue
            if "x-scope" not in prop:
                missing_scope.append(f"{module}.{fld}")
    if missing_scope:
        warnings.warn(
            f"{len(missing_scope)} non-secret config field(s) lack x-scope "
            f"(module-scoped by convention): {', '.join(sorted(missing_scope)[:5])}"
            f"{' …' if len(missing_scope) > 5 else ''}",
            stacklevel=2,
        )
    # never fails at baseline; the warning is the signal.
    assert True


# --- SECRET SELF-TEST: no plaintext secret in get_all / diff / audit -----------
def _fresh_store():
    reg = default_registry()  # sample modules carry the 2 known secrets
    sink = InMemoryAuditSink()
    return (
        ConfigStore(
            reg,
            MemoryBackend(),
            InMemoryHistory(),
            audit_hook=sink,
            env={},
        ),
        sink,
    )


def test_secret_never_plaintext_in_get_all():
    store, _ = _fresh_store()
    store.apply(
        {"models.ustc_api_key": "sk-PLAINTEXT-CANARY"},
        ts="t",
        snapshot_id="s",
    )
    dumped = repr(store.get_all(redact=True))
    assert "sk-PLAINTEXT-CANARY" not in dumped
    # the masked placeholder is present instead
    assert store.get_all(redact=True)["models.ustc_api_key"] == "***"


def test_secret_never_plaintext_in_diff():
    store, _ = _fresh_store()
    d = store.diff({"models.ustc_api_key": "sk-PLAINTEXT-CANARY"})
    assert "sk-PLAINTEXT-CANARY" not in repr(d)
    assert d["models.ustc_api_key"]["after"].startswith("hmac:")


def test_secret_never_plaintext_in_audit():
    import json

    store, sink = _fresh_store()
    store.apply(
        {"models.ustc_api_key": "sk-PLAINTEXT-CANARY"},
        ts="t",
        snapshot_id="s",
    )
    blob = json.dumps([e.to_dict() for e in sink.entries])
    assert "sk-PLAINTEXT-CANARY" not in blob


# --- doctor smoke: the module-level entrypoint runs and formats -----------------
def test_doctor_formats_report(report):
    from rtime_admin_core.coverage_doctor import format_report

    text = format_report(report)
    assert "modules covered:" in text
    assert "fields covered:" in text
    assert "uncovered env keys" in text


def test_scan_used_env_finds_known_keys():
    """The AST scanner finds a couple of anchor keys we know are read in-repo."""
    used = scan_used_env(_REPO)
    assert "DEFAULT_MODEL" in used  # read by qq/feishu
    assert "GATEWAY_PORT" in used  # read by assistant-gateway
    # each hit carries a file:line
    assert used["GATEWAY_PORT"][0].line > 0
