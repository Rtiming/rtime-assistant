# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "install-rtime-tooling.sh"


def test_install_rtime_tooling_help():
    completed = subprocess.run(
        [str(SCRIPT), "--help"],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0
    assert "Usage: scripts/install-rtime-tooling.sh" in completed.stdout
    assert "brain-library" in completed.stdout
    assert "brain-citation" in completed.stdout
    assert "rtime-assistant-runtime" in completed.stdout
    assert "rtime-hub-connector" in completed.stdout
    assert "rtime-context" in completed.stdout
    assert "rtime-profile" in completed.stdout
    assert "rtime-automation" in completed.stdout
    assert "rtime-review" in completed.stdout
    assert "rtime-agent-control" in completed.stdout
    assert "--write-mcp-config PATH" in completed.stdout
    assert "--check-installed" in completed.stdout


def test_install_rtime_tooling_dry_run_does_not_write(tmp_path):
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
            "--write-mcp-config",
            str(tmp_path / "mcp.json"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert "- mode: dry-run" in completed.stdout
    assert f"- mcp_config: {tmp_path / 'mcp.json'}" in completed.stdout
    assert f"dry-run: write combined MCP config {tmp_path / 'mcp.json'}" in completed.stdout
    assert (
        "- tools: brain-docpack brain-library brain-citation rtime-assistant-runtime rtime-hub-connector rtime-context rtime-profile rtime-automation rtime-review rtime-agent-control"
        in completed.stdout
    )
    assert "Standalone MCP client snippet for brain-docpack" in completed.stdout
    assert "Standalone MCP client snippet for brain-library" in completed.stdout
    assert "Standalone MCP client snippet for brain-citation" in completed.stdout
    assert "Standalone MCP client snippet for rtime-assistant-runtime" in completed.stdout
    assert "Standalone MCP client snippet for rtime-hub-connector" in completed.stdout
    assert "Standalone MCP client snippet for rtime-context" in completed.stdout
    assert "Standalone MCP client snippet for rtime-profile" in completed.stdout
    assert "Standalone MCP client snippet for rtime-automation" in completed.stdout
    assert "Standalone MCP client snippet for rtime-review" in completed.stdout
    assert "Standalone MCP client snippet for rtime-agent-control" in completed.stdout
    assert not (tmp_path / "codex").exists()
    assert not (tmp_path / "plugins").exists()
    assert not (tmp_path / "mcp.json").exists()


def test_install_rtime_tooling_dry_run_prints_cli_wrappers(tmp_path):
    bin_dir = tmp_path / "bin"
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--tool",
            "brain-docpack",
            "--bin-dir",
            str(bin_dir),
            "--skip-codex-skill",
            "--skip-claude-skill",
            "--skip-codex-plugin",
            "--no-mcp-snippets",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert f"dry-run: write Python wrapper {bin_dir.resolve() / 'brain-docpack'} -> brain_docpack.cli" in completed.stdout
    assert f"dry-run: write Python wrapper {bin_dir.resolve() / 'brain-docpack-mcp'} -> brain_docpack.mcp_server" in completed.stdout
    assert not bin_dir.exists()


def test_install_rtime_tooling_apply_syncs_all_tools_and_marketplace(tmp_path):
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--apply",
            "--profile",
            "mac",  # 固定 profile,使断言的 BRAIN_ROOT/HUB/REMINDERS 路径与运行测试的主机无关
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
            "--write-mcp-config",
            str(tmp_path / "mcp" / "rtime-tools.json"),
            "--no-mcp-snippets",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    for tool in (
        "brain-docpack",
        "brain-library",
        "brain-citation",
        "rtime-assistant-runtime",
        "rtime-hub-connector",
        "rtime-context",
        "rtime-profile",
        "rtime-automation",
        "rtime-review",
        "rtime-agent-control",
    ):
        assert (tmp_path / "codex" / "skills" / tool / "SKILL.md").is_file()
        assert (tmp_path / "claude" / "skills" / tool / "SKILL.md").is_file()
        assert (tmp_path / "plugins" / tool / ".mcp.json").is_file()

    marketplace = json.loads(
        (tmp_path / "agents" / "plugins" / "marketplace.json").read_text(encoding="utf-8")
    )
    entries = {item["name"]: item for item in marketplace["plugins"]}
    assert set(entries) == {
        "brain-docpack",
        "brain-library",
        "brain-citation",
        "rtime-assistant-runtime",
        "rtime-hub-connector",
        "rtime-context",
        "rtime-profile",
        "rtime-automation",
        "rtime-review",
        "rtime-agent-control",
        "rtime-library-gateway",
    }
    assert entries["brain-library"]["source"]["source"] == "local"
    assert entries["brain-citation"]["source"]["source"] == "local"
    assert entries["rtime-assistant-runtime"]["policy"]["installation"] == "AVAILABLE"
    assert entries["rtime-hub-connector"]["policy"]["authentication"] == "ON_INSTALL"
    assert entries["rtime-context"]["category"] == "Productivity"
    assert entries["rtime-profile"]["source"]["source"] == "local"
    assert entries["rtime-automation"]["source"]["source"] == "local"
    assert entries["rtime-review"]["source"]["source"] == "local"
    assert entries["rtime-agent-control"]["source"]["source"] == "local"

    mcp_config = json.loads((tmp_path / "mcp" / "rtime-tools.json").read_text(encoding="utf-8"))
    servers = mcp_config["mcpServers"]
    assert set(servers) == set(entries)
    assert servers["rtime-profile"]["args"] == ["-m", "rtime_profile.mcp_server"]

    # BRAIN_ROOT / RTIME_HUB_ROOT / RTIME_REMINDERS_PATH are resolved per platform
    # (mac vs orangepi) by the installer's profile detection, so assert the wiring
    # and internal consistency against the *resolved* values rather than hardcoded
    # absolute paths. This keeps the test green on both macOS and orangepi.
    brain_root = servers["rtime-profile"]["env"]["BRAIN_ROOT"]
    hub_root = servers["rtime-hub-connector"]["env"]["RTIME_HUB_ROOT"]
    reminders_path = servers["rtime-automation"]["env"]["RTIME_REMINDERS_PATH"]

    assert brain_root.startswith("/") and brain_root.endswith("/sync/brain")
    assert hub_root.startswith("/") and hub_root.endswith("/rtime-hub")
    assert reminders_path == f"{brain_root}/_system/reminders.jsonl"

    # Each env var is wired into exactly the tools that consume it, and the value
    # is identical everywhere it appears (single resolved root per platform).
    brain_tools = {
        "brain-library",
        "brain-citation",
        "rtime-profile",
        "rtime-context",
        "rtime-agent-control",
        "rtime-library-gateway",
    }
    hub_tools = {
        "rtime-hub-connector",
        "rtime-context",
        "rtime-agent-control",
        "rtime-library-gateway",
    }
    reminders_tools = {
        "rtime-automation",
        "rtime-agent-control",
        "rtime-library-gateway",
    }
    for tool in brain_tools:
        assert servers[tool]["env"]["BRAIN_ROOT"] == brain_root
    for tool in hub_tools:
        assert servers[tool]["env"]["RTIME_HUB_ROOT"] == hub_root
    for tool in reminders_tools:
        assert servers[tool]["env"]["RTIME_REMINDERS_PATH"] == reminders_path

    assert "BRAIN_ROOT" not in servers["rtime-review"]["env"]
    assert servers["rtime-agent-control"]["args"] == ["-m", "rtime_agent_control.mcp_server"]


def test_install_rtime_tooling_check_installed_reports_json_success(tmp_path):
    apply_cmd = [
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
        "--write-mcp-config",
        str(tmp_path / "mcp" / "rtime-tools.json"),
        "--no-mcp-snippets",
    ]
    applied = subprocess.run(
        apply_cmd,
        text=True,
        capture_output=True,
        check=False,
    )
    assert applied.returncode == 0, applied.stderr

    checked = subprocess.run(
        [
            str(SCRIPT),
            "--check-installed",
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
            "--write-mcp-config",
            str(tmp_path / "mcp" / "rtime-tools.json"),
            "--no-mcp-snippets",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert checked.returncode == 0, checked.stderr
    report = json.loads(checked.stdout)
    assert report["ok"] is True
    assert report["mode"] == "check-installed"
    assert report["surfaces"]["cli"] is False
    assert report["surfaces"]["codex_marketplace"] is True
    assert report["surfaces"]["mcp_config"] is True
    assert report["missing"] == []
    assert {item["tool"] for item in report["tools"]} == {
        "brain-docpack",
        "brain-library",
        "brain-citation",
        "rtime-assistant-runtime",
        "rtime-hub-connector",
        "rtime-context",
        "rtime-profile",
        "rtime-automation",
        "rtime-review",
        "rtime-agent-control",
        "rtime-library-gateway",
    }
    automation = next(item for item in report["tools"] if item["tool"] == "rtime-automation")
    assert automation["ok"] is True
    assert automation["checks"]["cli_import"]["requested"] is False
    assert automation["checks"]["codex_skill"]["ok"] is True
    assert automation["checks"]["claude_skill"]["ok"] is True
    assert automation["checks"]["plugin_source"]["ok"] is True
    assert automation["checks"]["plugin_mcp"]["ok"] is True
    assert automation["checks"]["marketplace_entry"]["entry_exists"] is True
    assert automation["checks"]["mcp_config"]["server_exists"] is True


def test_install_rtime_tooling_check_installed_reports_missing_surfaces(tmp_path):
    mcp_path = tmp_path / "mcp" / "rtime-tools.json"
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--check-installed",
            "--skip-cli",
            "--tool",
            "brain-library",
            "--codex-home",
            str(tmp_path / "codex"),
            "--claude-home",
            str(tmp_path / "claude"),
            "--plugin-home",
            str(tmp_path / "plugins"),
            "--marketplace",
            str(tmp_path / "agents" / "plugins" / "marketplace.json"),
            "--write-codex-marketplace",
            "--write-mcp-config",
            str(mcp_path),
            "--no-mcp-snippets",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 1
    assert not mcp_path.exists()
    report = json.loads(completed.stdout)
    assert report["ok"] is False
    assert report["tools"][0]["tool"] == "brain-library"
    assert report["tools"][0]["checks"]["source_package"]["ok"] is True
    assert report["tools"][0]["checks"]["cli_import"]["requested"] is False
    missing = {(item["tool"], item["surface"]) for item in report["missing"]}
    assert missing == {
        ("brain-library", "codex_skill"),
        ("brain-library", "claude_skill"),
        ("brain-library", "plugin_source"),
        ("brain-library", "plugin_mcp"),
        ("brain-library", "marketplace_entry"),
        ("brain-library", "mcp_config"),
    }


def test_install_rtime_tooling_can_select_one_tool(tmp_path):
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--apply",
            "--skip-cli",
            "--tool",
            "brain-library",
            "--codex-home",
            str(tmp_path / "codex"),
            "--claude-home",
            str(tmp_path / "claude"),
            "--plugin-home",
            str(tmp_path / "plugins"),
            "--write-mcp-config",
            str(tmp_path / "mcp.json"),
            "--no-mcp-snippets",
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 0, completed.stderr
    assert (tmp_path / "codex" / "skills" / "brain-library" / "SKILL.md").is_file()
    assert (tmp_path / "plugins" / "brain-library" / ".mcp.json").is_file()
    mcp_config = json.loads((tmp_path / "mcp.json").read_text(encoding="utf-8"))
    assert set(mcp_config["mcpServers"]) == {"brain-library"}
    assert not (tmp_path / "codex" / "skills" / "brain-docpack").exists()
    assert not (tmp_path / "plugins" / "rtime-assistant-runtime").exists()


def test_install_rtime_tooling_rejects_unknown_tool(tmp_path):
    completed = subprocess.run(
        [
            str(SCRIPT),
            "--tool",
            "unknown-tool",
            "--codex-home",
            str(tmp_path / "codex"),
        ],
        text=True,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert "unknown tool" in completed.stderr
