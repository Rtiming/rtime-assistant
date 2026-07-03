# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for scripts/check-entrypoint-drift.py (the cross-surface drift gate)."""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-entrypoint-drift.py"

SKILL = "---\nname: foo\ndescription: demo skill\n---\n\nbody\n"


def _build_consistent_repo(tmp: Path) -> None:
    (tmp / "skills" / "foo").mkdir(parents=True)
    (tmp / "skills" / "foo" / "SKILL.md").write_text(SKILL, encoding="utf-8")

    codex = tmp / "plugins" / "foo" / ".codex-plugin"
    codex.mkdir(parents=True)
    (codex / "plugin.json").write_text(
        json.dumps({"name": "foo", "version": "0.1.0"}), encoding="utf-8"
    )
    (tmp / "plugins" / "foo" / ".mcp.json").write_text(
        json.dumps(
            {"mcpServers": {"foo": {"cwd": ".", "command": "./scripts/foo-mcp.sh", "args": []}}}
        ),
        encoding="utf-8",
    )
    scripts = tmp / "plugins" / "foo" / "scripts"
    scripts.mkdir(parents=True)
    (scripts / "foo-mcp.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    bundled = tmp / "plugins" / "foo" / "skills" / "foo"
    bundled.mkdir(parents=True)
    (bundled / "SKILL.md").write_text(SKILL, encoding="utf-8")

    (tmp / "packages" / "foo").mkdir(parents=True)
    (tmp / "packages" / "foo" / "pyproject.toml").write_text(
        '[project]\nname = "foo"\nversion = "0.1.0"\n', encoding="utf-8"
    )

    (tmp / "module-submit.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "modules": [
                    {"id": "foo", "paths": ["plugins/foo/", "packages/foo/", "skills/foo/"]}
                ],
            }
        ),
        encoding="utf-8",
    )


def _run(tmp: Path) -> dict:
    completed = subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(tmp), "--json"],
        capture_output=True,
        text=True,
        check=False,
    )
    return json.loads(completed.stdout)


def test_consistent_repo_passes(tmp_path):
    _build_consistent_repo(tmp_path)
    result = _run(tmp_path)
    assert result["ok"] is True
    assert result["errors"] == []


def test_bundled_skill_drift_is_error(tmp_path):
    _build_consistent_repo(tmp_path)
    (tmp_path / "plugins" / "foo" / "skills" / "foo" / "SKILL.md").write_text(
        SKILL + "drifted line\n", encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("differs from canonical" in e for e in result["errors"])


def test_name_disagreement_is_error(tmp_path):
    _build_consistent_repo(tmp_path)
    (tmp_path / "plugins" / "foo" / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "bar", "version": "0.1.0"}), encoding="utf-8"
    )
    result = _run(tmp_path)
    assert any("!= dir name" in e for e in result["errors"])


def test_version_mismatch_is_error(tmp_path):
    _build_consistent_repo(tmp_path)
    (tmp_path / "packages" / "foo" / "pyproject.toml").write_text(
        '[project]\nname = "foo"\nversion = "0.2.0"\n', encoding="utf-8"
    )
    result = _run(tmp_path)
    assert any("version=" in e and "0.2.0" in e for e in result["errors"])


def test_missing_mcp_command_is_error(tmp_path):
    _build_consistent_repo(tmp_path)
    (tmp_path / "plugins" / "foo" / "scripts" / "foo-mcp.sh").unlink()
    result = _run(tmp_path)
    assert any("missing file" in e for e in result["errors"])


def test_unreferenced_dir_is_warning_not_error(tmp_path):
    _build_consistent_repo(tmp_path)
    orphan = tmp_path / "packages" / "orphan"
    orphan.mkdir(parents=True)
    (orphan / "pyproject.toml").write_text(
        '[project]\nname = "orphan"\nversion = "0.1.0"\n', encoding="utf-8"
    )
    result = _run(tmp_path)
    # An unregistered package with no plugin is a coverage warning, not a hard error.
    assert result["ok"] is True
    assert any("orphan" in w for w in result["warnings"])


# ---- P3: course vocab + gateway port + model registry -------------------------------


