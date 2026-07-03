# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bin" / "rtime-reminder-register"


def _run(*args: str, env: dict[str, str] | None = None) -> dict:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=True,
        capture_output=True,
        text=True,
        env=env,
    )
    return json.loads(result.stdout)


def test_register_add_list_cancel_roundtrip(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--due",
        "2026-06-11T09:30:00+08:00",
        "--message",
        "private body",
        "--id",
        "r1",
    )

    assert added["ok"] is True
    assert added["id"] == "r1"
    assert added["message_chars"] == len("private body")
    assert added["privacy"]["message_text_returned"] is False
    assert reminders.exists()

    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["status"] == "pending"
    assert stored[0]["target"] == "ou_test_user"
    assert stored[0]["message"] == "private body"

    listed = _run("--path", str(reminders), "list", "--status", "pending")
    assert listed["count"] == 1
    assert listed["items"][0]["id"] == "r1"
    assert "message" not in listed["items"][0]
    assert "target" not in listed["items"][0]

    cancelled = _run("--path", str(reminders), "cancel", "--id", "r1")
    assert cancelled["ok"] is True
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["status"] == "cancelled"


def test_register_accepts_naive_due_as_beijing_time(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--due",
        "2026-06-11T09:30:00",
        "--message",
        "x",
    )

    assert added["due"] == "2026-06-11T09:30:00+08:00"


def test_register_converts_utc_due_to_beijing_time(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--due",
        "2026-06-17T23:30:00.000Z",
        "--message",
        "x",
    )

    assert added["due"] == "2026-06-18T07:30:00+08:00"


def test_register_lists_failed_without_private_fields(tmp_path):
    reminders = tmp_path / "reminders.jsonl"
    reminders.write_text(
        json.dumps(
            {
                "id": "failed-1",
                "due": "2026-06-11T09:30:00+08:00",
                "repeat": "none",
                "message": "private body",
                "target": "ou_private_target",
                "status": "failed",
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    listed = _run("--path", str(reminders), "list", "--status", "failed")

    assert listed["count"] == 1
    assert listed["items"][0]["id"] == "failed-1"
    assert listed["items"][0]["message_chars"] == len("private body")
    assert "message" not in listed["items"][0]
    assert "target" not in listed["items"][0]


def test_register_wake_mode_stores_prompt_but_returns_only_metadata(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--mode",
        "wake",
        "--due",
        "2026-06-11T09:30:00+08:00",
        "--message",
        "wake title",
        "--prompt",
        "private wake prompt",
        "--cwd",
        "/mnt/brain",
    )

    assert added["ok"] is True
    assert added["mode"] == "wake"
    assert added["message_chars"] == len("wake title")
    assert added["prompt_chars"] == len("private wake prompt")
    assert added["privacy"]["prompt_text_returned"] is False
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["mode"] == "wake"
    assert stored[0]["prompt"] == "private wake prompt"
    assert stored[0]["cwd"] == "/mnt/brain"

    listed = _run("--path", str(reminders), "list", "--status", "pending")
    assert listed["items"][0]["mode"] == "wake"
    assert listed["items"][0]["prompt_chars"] == len("private wake prompt")
    assert "prompt" not in listed["items"][0]


def test_register_wake_mode_without_prompt_generates_self_contained_prompt(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--mode",
        "wake",
        "--due",
        "2026-06-13T14:00:00+08:00",
        "--message",
        "六级出发提醒：14:30报到，东区第二教学楼2407",
    )

    assert added["ok"] is True
    assert added["mode"] == "wake"
    assert added["prompt_chars"] > added["message_chars"]
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    prompt = stored[0]["prompt"]
    assert "定时唤醒提醒任务" in prompt
    assert "登记时计划触发时间：2026-06-13T14:00:00+08:00" in prompt
    assert "不要只复述标题" in prompt
    assert "六级出发提醒" in prompt


def test_register_infers_wake_when_prompt_given_without_mode(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--due",
        "2026-06-22T08:00:00+08:00",
        "--message",
        "考前提醒",
        "--prompt",
        "到点结合考期提醒用户复习重点",
        "--id",
        "w-infer",
    )

    assert added["ok"] is True
    assert added["mode"] == "wake"
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["mode"] == "wake"
    assert stored[0]["prompt"] == "到点结合考期提醒用户复习重点"


def test_register_defaults_notify_without_prompt_or_mode(tmp_path, monkeypatch):
    reminders = tmp_path / "reminders.jsonl"
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_user")

    added = _run(
        "--path",
        str(reminders),
        "add",
        "--due",
        "2026-06-22T08:00:00+08:00",
        "--message",
        "喝水",
        "--id",
        "n-default",
    )

    assert added["ok"] is True
    stored = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert stored[0]["mode"] == "notify"
    assert "prompt" not in stored[0]
