# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-brain-docpack-tooling.sh"

# The installer is a POSIX shell script and cannot be spawned as a native
# Windows executable; skip the whole module there rather than fail on WinError 193.
pytestmark = pytest.mark.skipif(
    os.name == "nt",
    reason="install-brain-docpack-tooling.sh is a POSIX shell entrypoint",
)


def test_install_tooling_help():
    completed = subprocess.run(
        [str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Usage: scripts/install-brain-docpack-tooling.sh" in completed.stdout


def test_install_tooling_dry_run_does_not_write(tmp_path):
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--skip-cli",
            "--codex-home",
            str(tmp_path / "codex"),
            "--claude-home",
            str(tmp_path / "claude"),
            "--plugin-home",
            str(tmp_path / "plugins"),
            "--marketplace",
            str(tmp_path / "marketplace.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "- mode: dry-run" in completed.stdout
    assert "Standalone MCP client snippet" in completed.stdout
    assert not (tmp_path / "codex").exists()
    assert not (tmp_path / "plugins").exists()


def test_install_tooling_dry_run_prints_cli_wrappers(tmp_path):
    bin_dir = tmp_path / "bin"
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--bin-dir",
            str(bin_dir),
            "--skip-codex-skill",
            "--skip-claude-skill",
            "--skip-codex-plugin",
            "--no-mcp-snippet",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"dry-run: write Python wrapper {bin_dir.resolve() / 'brain-docpack'} -> brain_docpack.cli" in completed.stdout
    assert f"dry-run: write Python wrapper {bin_dir.resolve() / 'brain-docpack-mcp'} -> brain_docpack.mcp_server" in completed.stdout
    assert not bin_dir.exists()


def test_install_tooling_apply_syncs_skills_plugin_and_marketplace(tmp_path):
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--apply",
            "--skip-cli",
            "--codex-home",
            str(tmp_path / "codex"),
            "--claude-home",
            str(tmp_path / "claude"),
            "--plugin-home",
            str(tmp_path / "plugins"),
            "--marketplace",
            str(tmp_path / "agents" / "plugins" / "marketplace.json"),
            "--write-codex-marketplace",
            "--no-mcp-snippet",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "codex" / "skills" / "brain-docpack" / "SKILL.md").is_file()
    assert (tmp_path / "claude" / "skills" / "brain-docpack" / "SKILL.md").is_file()
    assert (tmp_path / "plugins" / "brain-docpack" / ".mcp.json").is_file()

    marketplace = json.loads(
        (tmp_path / "agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entry = next(item for item in marketplace["plugins"] if item["name"] == "brain-docpack")
    assert entry["source"]["source"] == "local"
    assert entry["policy"]["installation"] == "AVAILABLE"
