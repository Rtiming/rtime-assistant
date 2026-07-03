# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "module-submit-check.py"


def write_manifest(tmp_path: Path) -> Path:
    manifest = {
        "schema_version": 1,
        "modules": [
            {
                "id": "alpha",
                "title": "Alpha module",
                "kind": "package",
                "paths": ["alpha/", "docs/alpha.md"],
                "checks": {
                    "quick": [
                        {
                            "command": "python -c \"print('alpha-ok')\"",
                        },
                    ],
                    "docker": [
                        {
                            "command": "python -c \"print('alpha-docker')\"",
                        },
                    ],
                },
            },
            {
                "id": "beta",
                "title": "Beta module",
                "kind": "app",
                "paths": ["beta/"],
                "checks": {
                    "quick": [
                        {
                            "cwd": ".",
                            "command": "python -c \"print('beta-ok')\"",
                        },
                    ],
                },
            },
        ],
    }
    path = tmp_path / "module-submit.json"
    path.write_text(json.dumps(manifest), encoding="utf-8")
    return path


def run_script(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [str(SCRIPT), *args],
        cwd=cwd or ROOT,
        text=True,
        capture_output=True,
        check=False,
    )


def test_module_submit_check_lists_modules(tmp_path: Path):
    manifest = write_manifest(tmp_path)

    completed = run_script("--manifest", str(manifest), "--list")

    assert completed.returncode == 0, completed.stderr
    assert "alpha\tpackage\tAlpha module" in completed.stdout
    assert "beta\tapp\tBeta module" in completed.stdout


def test_module_submit_check_dry_run_selected_tiers(tmp_path: Path):
    manifest = write_manifest(tmp_path)

    completed = run_script(
        "--manifest",
        str(manifest),
        "--module",
        "alpha",
        "--tier",
        "quick",
        "--tier",
        "docker",
        "--dry-run",
        "--json",
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run [alpha:quick]" in completed.stdout
    assert "dry-run [alpha:docker]" in completed.stdout
    assert '"status": "planned"' in completed.stdout


def test_module_submit_check_rejects_unknown_module(tmp_path: Path):
    manifest = write_manifest(tmp_path)

    completed = run_script("--manifest", str(manifest), "--module", "missing", "--dry-run")

    assert completed.returncode != 0
    assert "unknown module" in completed.stderr


def test_module_submit_check_writes_report(tmp_path: Path):
    manifest = write_manifest(tmp_path)
    report = tmp_path / "report.md"

    completed = run_script(
        "--manifest",
        str(manifest),
        "--module",
        "alpha",
        "--dry-run",
        "--report",
        str(report),
    )

    assert completed.returncode == 0, completed.stderr
    assert report.exists()
    assert "| alpha | quick | planned |" in report.read_text(encoding="utf-8")


def test_module_submit_check_changed_uses_manifest_root(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required for --changed mode")

    manifest = write_manifest(tmp_path)
    (tmp_path / "alpha").mkdir()
    (tmp_path / "alpha" / "changed.txt").write_text("changed", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    completed = run_script(
        "--manifest",
        str(manifest),
        "--changed",
        "--dry-run",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run [alpha:quick]" in completed.stdout
    assert "dry-run [beta:quick]" not in completed.stdout


def test_module_submit_check_changed_clean_tree_is_noop(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required for --changed mode")

    manifest = write_manifest(tmp_path)
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(["git", "add", "module-submit.json"], cwd=tmp_path, check=True)
    subprocess.run(
        [
            "git",
            "-c",
            "user.name=Test User",
            "-c",
            "user.email=test@example.com",
            "commit",
            "-m",
            "init",
        ],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )

    completed = run_script(
        "--manifest",
        str(manifest),
        "--changed",
        "--dry-run",
        "--json",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    result = json.loads(completed.stdout)
    assert result["ok"] is True
    assert result["modules"] == []
    assert result["records"] == []


def test_module_submit_check_changed_expands_untracked_directories(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required for --changed mode")

    manifest = {
        "schema_version": 1,
        "modules": [
            {
                "id": "nested",
                "title": "Nested module",
                "kind": "package",
                "paths": ["alpha/nested/"],
                "checks": {
                    "quick": [
                        {
                            "command": "python -c \"print('nested-ok')\"",
                        },
                    ],
                },
            },
        ],
    }
    manifest_path = tmp_path / "module-submit.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "alpha" / "nested").mkdir(parents=True)
    (tmp_path / "alpha" / "nested" / "changed.txt").write_text("changed", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    completed = run_script(
        "--manifest",
        str(manifest_path),
        "--changed",
        "--dry-run",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run [nested:quick]" in completed.stdout


def test_module_submit_check_directory_paths_do_not_require_trailing_slash(tmp_path: Path):
    if shutil.which("git") is None:
        pytest.skip("git is required for --changed mode")

    manifest = {
        "schema_version": 1,
        "modules": [
            {
                "id": "nested",
                "title": "Nested module",
                "kind": "package",
                "paths": ["alpha/nested"],
                "checks": {
                    "quick": [
                        {
                            "command": "python -c \"print('nested-ok')\"",
                        },
                    ],
                },
            },
        ],
    }
    manifest_path = tmp_path / "module-submit.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    (tmp_path / "alpha" / "nested").mkdir(parents=True)
    (tmp_path / "alpha" / "nested" / "changed.txt").write_text("changed", encoding="utf-8")
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)

    completed = run_script(
        "--manifest",
        str(manifest_path),
        "--changed",
        "--dry-run",
        cwd=tmp_path,
    )

    assert completed.returncode == 0, completed.stderr
    assert "dry-run [nested:quick]" in completed.stdout
