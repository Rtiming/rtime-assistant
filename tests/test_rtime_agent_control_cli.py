# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-agent-control" / "src"

TOOLS = (
    ("brain-docpack", "brain_docpack", "brain-docpack-mcp"),
    ("brain-library", "brain_library", "brain-library-mcp"),
    ("brain-citation", "brain_citation", "brain-citation-mcp"),
    ("rtime-assistant-runtime", "rtime_assistant_runtime", "rtime-runtime-mcp"),
    ("rtime-hub-connector", "rtime_hub_connector", "rtime-hub-mcp"),
    ("rtime-context", "rtime_context", "rtime-context-mcp"),
    ("rtime-profile", "rtime_profile", "rtime-profile-mcp"),
    ("rtime-automation", "rtime_automation", "rtime-automation-mcp"),
    ("rtime-review", "rtime_review", "rtime-review-mcp"),
    ("rtime-agent-control", "rtime_agent_control", "rtime-agent-control-mcp"),
    ("rtime-library-gateway", "rtime_library_gateway", "rtime-library-gateway-mcp"),
)


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_agent_control.cli")


def _make_repo_fixture(root: Path) -> Path:
    repo = root / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "tests").mkdir(parents=True)
    (repo / "AGENTS.md").write_text("# Agents\n", encoding="utf-8")
    (repo / "docs" / "tooling-packaging.md").write_text("# Tooling\n", encoding="utf-8")
    (repo / "docs" / "agent-control-mcp.md").write_text("# Agent Control\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts" / "install-rtime-tooling.sh").write_text(
        "#!/usr/bin/env bash\n# rtime-agent-control\n", encoding="utf-8"
    )

    module_manifest = {
        "schema_version": 1,
        "modules": [
            {
                "id": "agent-control",
                "title": "Agent control MCP",
                "kind": "package",
                "paths": [
                    "packages/rtime-agent-control/",
                    "docs/agent-control-mcp.md",
                    "skills/rtime-agent-control/",
                    "plugins/rtime-agent-control/",
                    "tests/test_rtime_agent_control_cli.py",
                    "tests/test_rtime_agent_control_mcp.py",
                ],
                "checks": {
                    "quick": [
                        {"command": "python -m py_compile packages/rtime-agent-control/src/rtime_agent_control/*.py"},
                        {
                            "env": {"PYTHONPATH": "packages/rtime-agent-control/src"},
                            "command": (
                                "python -m pytest tests/test_rtime_agent_control_cli.py "
                                "tests/test_rtime_agent_control_mcp.py -q"
                            ),
                        },
                        {"command": "scripts/validate-codex-plugin.py plugins/rtime-agent-control"},
                    ]
                },
            },
            {
                "id": "other",
                "title": "Other module",
                "kind": "package",
                "paths": ["packages/other/"],
                "checks": {"quick": [{"command": "python -m pytest tests/test_other.py -q"}]},
            },
        ],
    }
    (repo / "module-submit.json").write_text(json.dumps(module_manifest), encoding="utf-8")

    for tool, module, mcp_cli in TOOLS:
        package_dir = repo / "packages" / tool
        (package_dir / "src" / module).mkdir(parents=True)
        (package_dir / "pyproject.toml").write_text("[project]\nname='x'\n", encoding="utf-8")
        (package_dir / "src" / module / "cli.py").write_text("# cli\n", encoding="utf-8")
        (package_dir / "src" / module / "mcp_server.py").write_text("# mcp\n", encoding="utf-8")

        skill_dir = repo / "skills" / tool
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(
            f"---\nname: {tool}\ndescription: test\n---\n", encoding="utf-8"
        )

        plugin_dir = repo / "plugins" / tool
        (plugin_dir / ".codex-plugin").mkdir(parents=True)
        (plugin_dir / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": tool, "version": "0.1.0"}), encoding="utf-8"
        )
        (plugin_dir / ".mcp.json").write_text('{"mcpServers": {}}', encoding="utf-8")
        (plugin_dir / "scripts").mkdir()
        (plugin_dir / "scripts" / f"{mcp_cli}.sh").write_text("#!/usr/bin/env bash\n", encoding="utf-8")

        (repo / "tests" / f"test_{module}_cli.py").write_text("def test_ok(): pass\n", encoding="utf-8")

    for path in (
        "apps/feishu-bridge/main.py",
        "compose.prod.yml",
        "docs/docker-production.md",
        "docs/deployment.md",
        "docs/runtime-assets.md",
        "packages/rtime-assistant-runtime/pyproject.toml",
    ):
        target = repo / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text("ok\n", encoding="utf-8")

    return repo


