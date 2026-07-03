# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docker-prod-check.sh"
ENV_EXAMPLE = ROOT / "deploy" / "env" / "feishu-bridge.prod.env.example"


def test_docker_prod_check_help():
    completed = subprocess.run(
        [str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Usage: scripts/docker-prod-check.sh" in completed.stdout
    assert "--use-host-proxy" in completed.stdout


def test_docker_prod_check_build_dry_run_clears_proxy_by_default():
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--env-file",
            str(ENV_EXAMPLE),
            "--build",
            "--dry-run",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "build" in completed.stdout
    assert "HTTP_PROXY=" in completed.stdout


def test_docker_prod_check_can_forward_proxy_without_printing_values(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://user:secret@example.invalid:8080")

    completed = subprocess.run(
        [
            str(SCRIPT),
            "--env-file",
            str(ENV_EXAMPLE),
            "--build",
            "--dry-run",
            "--use-host-proxy",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "--build-arg HTTPS_PROXY" in completed.stdout
    assert "HTTPS_PROXY=" not in completed.stdout
    assert "user:secret" not in completed.stdout
