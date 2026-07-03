# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-hub-connector" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_hub_connector.cli")


def _make_hub_fixture(root: Path) -> Path:
    hub = root / "rtime-hub"
    (hub / ".git").mkdir(parents=True)
    (hub / "projects" / "knowledge-base").mkdir(parents=True)
    (hub / "devices" / "orangepi").mkdir(parents=True)
    (hub / "contacts").mkdir(parents=True)
    (hub / "me").mkdir(parents=True)
    (hub / "AGENTS.md").write_text("# AGENTS.md\n", encoding="utf-8")
    (hub / "README.md").write_text("# rtime-hub\n", encoding="utf-8")
    (hub / "状态.md").write_text("# 当前状态\n", encoding="utf-8")
    (hub / "scratch.md").write_text("# scratch\n", encoding="utf-8")
    (hub / "projects" / "knowledge-base" / "context.md").write_text(
        "# Knowledge Base\n#project active", encoding="utf-8"
    )
    (hub / "projects" / "knowledge-base" / "tasks.md").write_text(
        "# Tasks\n- [ ] process papers", encoding="utf-8"
    )
    (hub / "devices" / "orangepi" / "profile.md").write_text(
        "# OrangePi\nservices live", encoding="utf-8"
    )
    (hub / "devices" / "orangepi" / "services.md").write_text(
        "# Services\nsystemd bridge", encoding="utf-8"
    )
    (hub / "contacts" / "lab.json").write_text(
        json.dumps({"name": "Lab", "api_key": "hidden", "role": "group"}),
        encoding="utf-8",
    )
    (hub / "me" / "profile.md").write_text("# User\n", encoding="utf-8")
    return hub


def test_doctor_reports_hub_and_repo_surfaces(tmp_path, capfd):
    cli = _load_cli()
    hub = _make_hub_fixture(tmp_path)

    assert cli.main(["--repo-root", str(ROOT), "doctor", str(hub)]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["root"] == str(hub)
    assert data["checks"]["agents_md"] == "ok"
    assert data["checks"]["repo_package"] == "ok"


def test_scan_reports_sections_and_privacy(tmp_path, capfd):
    cli = _load_cli()
    hub = _make_hub_fixture(tmp_path)

    assert cli.main(["scan", str(hub), "--sample-limit", "5"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["guidance"]["agents_md"] is True
    assert data["guidance"]["first_reads"][:2] == ["状态.md", "AGENTS.md"]
    assert data["sections"]["projects"]["count"] >= 2
    assert data["sections"]["devices"]["count"] >= 2
    assert data["sections"]["contacts"]["count"] == 1
    contact = data["sections"]["contacts"]["samples"][0]
    assert contact["json"]["sample_keys"] == ["name", "role"]
    assert contact["json"]["sensitive_key_count"] == 1
    assert data["privacy"]["body_text_returned"] is False
    assert data["privacy"]["sensitive_json_key_count"] == 1


def test_panel_and_contacts_are_card_surfaces(tmp_path, capfd):
    cli = _load_cli()
    hub = _make_hub_fixture(tmp_path)

    assert cli.main(["panel", str(hub), "--sample-limit", "2"]) == 0
    panel = json.loads(capfd.readouterr().out)
    assert panel["ok"] is True
    assert "projects" in panel["cards"]
    assert "devices" in panel["cards"]

    assert cli.main(["contacts", str(hub), "--sample-limit", "2"]) == 0
    contacts = json.loads(capfd.readouterr().out)
    assert contacts["ok"] is True
    assert contacts["count"] == 1
    assert contacts["samples"][0]["path"] == "contacts/lab.json"


def test_scan_rejects_missing_root(tmp_path, capfd):
    cli = _load_cli()

    assert cli.main(["scan", str(tmp_path / "missing")]) == 1
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is False
    assert data["errors"] == ["root is not a directory"]
