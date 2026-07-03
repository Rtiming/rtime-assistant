# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import os
import subprocess
import sys
import importlib.util
from importlib.machinery import SourceFileLoader
from types import SimpleNamespace
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "deploy" / "bin" / "rtime-reminder-wake-runner"


def load_runner_module():
    loader = SourceFileLoader("rtime_reminder_wake_runner", str(RUNNER))
    spec = importlib.util.spec_from_loader("rtime_reminder_wake_runner", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_wake_runner_direct_echo_returns_metadata_without_prompt_text():
    env = os.environ.copy()
    env["RTIME_REMINDER_WAKE_DIRECT"] = "1"
    env["RTIME_REMINDER_WAKE_ECHO"] = "1"
    env["RTIME_REMINDER_WAKE_ECHO_TEXT"] = "wake result"

    proc = subprocess.run(
        [sys.executable, str(RUNNER), "--inside"],
        input=json.dumps({"prompt": "private wake prompt"}),
        capture_output=True,
        text=True,
        check=True,
        env=env,
    )

    data = json.loads(proc.stdout)
    assert data["ok"] is True
    assert data["text"] == "wake result"
    assert data["prompt_chars"] == len("private wake prompt")
    assert "private wake prompt" not in proc.stdout


def test_wake_runner_outside_passes_allowed_echo_env(monkeypatch, capsys):
    module = load_runner_module()
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        seen["kwargs"] = kwargs
        return subprocess.CompletedProcess(
            cmd,
            0,
            stdout=json.dumps({"ok": True, "text": "wake result", "prompt_chars": 6}),
            stderr="",
        )

    monkeypatch.setenv("RTIME_REMINDER_WAKE_ECHO", "1")
    monkeypatch.setenv("RTIME_REMINDER_WAKE_ECHO_TEXT", "wake result")
    monkeypatch.setattr(module.subprocess, "run", fake_run)

    args = SimpleNamespace(timeout=60, cwd=None, model=None, permission_mode=None)
    assert module._outside(args, {"prompt": "secret"}) == 0

    cmd = seen["cmd"]
    service_index = cmd.index("feishu-bridge")
    assert "-e" in cmd[:service_index]
    assert "RTIME_REMINDER_WAKE_ECHO=1" in cmd[:service_index]
    assert "RTIME_REMINDER_WAKE_ECHO_TEXT=wake result" in cmd[:service_index]
    assert json.loads(capsys.readouterr().out)["ok"] is True


def test_wake_runner_outside_preserves_container_json_error(monkeypatch, capsys):
    module = load_runner_module()

    def fake_run(cmd, **kwargs):
        return subprocess.CompletedProcess(
            cmd,
            1,
            stdout=json.dumps({"ok": False, "error": "RuntimeError: wake failed", "prompt_chars": 6}),
            stderr="",
        )

    monkeypatch.setattr(module.subprocess, "run", fake_run)

    args = SimpleNamespace(timeout=60, cwd=None, model=None, permission_mode=None)
    assert module._outside(args, {"prompt": "secret"}) == 1

    data = json.loads(capsys.readouterr().out)
    assert data["ok"] is False
    assert data["error"] == "RuntimeError: wake failed"
    assert data["returncode"] == 1
    assert "secret" not in json.dumps(data)


def test_wake_runner_adds_bridge_import_path(monkeypatch, tmp_path):
    module = load_runner_module()
    bridge_dir = tmp_path / "apps" / "feishu-bridge"
    bridge_dir.mkdir(parents=True)
    (bridge_dir / "claude_runner.py").write_text("# test marker\n", encoding="utf-8")
    monkeypatch.setattr(module, "BRIDGE_IMPORT_PATHS", [str(bridge_dir)])
    monkeypatch.setattr(module.sys, "path", [item for item in module.sys.path if item != str(bridge_dir)])

    module._ensure_bridge_import_path()

    assert module.sys.path[0] == str(bridge_dir)


def test_wake_runner_wraps_prompt_with_trigger_metadata():
    module = load_runner_module()

    prompt = module._prompt_with_trigger_metadata(
        "private wake prompt",
        {
            "id": "wake-1",
            "due": "2026-06-13T14:00:00+08:00",
            "message": "六级出发提醒",
        },
    )

    assert "current_beijing_datetime:" in prompt
    assert "today_beijing_date:" in prompt
    assert "due_beijing: 2026-06-13T14:00:00+08:00" in prompt
    assert "reminder_id: wake-1" in prompt
    assert "本次计划触发时间（原始due）：2026-06-13T14:00:00+08:00" in prompt
    assert "提醒标题：六级出发提醒" in prompt
    assert prompt.endswith("private wake prompt")


def test_wake_runner_converts_utc_due_to_beijing_metadata():
    module = load_runner_module()

    prompt = module._prompt_with_trigger_metadata(
        "private wake prompt",
        {"id": "wake-utc", "due": "2026-06-17T23:30:00.000Z"},
    )

    assert "due_beijing: 2026-06-18T07:30:00+08:00" in prompt
