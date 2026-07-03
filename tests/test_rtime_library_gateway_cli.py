# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_library_gateway.cli")


def test_doctor_reports_ok_and_resolved_roots(tmp_path, capfd, monkeypatch):
    cli = _load_cli()
    monkeypatch.setenv("RTIME_ASSISTANT_ROOT", str(ROOT))
    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))

    assert cli.main(["doctor"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["server"] == "rtime-library-gateway"
    assert data["roots"]["repo_root"] == str(ROOT)
    assert data["policy"]["loaded"] is True
    assert data["dispatch"]["tables_disjoint"] is True
    # doctor never reads the brain
    assert data["privacy"]["brain_read"] is False
    # the narrow write tools are present
    assert set(data["dispatch"]["write_executables"]) == {
        "rtime-context-source",
        "rtime-memory-candidate",
        "rtime-reminder-register",
        "rtime-contribute",
        "rtime-finalize",
        "rtime-course-intake",
        "rtime-jobs-submit",
    }


def test_policy_show_outputs_loaded_policy(capfd):
    cli = _load_cli()

    assert cli.main(["policy-show"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    policy = data["policy"]
    assert policy["schema_version"] == 1
    # Single-owner deployment: personal-data is intentionally NOT gated (owner reads
    # the full library through the gateway from every device). An explicit empty list
    # means "exclude nothing"; the gate mechanism stays covered by the gate unit tests.
    assert policy["excluded_top_dirs"] == []
    assert "lib.search" in policy["methods"]
    assert policy["methods"]["lib.settings.reminder_cancel"]["tier"] == "write"


def test_policy_show_prefers_env_policy(tmp_path, capfd, monkeypatch):
    cli = _load_cli()
    custom = tmp_path / "policy.json"
    custom.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "default_read": "allow",
                "default_write": "deny",
                "methods": {"lib.search": {"tier": "read", "enabled": True}},
                "clients": {"default": {"allow": ["*"]}},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_LIBRARY_GATEWAY_POLICY", str(custom))

    assert cli.main(["policy-show"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["policy"]["default_write"] == "deny"


def test_call_routes_through_gate_and_dispatch(tmp_path, capfd, monkeypatch):
    cli = _load_cli()
    import rtime_library_gateway.dispatch as dispatch

    monkeypatch.setenv("BRAIN_ROOT", str(tmp_path / "brain"))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    canned = {"ok": True, "record_count": 3}

    def fake_run_cli(target, *, timeout=60):
        return canned, 0, json.dumps(canned)

    monkeypatch.setattr(dispatch, "run_cli", fake_run_cli)

    assert cli.main(["call", "lib.automation", "--args-json", '{"op": "doctor"}']) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    assert data["record_count"] == 3


def test_call_denied_path_returns_clean_json_error(tmp_path, capfd, monkeypatch):
    cli = _load_cli()
    brain = tmp_path / "brain"
    (brain / "knowledge").mkdir(parents=True)
    monkeypatch.setenv("BRAIN_ROOT", str(brain))
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))

    # A path that ESCAPES the brain root is always denied by the gate, independent of
    # the (now open) single-owner personal-data policy; the CLI must surface a clean
    # JSON error (not an unhandled traceback) and exit non-zero.
    rc = cli.main(
        ["call", "lib.docpack", "--args-json", '{"op": "validate", "path": "../outside.md"}']
    )
    data = json.loads(capfd.readouterr().out)
    assert rc == 1
    assert data["ok"] is False
    assert "not allowed" in data["error"]
