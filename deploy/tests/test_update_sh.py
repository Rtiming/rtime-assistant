# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# -*- coding: utf-8 -*-
"""deploy/update.sh 的沙箱化测试。

不碰真实 docker/网络:tmp 里 git init 假仓库(占位 compose.prod.yml + CHANGELOG +
迁移脚本,打 v0.1.0/v0.1.1/v0.2.0 tag,v0.2.0 节含 BREAKING:),假实例目录,
PATH 前置假 `docker` shim(把调用记进日志文件、ps 时按注入的开关吐健康/不健康 JSON)。
"""
from __future__ import annotations

import json
import os
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import pytest

UPDATE_SH = Path(__file__).resolve().parents[1] / "update.sh"

DOCKER_SHIM = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    # 假 docker:记录调用;compose ps 时按 DOCKER_SHIM_UNHEALTHY_FIRST 吐容器状态。
    set -euo pipefail
    printf 'docker %s\\n' "$*" >> "${DOCKER_SHIM_LOG:?}"
    is_ps=0
    for a in "$@"; do
      if [ "$a" = ps ]; then is_ps=1; fi
    done
    if [ "$is_ps" -eq 1 ]; then
      count_file="${DOCKER_SHIM_LOG}.pscount"
      n=0
      [ -f "$count_file" ] && n=$(cat "$count_file")
      n=$((n + 1))
      printf '%s' "$n" > "$count_file"
      if [ "$n" -le "${DOCKER_SHIM_UNHEALTHY_FIRST:-0}" ]; then
        printf '{"Name":"rtime-x-feishu-bridge-1","Service":"feishu-bridge","State":"exited","Health":""}\\n'
      else
        printf '{"Name":"rtime-x-feishu-bridge-1","Service":"feishu-bridge","State":"running","Health":"healthy"}\\n'
      fi
    fi
    exit 0
    """
)

MIGRATION_001 = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    touch "${RTIME_INSTANCE_DIR:?}/data/marker-001"
    """
)

MIGRATION_002 = textwrap.dedent(
    """\
    #!/usr/bin/env bash
    set -euo pipefail
    touch "${RTIME_INSTANCE_DIR:?}/data/marker-002"
    """
)


def changelog(*versions: str) -> str:
    """按新在前生成 Keep-a-Changelog 风格全文。versions 形如 '0.1.0'。"""
    parts = ["# Changelog\n\n## [Unreleased]\n"]
    for v in versions:
        parts.append(f"\n## [{v}]\n\n### Added\n\n- feature for {v}\n")
        if v == "0.1.1":
            parts.append("- MIGRATION: 002_add_marker.sh\n")
        if v == "0.2.0":
            parts.append("- BREAKING: config format changed, read the notes\n")
    return "".join(parts)


def git(cwd: Path, *args: str) -> str:
    out = subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", *args],
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
    )
    return out.stdout.strip()


@dataclass
class Sandbox:
    upstream: Path
    clone: Path
    instance: Path
    shim_bin: Path
    shim_log: Path

    def env(self, **extra: str) -> dict:
        env = os.environ.copy()
        env["PATH"] = f"{self.shim_bin}{os.pathsep}{env['PATH']}"
        env["DOCKER_SHIM_LOG"] = str(self.shim_log)
        env.update(extra)
        return env

    def run(self, *args: str, **extra_env: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [str(UPDATE_SH), *args, "--instance", str(self.instance), "--repo", str(self.clone)],
            capture_output=True,
            text=True,
            env=self.env(**extra_env),
        )

    def docker_calls(self) -> list[str]:
        if not self.shim_log.exists():
            return []
        return [line for line in self.shim_log.read_text().splitlines() if line.strip()]

    def publish_v020(self) -> None:
        (self.upstream / "CHANGELOG.md").write_text(changelog("0.2.0", "0.1.1", "0.1.0"))
        git(self.upstream, "add", "-A")
        git(self.upstream, "commit", "-m", "release 0.2.0")
        git(self.upstream, "tag", "-a", "v0.2.0", "-m", "v0.2.0")

    def clone_describe(self) -> str:
        return git(self.clone, "describe", "--tags", "--abbrev=0")


