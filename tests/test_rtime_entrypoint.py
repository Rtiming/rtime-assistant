# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for the scripts/rtime unified entrypoint thin router.

The router must only *dispatch* verbs to existing commands — these tests assert
the resolved command for each verb (pure ``resolve()``, no execution) plus a few
live builtins, so behavior changes in the routing table fail fast.
"""
from __future__ import annotations

import importlib.util
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "rtime"


def load_router():
    loader = SourceFileLoader("rtime_router", str(SCRIPT))
    spec = importlib.util.spec_from_loader("rtime_router", loader)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def rt():
    return load_router()


# --------------------------------------------------------------------------- #
# dispatch (pure resolve, no execution)
# --------------------------------------------------------------------------- #
def test_help_and_no_args_are_builtins(rt):
    for argv in ([], ["--help"], ["-h"], ["help"]):
        plan = rt.resolve(argv)
        assert plan.kind == "builtin"


def test_unknown_verb_is_error(rt):
    plan = rt.resolve(["frobnicate"])
    assert plan.kind == "error"


def test_runtime_forwards_to_runtime_module(rt):
    plan = rt.resolve(["runtime", "doctor"])
    assert plan.kind == "exec"
    cmd = plan.cmds[0]
    assert cmd.argv[1:] == ["-m", "rtime_assistant_runtime", "doctor"]
    # naming-drift: package dir != import name; PYTHONPATH must point at the right src
    assert cmd.env is not None
    assert cmd.env["PYTHONPATH"].endswith("packages/rtime-assistant-runtime/src")


def test_module_bare_id_expands_to_module_flag(rt):
    plan = rt.resolve(["module", "brain-docpack", "--dry-run"])
    assert plan.kind == "exec"
    argv = plan.cmds[0].argv
    assert argv[1].endswith("scripts/module-submit-check.py")
    assert argv[2:] == ["--module", "brain-docpack", "--dry-run"]


def test_module_two_ids_each_get_module_flag(rt):
    plan = rt.resolve(["module", "brain-docpack", "brain-library"])
    assert plan.cmds[0].argv[2:] == ["--module", "brain-docpack", "--module", "brain-library"]


def test_module_leading_flag_passes_through(rt):
    plan = rt.resolve(["module", "--changed", "--dry-run"])
    assert plan.cmds[0].argv[2:] == ["--changed", "--dry-run"]


def test_module_no_args_lists(rt):
    plan = rt.resolve(["module"])
    assert plan.cmds[0].argv[2:] == ["--list"]


def test_dev_runs_uv_sync(rt):
    plan = rt.resolve(["dev"])
    assert plan.cmds[0].argv == ["uv", "sync", "--all-packages"]


def test_doctor_all_is_multi_over_every_target(rt):
    plan = rt.resolve(["doctor"])
    assert plan.kind == "multi"
    assert len(plan.cmds) == len(rt.DOCTOR_TARGETS)
    assert all(cmd.argv[-1] == "doctor" for cmd in plan.cmds)


def test_doctor_single_target(rt):
    plan = rt.resolve(["doctor", "library", "--json"])
    assert plan.kind == "exec"
    cmd = plan.cmds[0]
    assert cmd.argv[1:] == ["-m", "brain_library", "doctor", "--json"]
    assert cmd.env["PYTHONPATH"].endswith("packages/brain-library/src")


def test_doctor_unknown_target_is_error(rt):
    assert rt.resolve(["doctor", "nope"]).kind == "error"


def test_doctor_list_is_builtin(rt):
    assert rt.resolve(["doctor", "--list"]).kind == "builtin"


@pytest.mark.parametrize("action", ["start", "stop", "restart", "status"])
def test_gateway_systemd_actions(rt, action):
    plan = rt.resolve(["gateway", action])
    assert plan.cmds[0].argv == ["systemctl", "--user", action, rt.GATEWAY_UNIT]


def test_gateway_logs_uses_journalctl(rt):
    plan = rt.resolve(["gateway", "logs"])
    assert plan.cmds[0].argv[:3] == ["journalctl", "--user", "-u"]


def test_gateway_health_runs_live_audit(rt):
    plan = rt.resolve(["gateway", "health"])
    assert plan.cmds[0].argv[-1].endswith("scripts/gateway-live-audit.sh")


def test_gateway_verify_runs_deploy_verify(rt):
    plan = rt.resolve(["gateway", "verify"])
    assert plan.cmds[0].argv[-1].endswith("scripts/gateway-deploy-verify.sh")


def test_gateway_bad_action_is_error(rt):
    assert rt.resolve(["gateway", "explode"]).kind == "error"


def test_deploy_default_is_prod_check_dry_run(rt):
    plan = rt.resolve(["deploy"])
    argv = plan.cmds[0].argv
    assert argv[1].endswith("scripts/docker-prod-check.sh")
    assert argv[2:] == ["--dry-run"]


def test_deploy_host_runs_orangepi_script(rt):
    plan = rt.resolve(["deploy", "host"])
    assert plan.cmds[0].argv[-1].endswith("scripts/deploy-on-orangepi.sh")


def test_deploy_forwards_args(rt):
    plan = rt.resolve(["deploy", "--config", "--env-file", "x.env"])
    assert plan.cmds[0].argv[2:] == ["--config", "--env-file", "x.env"]


def test_mcp_list_is_builtin(rt):
    assert rt.resolve(["mcp", "list"]).kind == "builtin"


def test_mcp_bad_action_is_error(rt):
    assert rt.resolve(["mcp"]).kind == "error"


def test_check_default_is_multi_of_both_gates(rt):
    plan = rt.resolve(["check"])
    assert plan.kind == "multi"
    tails = [c.argv[-1] for c in plan.cmds]
    assert any(t.endswith("scripts/check-entrypoint-drift.py") for t in tails)
    assert any(t.endswith("tools/rtime-project-check.py") for t in tails)


def test_check_drift_only(rt):
    plan = rt.resolve(["check", "drift"])
    assert plan.kind == "exec"
    assert plan.cmds[0].argv[-1].endswith("scripts/check-entrypoint-drift.py")


def test_doctor_targets_match_pyproject_scripts(rt):
    """Every doctor target's import name must be a real package with that module dir."""
    for module, pkg_dir in rt.DOCTOR_TARGETS.values():
        src = ROOT / "packages" / pkg_dir / "src" / module
        assert src.is_dir(), f"{module} missing under packages/{pkg_dir}/src"


# --------------------------------------------------------------------------- #
# live behavior (subprocess against the real script)
# --------------------------------------------------------------------------- #
def run(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        cwd=str(ROOT),
    )


def test_cli_help_exit_zero_lists_verbs():
    res = run("--help")
    assert res.returncode == 0
    for verb in ("doctor", "dev", "module", "runtime", "gateway", "deploy", "mcp", "check"):
        assert verb in res.stdout


def test_cli_unknown_verb_exit_two():
    res = run("nope")
    assert res.returncode == 2


def test_cli_print_runtime_doctor():
    res = run("--print", "runtime", "doctor")
    assert res.returncode == 0
    assert "-m rtime_assistant_runtime doctor" in res.stdout
    assert "PYTHONPATH=" in res.stdout


def test_cli_mcp_list_includes_known_servers():
    res = run("mcp", "list")
    assert res.returncode == 0
    assert "rtime-library-gateway" in res.stdout
    assert "brain-docpack" in res.stdout


def test_cli_mcp_list_json_is_parseable():
    import json

    res = run("mcp", "list", "--json")
    assert res.returncode == 0
    rows = json.loads(res.stdout)
    assert any(r["server"] == "brain-library" for r in rows)
    assert all(r["exists"] for r in rows if r["server"])
