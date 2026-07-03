# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""End-to-end regression tests for the bare-repo post-receive CI hook.

These guard the two properties the hook MUST keep (see docs/ci-server-gate.zh-CN.md):
  * advisory / non-blocking — it never rejects a push, and the heavy pytest gate is
    fully detached so `git push` returns promptly instead of hanging on it;
  * feedback to the pusher — the synchronous portability gate (and which modules
    changed) is echoed back over the push connection, with actionable detail.

Each test stands up a throwaway bare repo, installs the real hook, and pushes into
it — exactly how orangepi runs it. Skipped where git/bash are unavailable.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
HOOK = ROOT / "deploy" / "git-hooks" / "post-receive"
CHECKER = ROOT / "tools" / "rtime-project-check.py"
MANIFEST = ROOT / "module-submit.json"

pytestmark = pytest.mark.skipif(
    not (shutil.which("git") and shutil.which("bash")),
    reason="post-receive hook tests need git and bash on PATH",
)


def _run(cmd: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        cwd=str(cwd),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
    )


def _git(args: list[str], cwd: Path, timeout: int = 60) -> subprocess.CompletedProcess:
    return _run(["git", *args], cwd, timeout=timeout)


def _setup(tmp_path: Path, ci_env: str = "") -> tuple[Path, Path]:
    """Create a bare repo with the hook installed, plus a working clone seeded with
    the real portability checker + module manifest so the hook's gates run for real."""
    bare = tmp_path / "bare.git"
    _git(["init", "-q", "--bare", str(bare)], tmp_path)
    shutil.copy(HOOK, bare / "hooks" / "post-receive")
    os.chmod(bare / "hooks" / "post-receive", 0o755)
    if ci_env:
        (bare / "hooks" / "ci.env").write_text(ci_env, encoding="utf-8")

    work = tmp_path / "work"
    _git(["clone", "-q", str(bare), str(work)], tmp_path)
    _git(["config", "user.email", "t@example.com"], work)
    _git(["config", "user.name", "tester"], work)
    _git(["checkout", "-q", "-b", "main"], work)

    (work / "tools").mkdir()
    shutil.copy(CHECKER, work / "tools" / "rtime-project-check.py")
    shutil.copy(MANIFEST, work / "module-submit.json")
    gw = work / "apps" / "assistant-gateway"
    gw.mkdir(parents=True)
    (gw / "gateway.py").write_text("print('hi')\n", encoding="utf-8")
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "init"], work)
    return bare, work


def test_clean_push_reports_portability_and_modules(tmp_path: Path) -> None:
    _bare, work = _setup(tmp_path)
    r = _git(["push", "-q", "-u", "origin", "main"], work)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "可移植性通过" in out, out
    assert "改动模块" in out and "assistant-gateway" in out, out


def test_portability_failure_is_advisory_with_detail(tmp_path: Path) -> None:
    _bare, work = _setup(tmp_path)
    _git(["push", "-q", "-u", "origin", "main"], work)  # baseline so next diff is small
    # Inject a hardcoded personal-home path. Build the token piecewise so THIS test
    # file stays clean under tools/rtime-project-check.py (no literal /Users/<name>).
    home = "/Users/" + "someone"
    (work / "apps" / "assistant-gateway" / "badpath.py").write_text(
        'BAD = "' + home + '/secret.py"\n', encoding="utf-8"
    )
    _git(["add", "-A"], work)
    _git(["commit", "-qm", "add bad abs path"], work)
    r = _git(["push", "-q", "origin", "main"], work)
    out = r.stdout + r.stderr
    assert r.returncode == 0, "advisory hook must never reject the push:\n" + out
    assert "可移植性发现问题" in out, out
    # the indented detail line (grep -A1) must survive, not just the header
    assert "硬编码个人主目录" in out, out


def test_feature_branch_skips_pytest(tmp_path: Path) -> None:
    _bare, work = _setup(tmp_path)
    _git(["push", "-q", "-u", "origin", "main"], work)
    _git(["checkout", "-q", "-b", "feat/x"], work)
    (work / "apps" / "assistant-gateway" / "gateway.py").write_text(
        "print('hi2')\n", encoding="utf-8"
    )
    _git(["commit", "-qam", "tweak"], work)
    r = _git(["push", "-q", "-u", "origin", "feat/x"], work)
    out = r.stdout + r.stderr
    assert r.returncode == 0, out
    assert "可移植性通过" in out, out          # fast gate still runs on every branch
    assert "不跑 pytest" in out, out            # but the heavy gate is main-only


def test_pytest_gate_is_detached_nonblocking(tmp_path: Path) -> None:
    """The heavy gate must not hold the push connection open. We point it at a fake
    'pytest' that blocks on a sentinel we control: a non-detached hook would hang the
    push until the sentinel is released; a detached one returns immediately."""
    marker = tmp_path / "marker"
    release = tmp_path / "release"
    fakepy = tmp_path / "fakepy"
    fakepy.write_text(
        "#!/bin/bash\n"
        f'echo START >> "{marker}"\n'
        f'for _ in $(seq 1 600); do [ -f "{release}" ] && break; sleep 0.1; done\n'
        f'echo DONE >> "{marker}"\n'
        "exit 0\n",
        encoding="utf-8",
    )
    os.chmod(fakepy, 0o755)
    ci_env = f'RTIME_CI_VENV_PY="{fakepy}"\nRTIME_CI_LOG="{tmp_path}/ci.log"\n'
    _bare, work = _setup(tmp_path, ci_env=ci_env)

    try:
        try:
            r = _git(["push", "-q", "-u", "origin", "main"], work, timeout=30)
        except subprocess.TimeoutExpired:
            release.write_text("go", encoding="utf-8")
            pytest.fail("git push blocked on the background gate — hook is not detached")
        out = r.stdout + r.stderr
        assert r.returncode == 0, out
        assert "pytest 后台运行" in out, out

        # background job should have started but still be blocked on the sentinel
        deadline = time.time() + 5
        while time.time() < deadline and not marker.exists():
            time.sleep(0.05)
        assert marker.exists() and "START" in marker.read_text(), "bg gate did not start"
        assert "DONE" not in marker.read_text(), "push waited for the gate (not detached)"
    finally:
        release.write_text("go", encoding="utf-8")  # always release the bg job

    deadline = time.time() + 10
    while time.time() < deadline:
        if marker.exists() and "DONE" in marker.read_text():
            break
        time.sleep(0.1)
    assert "DONE" in marker.read_text(), "bg gate never completed after release"