@pytest.fixture()
def sandbox(tmp_path: Path) -> Sandbox:
    # --- 上游(发布方)仓库: v0.1.0 -> v0.1.1(非 BREAKING, 带迁移 002) ---
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    git(upstream, "init", "-b", "main")
    (upstream / "compose.prod.yml").write_text("services:\n  feishu-bridge:\n    image: placeholder\n")
    (upstream / "CHANGELOG.md").write_text(changelog("0.1.0"))
    mig = upstream / "deploy" / "migrations"
    mig.mkdir(parents=True)
    (mig / "001_init.sh").write_text(MIGRATION_001)
    git(upstream, "add", "-A")
    git(upstream, "commit", "-m", "release 0.1.0")
    git(upstream, "tag", "-a", "v0.1.0", "-m", "v0.1.0")

    (mig / "002_add_marker.sh").write_text(MIGRATION_002)
    (upstream / "CHANGELOG.md").write_text(changelog("0.1.1", "0.1.0"))
    git(upstream, "add", "-A")
    git(upstream, "commit", "-m", "release 0.1.1")
    git(upstream, "tag", "-a", "v0.1.1", "-m", "v0.1.1")

    # --- 实例侧代码 clone,停在 v0.1.0 ---
    clone = tmp_path / "clone"
    git(tmp_path, "clone", "--quiet", str(upstream), str(clone))
    git(clone, "checkout", "--quiet", "--detach", "v0.1.0")

    # --- 假实例目录 ---
    instance = tmp_path / "instance"
    (instance / "data").mkdir(parents=True)
    (instance / "state").mkdir()
    (instance / ".env").write_text(
        "UPDATE_HEALTH_RETRIES=1\nUPDATE_HEALTH_INTERVAL=0\nSOME_APP_KEY=value\n"
    )
    (instance / "compose.override.yml").write_text("services:\n  feishu-bridge: {}\n")
    # 实例视为在 v0.1.0 上已初始化:001 已记账
    (instance / "state" / "migrations-applied").write_text("001_init.sh\n")

    # --- 假 docker shim ---
    shim_bin = tmp_path / "bin"
    shim_bin.mkdir()
    docker = shim_bin / "docker"
    docker.write_text(DOCKER_SHIM)
    docker.chmod(0o755)

    return Sandbox(
        upstream=upstream,
        clone=clone,
        instance=instance,
        shim_bin=shim_bin,
        shim_log=tmp_path / "docker-calls.log",
    )


def out_json(proc: subprocess.CompletedProcess) -> dict:
    lines = [line for line in proc.stdout.splitlines() if line.strip()]
    assert len(lines) == 1, f"stdout 应只有一行 JSON,得到: {proc.stdout!r} / stderr: {proc.stderr!r}"
    return json.loads(lines[0])


# ---------- check ----------


def test_check_update_available_exit10(sandbox: Sandbox) -> None:
    proc = sandbox.run("check")
    assert proc.returncode == 10, proc.stderr
    data = out_json(proc)
    assert data["current"] == "v0.1.0"
    assert data["latest"] == "v0.1.1"
    assert data["update_available"] is True
    assert data["breaking"] is False
    assert data["migration_pending"] is True
    assert data["pending_migrations"] == ["002_add_marker.sh"]
    assert "0.1.1" in data["changelog_excerpt"]


def test_check_breaking_exit20_after_fetch_picks_new_tag(sandbox: Sandbox) -> None:
    sandbox.publish_v020()
    proc = sandbox.run("check")
    assert proc.returncode == 20, proc.stderr
    data = out_json(proc)
    assert data["latest"] == "v0.2.0"
    assert data["breaking"] is True
    assert "BREAKING:" in data["changelog_excerpt"]


def test_check_up_to_date_exit0(sandbox: Sandbox) -> None:
    git(sandbox.clone, "checkout", "--quiet", "--detach", "v0.1.1")
    proc = sandbox.run("check")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["current"] == "v0.1.1"
    assert data["update_available"] is False
    assert data["breaking"] is False
    # check 只读:不碰 docker、不写实例状态
    assert sandbox.docker_calls() == []


# ---------- apply ----------


