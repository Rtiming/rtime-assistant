# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""deploy/setup-wizard.py 的沙箱化测试(不碰 docker;tmp 实例目录+真实 manifest)。"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
WIZARD = REPO / "deploy" / "setup-wizard.py"


def run(*args: str, expect: int = 0) -> subprocess.CompletedProcess:
    proc = subprocess.run(
        [sys.executable, str(WIZARD), *args], capture_output=True, text=True
    )
    assert proc.returncode == expect, f"exit={proc.returncode}\n{proc.stdout}\n{proc.stderr}"
    return proc


def test_list_shows_real_manifest_modules():
    proc = run("list", "--json")
    ids = {m["id"] for m in json.loads(proc.stdout)["modules"]}
    assert {"channel-qq", "channel-web", "integration-sync", "integration-wechat-mp"} <= ids


def test_plan_resolves_deps_and_profiles(tmp_path):
    proc = run("plan", "--instance", str(tmp_path / "inst"), "--modules", "channel-qq", "--json")
    out = json.loads(proc.stdout)
    # depends_on 闭包:channel-qq 拉进 core-config/gateway-core;core 恒含
    assert "channel-qq" in out["modules"] and "core-config" in out["modules"]
    assert out["compose_profiles"] == ["qq"]
    assert out["profile_issues"] == []


def test_init_creates_instance_and_lock(tmp_path):
    inst = tmp_path / "inst"
    proc = run("init", "--instance", str(inst), "--modules", "channel-qq,channel-web", "--json")
    out = json.loads(proc.stdout)
    assert out["compose_profiles"] == ["qq", "web"]
    env = (inst / ".env").read_text(encoding="utf-8")
    assert "COMPOSE_PROFILES=qq,web" in env and f"UPDATE_INSTANCE_NAME={inst.name}" in env
    assert "[channel-qq]" in env  # setup_notes 注入
    assert (inst / "compose.override.yml").is_file()
    lock = json.loads((inst / "state" / "install.lock").read_text(encoding="utf-8"))
    assert lock["compose_profiles"] == ["qq", "web"] and "channel-qq" in lock["modules"]


def test_install_lock_blocks_reinit(tmp_path):
    inst = tmp_path / "inst"
    run("init", "--instance", str(inst), "--modules", "")
    proc = run("init", "--instance", str(inst), "--modules", "channel-qq", expect=1)
    assert "已初始化" in proc.stderr
    # --force 放行且不覆盖用户改过的 override
    (inst / "compose.override.yml").write_text("services: {custom: {}}\n", encoding="utf-8")
    run("init", "--instance", str(inst), "--modules", "channel-qq", "--force")
    assert "custom" in (inst / "compose.override.yml").read_text(encoding="utf-8")


def test_unknown_module_and_noninteractive_guard(tmp_path):
    proc = run("plan", "--instance", str(tmp_path), "--modules", "ghost", expect=2)
    assert "未知 module id" in proc.stderr
    # 非 TTY 且没给 --modules -> 拒绝(不挂死等输入)
    proc = subprocess.run(
        [sys.executable, str(WIZARD), "init", "--instance", str(tmp_path / "x")],
        capture_output=True, text=True, stdin=subprocess.DEVNULL,
    )
    assert proc.returncode != 0 and "非交互" in proc.stderr


def test_core_only_init(tmp_path):
    inst = tmp_path / "core-only"
    proc = run("init", "--instance", str(inst), "--modules", "", "--json")
    out = json.loads(proc.stdout)
    assert out["compose_profiles"] == []  # 只有base服务
    assert all(m in out["modules"] for m in ("core-config", "core-chat-runtime", "gateway-core"))
