# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SENDER = ROOT / "apps" / "reminder-sender" / "reminder-sender.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_reminder_sender_dry_run_does_not_leak_body_or_target(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    reminders.write_text(
        json.dumps(
            {
                "id": "r1",
                "due": "2026-06-11T09:30:00+08:00",
                "repeat": "none",
                "message": "private body",
                "target": "ou_private_target",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_REMINDERS_PATH", str(reminders))
    monkeypatch.setenv("RTIME_REMINDER_DRY_RUN", "1")

    result = subprocess.run(
        ["node", str(SENDER)],
        check=True,
        capture_output=True,
        text=True,
    )

    assert "private body" not in result.stdout
    assert "ou_private_target" not in result.stdout
    assert '"message_chars":12' in result.stdout
    assert '"target_set":true' in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_reminder_sender_dry_run_does_not_leak_wake_prompt(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    reminders.write_text(
        json.dumps(
            {
                "id": "wake-1",
                "due": "2026-06-11T09:30:00+08:00",
                "repeat": "none",
                "mode": "wake",
                "message": "wake title",
                "prompt": "private wake prompt",
                "target": "ou_private_target",
                "status": "pending",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_REMINDERS_PATH", str(reminders))
    monkeypatch.setenv("RTIME_REMINDER_DRY_RUN", "1")

    result = subprocess.run(["node", str(SENDER)], check=True, capture_output=True, text=True)

    assert "private wake prompt" not in result.stdout
    assert "ou_private_target" not in result.stdout
    assert '"mode":"wake"' in result.stdout
    assert '"prompt_chars":19' in result.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_reminder_sender_advances_repeat_due_in_beijing_time():
    script = """
const sender = require(process.argv[1]);
const rows = {
  utcDaily: sender.advanceDueBeijing("2026-06-17T23:30:00.000Z", "daily"),
  plusDaily: sender.advanceDueBeijing("2026-06-18T07:30:00+08:00", "daily"),
  weekly: sender.advanceDueBeijing("2026-06-18T07:30:00+08:00", "weekly"),
  hourly: sender.advanceDueBeijing("2026-06-18T07:30:00+08:00", "hourly"),
};
console.log(JSON.stringify(rows));
"""
    result = subprocess.run(
        ["node", "-e", script, str(SENDER)],
        check=True,
        capture_output=True,
        text=True,
    )
    data = json.loads(result.stdout)

    assert data["utcDaily"] == "2026-06-19T07:30:00+08:00"
    assert data["plusDaily"] == "2026-06-19T07:30:00+08:00"
    assert data["weekly"] == "2026-06-25T07:30:00+08:00"
    assert data["hourly"] == "2026-06-18T08:30:00+08:00"