def test_real_repo_has_no_entrypoint_drift():
    """Integration guard: the actual repo must pass every drift check (including the
    P3 model-registry consistency)."""
    result = _run(ROOT)
    assert result["errors"] == [], result["errors"]


def _write_course_vocab(tmp: Path, view_names: str) -> None:
    ci = tmp / "packages" / "brain-docpack" / "src" / "brain_docpack"
    ci.mkdir(parents=True)
    (ci / "course_intake.py").write_text(
        'OBSIDIAN_CATEGORY_FOLDERS = {"slides": "课件", "misc": "资料"}\n'
        'OBSIDIAN_MARKDOWN_FOLDER = "文稿"\n',
        encoding="utf-8",
    )
    ml = tmp / "scripts" / "brain-intake"
    ml.mkdir(parents=True)
    (ml / "m4_link.py").write_text(f"COURSE_VIEW_NAMES = {view_names}\n", encoding="utf-8")


def test_course_vocab_consistent_passes(tmp_path):
    _write_course_vocab(tmp_path, '{"课件", "文稿"}')
    assert _run(tmp_path)["ok"] is True


def test_course_vocab_drift_is_error(tmp_path):
    _write_course_vocab(tmp_path, '{"课件"}')  # missing 文稿
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("course vocab drift" in e for e in result["errors"])


def _write_gateway_port(tmp: Path, env_port: str) -> None:
    gw = tmp / "apps" / "assistant-gateway"
    gw.mkdir(parents=True)
    (gw / "gateway.py").write_text(
        'port = int(os.environ.get("GATEWAY_PORT", "8765"))\n', encoding="utf-8"
    )
    env = tmp / "deploy" / "env"
    env.mkdir(parents=True)
    (env / "assistant-gateway.env.example").write_text(f"GATEWAY_PORT={env_port}\n", encoding="utf-8")


def test_gateway_port_consistent_passes(tmp_path):
    _write_gateway_port(tmp_path, "8765")
    assert _run(tmp_path)["ok"] is True


def test_gateway_port_drift_is_error(tmp_path):
    _write_gateway_port(tmp_path, "9999")
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("gateway port drift" in e for e in result["errors"])


def _seed_registry(tmp: Path) -> None:
    """Copy the real rtime-models package into a tmp root so the registry checks run."""
    import shutil

    shutil.copytree(ROOT / "packages" / "rtime-models", tmp / "packages" / "rtime-models")


def test_stale_model_defaults_sh_is_error(tmp_path):
    _seed_registry(tmp_path)
    defaults = tmp_path / "deploy" / "bin"
    defaults.mkdir(parents=True)
    (defaults / "model-defaults.sh").write_text("# stale\nREG_DEEPSEEK_MODEL='wrong'\n", encoding="utf-8")
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("model-defaults.sh is stale" in e for e in result["errors"])


def test_claude_rtime_fallback_drift_is_error(tmp_path):
    _seed_registry(tmp_path)
    rtime = tmp_path / "deploy" / "bin"
    rtime.mkdir(parents=True)
    # A fallback alias table that disagrees with the registry must be flagged.
    (rtime / "claude-rtime").write_text(
        '_FALLBACK_ALIASES = {"ds": "WRONG"}\n', encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("_FALLBACK_ALIASES" in e for e in result["errors"])


def test_model_aliases_json_drift_is_error(tmp_path):
    _seed_registry(tmp_path)
    (tmp_path / ".env.example").write_text(
        'MODEL_ALIASES_JSON={"kimi":""}\n', encoding="utf-8"
    )
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("MODEL_ALIASES_JSON" in e for e in result["errors"])


def test_capability_schema_drift_is_error(tmp_path):
    _seed_registry(tmp_path)
    types_ts = tmp_path / "apps" / "obsidian-rtime-assistant" / "src"
    types_ts.mkdir(parents=True)
    (types_ts / "types.ts").write_text(
        "export interface AssistantModelCapabilities {\n  agent_tools?: boolean;\n}\n",
        encoding="utf-8",
    )
    result = _run(tmp_path)
    assert result["ok"] is False
    assert any("AssistantModelCapabilities keys" in e for e in result["errors"])
