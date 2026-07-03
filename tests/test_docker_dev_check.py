# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "docker-dev-check.sh"


def test_docker_dev_check_help():
    completed = subprocess.run(
        [str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Usage: scripts/docker-dev-check.sh" in completed.stdout
    assert "--timeout" in completed.stdout


def test_docker_dev_check_dry_run_lists_default_services():
    completed = subprocess.run(
        [str(SCRIPT), "--dry-run", "--skip-build", "--timeout", "3"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "feishu-bridge-tests" in completed.stdout
    assert "docpack-tests" in completed.stdout
    assert "docpack-office-tests" in completed.stdout
    assert "dry-run: docker run" in completed.stdout


def test_docker_dev_check_selects_one_service():
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--dry-run",
            "--skip-build",
            "--service",
            "docpack-tests",
            "--timeout",
            "3",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "== docpack-tests ==" in completed.stdout
    assert "== feishu-bridge-tests ==" not in completed.stdout


def test_docker_dev_check_rejects_unknown_service():
    completed = subprocess.run(
        [str(SCRIPT), "--dry-run", "--service", "unknown"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "unknown service" in completed.stderr


def test_docker_dev_check_cleanup_only_dry_run():
    completed = subprocess.run(
        [str(SCRIPT), "--cleanup-only", "--dry-run", "--timeout", "3"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "cleanup residual rtime-assistant-dev containers" in completed.stdout


def test_docker_dev_check_can_forward_proxy_without_printing_values(monkeypatch):
    monkeypatch.setenv("HTTP_PROXY", "http://user:secret@example.invalid:8080")

    completed = subprocess.run(
        [
            str(SCRIPT),
            "--dry-run",
            "--build-only",
            "--use-host-proxy",
            "--service",
            "feishu-bridge-tests",
            "--timeout",
            "3",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "host_proxy_build_args: 1" in completed.stdout
    assert "--build-arg HTTP_PROXY" in completed.stdout
    assert "user:secret" not in completed.stdout
