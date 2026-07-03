# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-context" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_context.cli")


def _make_workspace(root: Path) -> Path:
    workspace = root / "workspace"
    (workspace / "docs").mkdir(parents=True)
    (workspace / "AGENTS.md").write_text("# Rules\n", encoding="utf-8")
    (workspace / "README.md").write_text("# Project\n", encoding="utf-8")
    (workspace / "docs" / "architecture.md").write_text("# Architecture\n", encoding="utf-8")
    return workspace


def test_doctor_reports_repo_surfaces(tmp_path, capfd):
    cli = _load_cli()

    assert cli.main(["--repo-root", str(ROOT), "doctor"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["checks"]["repo_package"] == "ok"
    assert data["checks"]["repo_skill"] == "ok"


def test_plan_detects_runtime_code_and_workspace(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)

    request = "请重构 Feishu bridge，跑 pytest，并检查 run log"
    assert cli.main(["plan", request, "--workspace", str(workspace), "--entry", "feishu"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["entry"] == "feishu"
    assert data["risk"] == "code_change_or_review"
    assert "L3" in data["levels"]
    lanes = {lane["lane"] for lane in data["lanes"]}
    assert "Workspace" in lanes
    assert "Runtime Evidence" in lanes
    assert data["task_signals"]["local_rules"][0]["path"] == "AGENTS.md"


def test_plan_detects_library_and_sensitive_exclusion(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)

    request = "整理 Obsidian Zotero 文献 citation，但不要读取 API key"
    assert cli.main(["plan", request, "--workspace", str(workspace)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["risk"] == "sensitive_context_requested"
    assert "literature" in data["task_signals"]["groups"]
    assert "sensitive" in data["task_signals"]["groups"]
    literature_lane = next(lane for lane in data["lanes"] if lane["lane"] == "Brain / Knowledge Store")
    assert "brain-citation panel" in literature_lane["recommended_tools"]
    assert data["permissions"]["sensitive_unlocked"] is False
    assert data["excluded"][0]["lane"] == "Sensitive"


def test_plan_routes_profile_policy_requests(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)

    request = "调整助手人格和模型策略，顺便检查权限策略"
    assert cli.main(["plan", request, "--workspace", str(workspace)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["risk"] == "assistant_profile_policy"
    assert "profile" in data["task_signals"]["groups"]
    profile_lane = next(lane for lane in data["lanes"] if lane["lane"] == "Assistant Profile & Policy")
    assert "rtime-profile panel" in profile_lane["recommended_tools"]
    assert "rtime-profile plan" in profile_lane["recommended_tools"]


def test_plan_routes_automation_requests(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)

    request = "规划飞书提醒和定时任务，但不要真的发送通知"
    assert cli.main(["plan", request, "--workspace", str(workspace)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["risk"] == "automation_or_reminder"
    assert "automation" in data["task_signals"]["groups"]
    assert "L2" in data["levels"]
    automation_lane = next(lane for lane in data["lanes"] if lane["lane"] == "Workflow Automation")
    assert "rtime-automation panel" in automation_lane["recommended_tools"]
    assert "rtime-automation plan" in automation_lane["recommended_tools"]


def test_allow_sensitive_plans_metadata_lane(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)

    assert (
        cli.main(
            [
                "plan",
                "需要确认 API key 是否配置好",
                "--workspace",
                str(workspace),
                "--allow-sensitive",
            ]
        )
        == 0
    )
    data = json.loads(capfd.readouterr().out)

    assert data["permissions"]["sensitive_unlocked"] is True
    assert any(lane["lane"] == "Sensitive" for lane in data["lanes"])


def test_pack_and_explain_outputs(tmp_path, capfd):
    cli = _load_cli()
    workspace = _make_workspace(tmp_path)
    request = "查看 rtime-hub 项目状态和设备状态"

    assert cli.main(["pack", request, "--workspace", str(workspace)]) == 0
    pack = json.loads(capfd.readouterr().out)
    assert pack["kind"] == "context_pack_skeleton"
    assert "Project Workspace / rtime-hub" in pack["unlock_plan"]["lanes"]
    assert pack["local_rules"][0]["path"] == "AGENTS.md"

    assert cli.main(["explain", request, "--workspace", str(workspace)]) == 0
    explain = json.loads(capfd.readouterr().out)
    assert explain["ok"] is True
    assert any(item["lane"] == "Project Workspace / rtime-hub" for item in explain["explanations"])