def test_apply_refuses_breaking_without_yes(sandbox: Sandbox) -> None:
    sandbox.publish_v020()
    proc = sandbox.run("apply")
    assert proc.returncode == 6, proc.stderr
    data = out_json(proc)
    assert data["status"] == "refused-breaking"
    assert sandbox.docker_calls() == []
    assert sandbox.clone_describe() == "v0.1.0"
    assert not (sandbox.instance / "state" / "current-version").exists()


def test_apply_success_non_breaking(sandbox: Sandbox) -> None:
    proc = sandbox.run("apply", "--version", "v0.1.1")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["status"] == "updated"
    assert data["from"] == "v0.1.0"
    assert data["to"] == "v0.1.1"
    assert data["migrations_run"] == ["002_add_marker.sh"]

    # 代码层切到目标 tag
    assert sandbox.clone_describe() == "v0.1.1"
    # 迁移: 002 真跑了,001 已记账故跳过
    assert (sandbox.instance / "data" / "marker-002").exists()
    assert not (sandbox.instance / "data" / "marker-001").exists()
    ledger = (sandbox.instance / "state" / "migrations-applied").read_text().splitlines()
    assert ledger == ["001_init.sh", "002_add_marker.sh"]
    # docker 调用序列: build -> up -d -> ps(健康检查)
    calls = sandbox.docker_calls()
    idx_build = next(i for i, c in enumerate(calls) if " build" in c)
    idx_up = next(i for i, c in enumerate(calls) if " up -d" in c)
    idx_ps = next(i for i, c in enumerate(calls) if " ps " in c)
    assert idx_build < idx_up < idx_ps
    # 统一 compose 调用形态
    assert "-f" in calls[idx_build] and "compose.prod.yml" in calls[idx_build]
    assert "compose.override.yml" in calls[idx_build]
    assert "--env-file" in calls[idx_build]
    assert "-p rtime-instance" in calls[idx_build]
    # 状态与备份
    assert (sandbox.instance / "state" / "current-version").read_text().strip() == "v0.1.1"
    assert (sandbox.instance / "state" / "previous-version").read_text().strip() == "v0.1.0"
    backup = Path(data["backup"])
    assert backup.is_dir()
    assert (backup / "env.snapshot").exists()
    assert (backup / "state-snapshot.tar.gz").exists()
    last = json.loads((sandbox.instance / "state" / "last-update.json").read_text())
    assert last["status"] == "updated"


def test_apply_breaking_with_yes_succeeds(sandbox: Sandbox) -> None:
    sandbox.publish_v020()
    proc = sandbox.run("apply", "--yes")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["status"] == "updated"
    assert data["to"] == "v0.2.0"
    assert sandbox.clone_describe() == "v0.2.0"


def test_apply_health_fail_rolls_back(sandbox: Sandbox) -> None:
    # 第一次健康检查(1 次 ps)不健康 -> 回滚;回滚后的 ps 健康 -> 退出码 1
    proc = sandbox.run("apply", "--version", "v0.1.1", DOCKER_SHIM_UNHEALTHY_FIRST="1")
    assert proc.returncode == 1, proc.stderr
    data = out_json(proc)
    assert data["status"] == "rolled-back"
    assert data["attempted"] == "v0.1.1"
    assert data["reason"] == "health check failed"
    assert data["rollback_healthy"] is True
    assert data["backup"]
    # 代码层回到旧版本,current-version 未写成新版本
    assert sandbox.clone_describe() == "v0.1.0"
    assert not (sandbox.instance / "state" / "current-version").exists()
    # docker 序列出现两轮 build/up(升级一轮 + 回滚一轮)
    calls = sandbox.docker_calls()
    assert len([c for c in calls if " build" in c]) == 2
    assert len([c for c in calls if " up -d" in c]) == 2


def test_apply_health_fail_and_rollback_unhealthy_exit7(sandbox: Sandbox) -> None:
    proc = sandbox.run("apply", "--version", "v0.1.1", DOCKER_SHIM_UNHEALTHY_FIRST="999")
    assert proc.returncode == 7, proc.stderr
    data = out_json(proc)
    assert data["status"] == "rolled-back"
    assert data["rollback_healthy"] is False


