# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-automation" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_automation.cli")


def _make_repo_fixture(root: Path) -> Path:
    repo = root / "repo"
    (repo / "apps" / "reminder-sender").mkdir(parents=True)
    (repo / "deploy" / "systemd" / "user").mkdir(parents=True)
    (repo / "docs").mkdir(parents=True)
    (repo / "packages" / "rtime-automation" / "src" / "rtime_automation").mkdir(parents=True)
    (repo / "skills" / "rtime-automation").mkdir(parents=True)
    (repo / "plugins" / "rtime-automation" / ".codex-plugin").mkdir(parents=True)
    (repo / "plugins" / "rtime-automation").mkdir(parents=True, exist_ok=True)
    (repo / "apps" / "reminder-sender" / "reminder-sender.js").write_text(
        "const REM = process.env.RTIME_REMINDERS_PATH; const FEISHU_CONFIG = process.env.RTIME_ASSISTANT_FEISHU_CONFIG; if (process.argv.includes('--dry-run')) {}\n",
        encoding="utf-8",
    )
    (repo / "deploy" / "systemd" / "user" / "reminder.service").write_text(
        "[Service]\nExecStart=/usr/bin/node /usr/local/bin/reminder-sender.js\n",
        encoding="utf-8",
    )
    (repo / "deploy" / "systemd" / "user" / "reminder.timer").write_text(
        "[Timer]\nOnCalendar=*:0/1\n\n[Install]\nWantedBy=timers.target\n",
        encoding="utf-8",
    )
    (repo / "docs" / "workflows.md").write_text("# Workflows\n", encoding="utf-8")
    (repo / "docs" / "logging-and-audit.md").write_text("# Logging\n", encoding="utf-8")
    (repo / "packages" / "rtime-automation" / "src" / "rtime_automation" / "cli.py").write_text(
        "# cli\n", encoding="utf-8"
    )
    (repo / "skills" / "rtime-automation" / "SKILL.md").write_text(
        "---\nname: rtime-automation\ndescription: test\n---\n", encoding="utf-8"
    )
    (repo / "plugins" / "rtime-automation" / ".codex-plugin" / "plugin.json").write_text(
        json.dumps({"name": "rtime-automation"}), encoding="utf-8"
    )
    (repo / "plugins" / "rtime-automation" / ".mcp.json").write_text("{}", encoding="utf-8")
    return repo