def test_doctor_reports_agent_control_surfaces(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["--repo-root", str(repo), "doctor"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["checks"]["mcp_server"]["exists"] is True
    assert data["checks"]["module_submit_entry"]["exists"] is True
    assert data["policy"]["permission_tier"] == "read_only"


def test_repo_root_can_follow_subcommand(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["doctor", "--repo-root", str(repo)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["repo_root"] == str(repo)


def test_tooling_and_config_render_are_read_only(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["tooling", "--repo-root", str(repo)]) == 0
    tooling = json.loads(capfd.readouterr().out)
    assert tooling["ok"] is True
    assert len(tooling["tools"]) == 11

    assert cli.main(["config-render", "--repo-root", str(repo), "--tool", "rtime-agent-control"]) == 0
    rendered = json.loads(capfd.readouterr().out)
    servers = rendered["mcp_config"]["mcpServers"]
    assert rendered["write_enabled"] is False
    assert rendered["privacy"]["contains_secret_values"] is False
    assert list(servers) == ["rtime-agent-control"]
    assert servers["rtime-agent-control"]["args"] == ["-m", "rtime_agent_control.mcp_server"]
    assert servers["rtime-agent-control"]["env"]["RTIME_ASSISTANT_ROOT"] == str(repo)
    assert servers["rtime-agent-control"]["env"]["BRAIN_ROOT"] == "/mnt/brain"


def test_validation_plan_context_plan_and_runtime_snapshot(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["validation-plan", "--repo-root", str(repo), "--module", "agent-control"]) == 0
    plan = json.loads(capfd.readouterr().out)
    assert plan["executed"] is False
    assert plan["write_enabled"] is False
    assert plan["modules"][0]["module_id"] == "agent-control"

    assert cli.main(["context-plan", "need MCP tooling debug config", "--repo-root", str(repo)]) == 0
    context = json.loads(capfd.readouterr().out)
    assert context["ok"] is True
    assert context["permissions"]["write_enabled"] is False
    assert "tooling" in context["task_signals"]

    assert cli.main(["runtime-snapshot", "--repo-root", str(repo), "--run-log", str(tmp_path / "run.jsonl")]) == 0
    snapshot = json.loads(capfd.readouterr().out)
    assert snapshot["ok"] is True
    assert snapshot["live_service_state_checked"] is False
    assert snapshot["mutations_performed"] is False


def test_validation_plan_changed_mode_uses_git_status(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    subprocess.run(["git", "init"], cwd=repo, check=True, capture_output=True, text=True)
    (repo / "packages" / "rtime-agent-control" / "new.py").write_text("# change\n", encoding="utf-8")

    assert cli.main(["validation-plan", "--repo-root", str(repo), "--changed"]) == 0
    plan = json.loads(capfd.readouterr().out)

    assert plan["mode"] == "changed"
    assert plan["modules"][0]["module_id"] == "agent-control"
    assert "packages/rtime-agent-control/new.py" in plan["changed_paths"]


def test_unknown_tool_config_is_error(tmp_path):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    try:
        cli.main(["config-render", "--repo-root", str(repo), "--tool", "missing-tool"])
    except ValueError as exc:
        assert "missing-tool" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("expected ValueError")
