# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bin" / "rtime-context-source"


def run_tool(*args: str, check: bool = True):
    result = subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        capture_output=True,
        text=True,
        check=check,
    )
    return result


def test_context_source_add_check_list_deactivate(tmp_path):
    brain = tmp_path / "brain"
    source = brain / "_system" / "plan.md"
    source.parent.mkdir(parents=True)
    source.write_text("study plan body", encoding="utf-8")
    manifest = brain / "_system" / "rtime-context-sources.jsonl"

    add = run_tool(
        "--brain-root",
        str(brain),
        "--manifest",
        str(manifest),
        "add",
        "--id",
        "study-plan",
        "--kind",
        "study-plan",
        "--title",
        "Study Plan",
        "--source-path",
        "_system/plan.md",
        "--tags",
        "复习,study",
        "--priority",
        "80",
    )
    add_data = json.loads(add.stdout)
    assert add_data["ok"] is True
    assert add_data["source"]["path"] == "_system/plan.md"
    assert "study plan body" not in add.stdout

    check = json.loads(
        run_tool("--brain-root", str(brain), "--manifest", str(manifest), "check").stdout
    )
    assert check["ok"] is True
    assert check["active_count"] == 1

    listed = json.loads(
        run_tool("--brain-root", str(brain), "--manifest", str(manifest), "list").stdout
    )
    assert listed["items"][0]["id"] == "study-plan"

    deactivated = json.loads(
        run_tool(
            "--brain-root",
            str(brain),
            "--manifest",
            str(manifest),
            "deactivate",
            "--id",
            "study-plan",
        ).stdout
    )
    assert deactivated["changed"] == 1
    inactive = json.loads(
        run_tool("--brain-root", str(brain), "--manifest", str(manifest), "list").stdout
    )
    assert inactive["items"][0]["status"] == "inactive"


def test_context_source_blocks_personal_data_and_duplicates(tmp_path):
    brain = tmp_path / "brain"
    secret = brain / "personal-data" / "secret.md"
    secret.parent.mkdir(parents=True)
    secret.write_text("secret", encoding="utf-8")
    manifest = brain / "_system" / "rtime-context-sources.jsonl"
    (brain / "_system").mkdir(parents=True, exist_ok=True)
    (brain / "_system" / "ok.md").write_text("ok", encoding="utf-8")

    blocked = run_tool(
        "--brain-root",
        str(brain),
        "--manifest",
        str(manifest),
        "add",
        "--id",
        "secret",
        "--kind",
        "preference",
        "--title",
        "Secret",
        "--source-path",
        "personal-data/secret.md",
        check=False,
    )
    assert blocked.returncode != 0
    assert "personal-data" in blocked.stderr

    run_tool(
        "--brain-root",
        str(brain),
        "--manifest",
        str(manifest),
        "add",
        "--id",
        "ok",
        "--kind",
        "note",
        "--title",
        "OK",
        "--source-path",
        "_system/ok.md",
    )
    duplicate = run_tool(
        "--brain-root",
        str(brain),
        "--manifest",
        str(manifest),
        "add",
        "--id",
        "ok",
        "--kind",
        "note",
        "--title",
        "OK",
        "--source-path",
        "_system/ok.md",
        check=False,
    )
    assert duplicate.returncode != 0
    assert "duplicate source id" in duplicate.stderr
