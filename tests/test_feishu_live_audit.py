# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    spec = importlib.util.spec_from_file_location("feishu_live_audit", ROOT / "scripts" / "feishu-live-audit.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def test_collect_detects_npm_live_bridge(monkeypatch):
    mod = _load_module()

    def fake_run(cmd, **kwargs):
        text = "inactive\n"
        if cmd[:3] == ["systemctl", "--user", "is-active"] and cmd[-1] == "lark-bridge.service":
            text = "active\n"
        if cmd[:2] == ["docker", "ps"]:
            text = ""
        return SimpleNamespace(returncode=0, stdout=text, stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    payload = mod.collect()
    assert payload["live_bridge"] == "npm"
    assert payload["privacy"]["secrets_returned"] is False


def test_collect_detects_mixed_bridge(monkeypatch):
    mod = _load_module()

    def fake_run(cmd, **kwargs):
        if cmd[:3] == ["systemctl", "--user", "is-active"]:
            return SimpleNamespace(returncode=0, stdout="active\n", stderr="")
        if cmd[:2] == ["docker", "ps"]:
            return SimpleNamespace(returncode=0, stdout="feishu-bridge Up 2 hours\n", stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(mod.subprocess, "run", fake_run)
    payload = mod.collect()
    assert payload["live_bridge"] == "mixed"
    assert any("python:" in item for item in payload["evidence"])
