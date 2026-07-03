# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Functional tests for deploy/bin/rtime-contribute (the narrow inbox writer).

Exercised directly via subprocess against a temp brain root so nothing touches a
real brain store. Asserts the inbox-only boundary, sensitive refusal, idempotency,
dry-run, and that the note body is never echoed in the tool's JSON output.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TOOL = ROOT / "deploy" / "bin" / "rtime-contribute"


def _run(args, stdin_obj, brain_root):
    proc = subprocess.run(
        [sys.executable, str(TOOL), "--brain-root", str(brain_root), *args],
        input=json.dumps(stdin_obj, ensure_ascii=False),
        capture_output=True,
        text=True,
        encoding="utf-8",
    )
    assert proc.stdout, proc.stderr
    return json.loads(proc.stdout), proc.returncode


def _brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    (brain / "_inbox").mkdir(parents=True)
    (brain / "knowledge").mkdir()
    return brain


def test_plan_writes_nothing(tmp_path):
    brain = _brain(tmp_path)
    out, rc = _run(["plan", "--json-stdin"], {"title": "测试笔记", "text": "一些研究内容"}, brain)
    assert rc == 0 and out["ok"] and out["op"] == "plan"
    assert out["auto_apply_allowed"] is True
    assert out["confirmation_questions"]
    assert list((brain / "_inbox").rglob("*.md")) == []  # nothing written
    assert "一些研究内容" not in json.dumps(out, ensure_ascii=False)  # body never echoed


def test_stage_writes_into_inbox_only(tmp_path):
    brain = _brain(tmp_path)
    out, rc = _run(
        ["stage", "--json-stdin"],
        {"title": "守敬笔记", "text": "正文内容ABC", "note": "from session 1", "tags": ["a", "b"]},
        brain,
    )
    assert rc == 0 and out["ok"] and out["written"] is True
    p = Path(out["path"])
    assert p.exists()
    assert "_inbox/agent/" in p.as_posix()
    assert "knowledge" not in p.as_posix()
    body = p.read_text(encoding="utf-8")
    assert "正文内容ABC" in body  # body IS written to the inbox file
    assert "from session 1" in body
    assert Path(out["ticket_path"]).exists()  # ticket sidecar
    # the tool's JSON output never echoes the body
    assert "正文内容ABC" not in json.dumps(out, ensure_ascii=False)
    # nothing ever lands in knowledge/
    assert list((brain / "knowledge").rglob("*")) == []


def test_stage_refuses_sensitive(tmp_path):
    brain = _brain(tmp_path)
    out, rc = _run(["stage", "--json-stdin"], {"title": "note", "text": "my api_key=abc123 secret"}, brain)
    assert rc == 0 and out["action"] == "hold" and out["written"] is False
    assert list((brain / "_inbox").rglob("*.md")) == []


def test_stage_is_idempotent(tmp_path):
    brain = _brain(tmp_path)
    payload = {"title": "dup", "text": "same body content"}
    out1, _ = _run(["stage", "--json-stdin"], payload, brain)
    out2, _ = _run(["stage", "--json-stdin"], payload, brain)
    assert out1["written"] is True
    assert out2["action"] == "dedupe" and out2["written"] is False
    assert len(list((brain / "_inbox").rglob("*.md"))) == 1


def test_dry_run_stage_writes_nothing(tmp_path):
    brain = _brain(tmp_path)
    out, rc = _run(["stage", "--json-stdin", "--dry-run"], {"title": "dr", "text": "body"}, brain)
    assert out["ok"] and out["dry_run"] is True and out["written"] is False
    assert list((brain / "_inbox").rglob("*.md")) == []
