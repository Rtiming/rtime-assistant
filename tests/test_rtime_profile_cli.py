# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-profile" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_profile.cli")


def _make_repo_fixture(root: Path) -> tuple[Path, Path]:
    repo = root / "repo"
    brain = root / "brain"
    (repo / "docs").mkdir(parents=True)
    (repo / "apps" / "feishu-bridge").mkdir(parents=True)
    (repo / "packages" / "rtime-profile" / "src" / "rtime_profile").mkdir(parents=True)
    (repo / "skills" / "rtime-profile").mkdir(parents=True)
    (repo / "plugins" / "rtime-profile").mkdir(parents=True)
    (repo / "README.md").write_text("# Project\nmodel and output overview\n", encoding="utf-8")
    (repo / "AGENTS.md").write_text("# Rules\npermission and sensitive policy\n", encoding="utf-8")
    (repo / "docs" / "prompt-layering.md").write_text(
        "# Prompt Layering\npersona prompt context model\n", encoding="utf-8"
    )
    (repo / "docs" / "context-unlocking.md").write_text(
        "# Context\nsensitive permission memory policy\n", encoding="utf-8"
    )
    (repo / "docs" / "bridge-requirements.md").write_text(
        "# Bridge\nmodel permission automation output policy\n", encoding="utf-8"
    )
    (repo / "docs" / "logging-and-audit.md").write_text(
        "# Logging\nsecret token redaction permission\n", encoding="utf-8"
    )
    (repo / "docs" / "component-deep-dive.md").write_text("# Runtime\nprofile model\n", encoding="utf-8")
    (repo / "docs" / "ui-guide.md").write_text("# UI\nFeishu output segmented\n", encoding="utf-8")
    (repo / "docs" / "runbook.md").write_text("# Runbook\nshowToolCalls output\n", encoding="utf-8")
    (repo / "apps" / "feishu-bridge" / "AGENTS.md").write_text(
        "# Bridge Rules\noutput permission\n", encoding="utf-8"
    )
    (repo / "packages" / "rtime-profile" / "src" / "rtime_profile" / "cli.py").write_text(
        "# cli\n", encoding="utf-8"
    )
    (brain / "_meta").mkdir(parents=True)
    (brain / "profile").mkdir(parents=True)
    (brain / "CLAUDE.md").write_text("# Global\nassistant persona privacy preference\n", encoding="utf-8")
    (brain / "_meta" / "about-me.md").write_text("# About\npersonal profile\n", encoding="utf-8")
    (brain / "profile" / "index.md").write_text("# Profile\nstyle memory\n", encoding="utf-8")
    return repo, brain


def test_doctor_reports_profile_surfaces(tmp_path, capfd):
    cli = _load_cli()
    repo, brain = _make_repo_fixture(tmp_path)

    assert cli.main(["doctor", "--repo-root", str(repo), "--brain-root", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["checks"]["global_claude"] == "ok"
    assert data["checks"]["repo_package"] == "ok"


def test_scan_reports_sources_without_bodies_and_policy_coverage(tmp_path, capfd):
    cli = _load_cli()
    repo, brain = _make_repo_fixture(tmp_path)

    assert cli.main(["scan", "--repo-root", str(repo), "--brain-root", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["privacy"]["source_bodies_returned"] is False
    assert data["layer_counts"]["global"] == 1
    assert data["layer_counts"]["project"] >= 3
    assert data["policy_coverage"]["persona"] is True
    assert data["policy_coverage"]["model"] is True
    assert data["policy_coverage"]["permission"] is True
    assert data["policy_coverage"]["sensitive"] is True
    assert all("body" not in source for source in data["sources"])


def test_panel_returns_adjustment_lanes(tmp_path, capfd):
    cli = _load_cli()
    repo, brain = _make_repo_fixture(tmp_path)

    assert cli.main(["panel", "--repo-root", str(repo), "--brain-root", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    lanes = {lane["lane"]: lane for lane in data["panels"]["adjustment_lanes"]}
    assert lanes["persona"]["write_policy"] == "proposal_first"
    assert lanes["permission_policy"]["risk"] == "high_impact_policy_change"


def test_plan_routes_profile_adjustment_without_writes(tmp_path, capfd):
    cli = _load_cli()
    repo, brain = _make_repo_fixture(tmp_path)

    request = "调整助手人格、模型策略和权限策略，但不要读取 API key"
    assert cli.main(["plan", request, "--repo-root", str(repo), "--brain-root", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["write_enabled"] is False
    assert data["requires_confirmation"] is True
    categories = {item["category"] for item in data["recommended_changes"]}
    assert {"persona", "model", "permission_sensitive"} <= categories
    assert data["privacy"]["request_body_logged"] is False


def test_missing_required_source_creates_scan_risk(tmp_path, capfd):
    cli = _load_cli()
    repo, brain = _make_repo_fixture(tmp_path)
    (brain / "CLAUDE.md").unlink()

    assert cli.main(["scan", "--repo-root", str(repo), "--brain-root", str(brain)]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert "required_profile_sources_missing" in data["risks"]