def _make_reminders(path: Path) -> Path:
    records = [
        {
            "status": "pending",
            "due": "2000-01-01T00:00:00Z",
            "target": "ou_secret",
            "message": "private message",
            "repeat": "none",
        },
        {
            "status": "pending",
            "due": "2026-06-12T00:00:00Z",
            "target": "ou_secret",
            "message": "future message",
            "repeat": "daily",
        },
        {"status": "done", "due": "2026-06-09T00:00:00Z", "message": "done"},
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def _make_failed_reminders(path: Path) -> Path:
    records = [
        {
            "id": "rtime-daily-digest",
            "status": "failed",
            "due": "2026-06-17T12:30:00+08:00",
            "repeat": "daily",
            "mode": "wake",
            "target": "ou_secret",
            "message": "daily digest body",
            "failed_at": "2026-06-17T12:35:49+08:00",
            "last_error": {"code": None, "msg": "TimeoutError: secret-looking detail"},
        },
        {
            "id": "rtime-exam-eve",
            "status": "pending",
            "due": "2026-06-21T21:00:00+08:00",
            "repeat": "none",
            "mode": "wake",
            "target": "ou_secret",
            "message": "exam eve body",
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_doctor_reports_automation_surfaces(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    reminders = _make_reminders(tmp_path / "reminders.jsonl")

    assert cli.main(["doctor", "--repo-root", str(repo), "--reminders", str(reminders)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["surfaces"]["checks"]["sender_script"] == "ok"
    assert data["surfaces"]["checks"]["repo_plugin"] == "ok"


def test_reminders_summary_returns_metadata_without_private_body(tmp_path, capfd):
    cli = _load_cli()
    reminders = _make_reminders(tmp_path / "reminders.jsonl")

    assert cli.main(["reminders", str(reminders), "--now", "2026-06-10T00:01:00Z"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["record_count"] == 3
    assert data["pending_count"] == 2
    assert data["due_pending_count"] == 1
    assert data["due_samples"][0]["message_chars"] == len("private message")
    assert data["privacy"]["message_text_returned"] is False
    assert "message" not in data["due_samples"][0]
    assert "target" not in data["due_samples"][0]


def test_reminders_summary_flags_invalid_pending_records(tmp_path, capfd):
    cli = _load_cli()
    reminders = tmp_path / "bad-reminders.jsonl"
    reminders.write_text(
        json.dumps({"status": "pending", "due": "bad-date", "message": "x"}) + "\n",
        encoding="utf-8",
    )

    assert cli.main(["reminders", str(reminders), "--now", "2026-06-10T00:01:00Z"]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert "pending_reminders_with_invalid_due" in data["risks"]
    assert "pending_reminders_missing_target" in data["risks"]


def test_panel_combines_surfaces_and_reminders(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    reminders = _make_reminders(tmp_path / "reminders.jsonl")

    assert (
        cli.main(
            [
                "panel",
                "--repo-root",
                str(repo),
                "--reminders",
                str(reminders),
                "--now",
                "2026-06-10T00:01:00Z",
            ]
        )
        == 0
    )
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["panels"]["reminders"]["due_pending_count"] == 1
    lanes = {lane["lane"] for lane in data["panels"]["automation_lanes"]}
    assert {"reminders", "scheduler", "notification", "workflow_runner"} <= lanes


def test_plan_routes_reminder_scheduler_without_writes(capfd):
    cli = _load_cli()

    request = "帮我规划飞书提醒和定时任务，但不要真的发送通知"
    assert cli.main(["plan", request]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["write_enabled"] is False
    assert data["requires_confirmation"] is True
    categories = {item["category"] for item in data["recommended_changes"]}
    assert {"reminder", "scheduler", "notification"} <= categories
    assert data["privacy"]["request_body_logged"] is False


def test_health_surfaces_failed_reminders_without_private_body(tmp_path, capfd):
    cli = _load_cli()
    reminders = _make_failed_reminders(tmp_path / "reminders.jsonl")

    assert cli.main(["health", str(reminders), "--now", "2026-06-18T00:00:00Z"]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert data["failed_count"] == 1
    assert data["overdue_pending_count"] == 0
    assert "failed_reminders" in data["risks"]
    sample = data["failed_samples"][0]
    assert sample["id"] == "rtime-daily-digest"
    assert sample["mode"] == "wake"
    assert sample["failed_at"] == "2026-06-17T12:35:49+08:00"
    assert sample["last_error_msg_chars"] == len("TimeoutError: secret-looking detail")
    assert "message" not in sample
    assert "target" not in sample
    assert "last_error" not in sample
    assert data["privacy"]["last_error_message_returned"] is False


def test_reminders_summary_flags_failed_reminders(tmp_path, capfd):
    cli = _load_cli()
    reminders = _make_failed_reminders(tmp_path / "reminders.jsonl")

    assert cli.main(["reminders", str(reminders), "--now", "2026-06-18T00:00:00Z"]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert data["failed_count"] == 1
    assert "failed_reminders" in data["risks"]
    assert data["failed_samples"][0]["id"] == "rtime-daily-digest"


def test_doctor_flags_failed_reminders(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    reminders = _make_failed_reminders(tmp_path / "reminders.jsonl")

    assert cli.main(["doctor", "--repo-root", str(repo), "--reminders", str(reminders)]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert "failed_reminders" in data["risks"]
    assert data["reminder_health"]["failed_count"] == 1