def test_apply_dry_run_no_side_effects(sandbox: Sandbox) -> None:
    head_before = git(sandbox.clone, "rev-parse", "HEAD")
    proc = sandbox.run("apply", "--version", "v0.1.1", "--dry-run")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["status"] == "dry-run"
    assert data["to"] == "v0.1.1"
    assert data["pending_migrations"] == ["002_add_marker.sh"]
    # 一切写操作跳过
    assert sandbox.docker_calls() == []
    assert git(sandbox.clone, "rev-parse", "HEAD") == head_before
    assert not (sandbox.instance / "data" / "marker-002").exists()
    assert not (sandbox.instance / "state" / "current-version").exists()
    assert not (sandbox.instance / "state" / "backups").exists()


# ---------- rollback ----------


def test_rollback_returns_to_previous(sandbox: Sandbox) -> None:
    proc = sandbox.run("apply", "--version", "v0.1.1")
    assert proc.returncode == 0, proc.stderr
    sandbox.shim_log.write_text("")  # 清空,只看 rollback 的调用
    proc = sandbox.run("rollback")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["status"] == "rolled-back"
    assert data["from"] == "v0.1.1"
    assert data["to"] == "v0.1.0"
    assert data["healthy"] is True
    assert "backups" in data["latest_backup"]
    assert sandbox.clone_describe() == "v0.1.0"
    assert (sandbox.instance / "state" / "current-version").read_text().strip() == "v0.1.0"
    calls = sandbox.docker_calls()
    assert any(" build" in c for c in calls)
    assert any(" up -d" in c for c in calls)


def test_rollback_without_history_fails(sandbox: Sandbox) -> None:
    proc = sandbox.run("rollback")
    assert proc.returncode == 2, proc.stderr
    assert out_json(proc)["status"] == "error"


# ---------- 锁 ----------


def test_lock_second_apply_refused(sandbox: Sandbox) -> None:
    import fcntl

    state = sandbox.instance / "state"
    # 同时占住两种锁形态:flock(Linux 上 update.sh 用 flock)与 mkdir 锁(无 flock 的平台)
    lockfile = state / "update.lock"
    fd = os.open(lockfile, os.O_WRONLY | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    lockdir = state / "update.lock.d"
    lockdir.mkdir()
    (lockdir / "pid").write_text(f"{os.getpid()}\n")  # 活着的进程 => 锁有效
    try:
        proc = sandbox.run("apply", "--version", "v0.1.1")
        assert proc.returncode == 5, proc.stderr
        assert out_json(proc)["status"] == "locked"
        assert sandbox.docker_calls() == []
        assert sandbox.clone_describe() == "v0.1.0"
    finally:
        os.close(fd)


def test_stale_dir_lock_is_taken_over(sandbox: Sandbox) -> None:
    state = sandbox.instance / "state"
    lockdir = state / "update.lock.d"
    lockdir.mkdir()
    (lockdir / "pid").write_text("999999999\n")  # 死进程 => 失效锁,接管
    proc = sandbox.run(
        "apply", "--version", "v0.1.1", RTIME_UPDATE_LOCK_METHOD="dir"
    )
    assert proc.returncode == 0, proc.stderr
    assert out_json(proc)["status"] == "updated"


# ---------- status ----------


def test_status_after_apply(sandbox: Sandbox) -> None:
    proc = sandbox.run("apply", "--version", "v0.1.1")
    assert proc.returncode == 0, proc.stderr
    proc = sandbox.run("status")
    assert proc.returncode == 0, proc.stderr
    data = out_json(proc)
    assert data["current_version"] == "v0.1.1"
    assert data["repo_describe"].startswith("v0.1.1")
    assert data["containers"][0]["service"] == "feishu-bridge"
    assert data["containers"][0]["state"] == "running"
    assert data["last_update"]["status"] == "updated"


# ---------- 参数校验 ----------


def test_missing_instance_is_usage_error(tmp_path: Path, sandbox: Sandbox) -> None:
    proc = subprocess.run(
        [str(UPDATE_SH), "check", "--repo", str(sandbox.clone)],
        capture_output=True,
        text=True,
        env=sandbox.env(),
    )
    assert proc.returncode == 2
    assert out_json(proc)["status"] == "error"
