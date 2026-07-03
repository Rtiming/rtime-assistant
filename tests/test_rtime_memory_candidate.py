# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bin" / "rtime-memory-candidate"
SCHEMA = ROOT / "scripts" / "brain-intake" / "memory_schema.py"


def run_tool(*args: str, input_text: str | None = None, check: bool = True):
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=input_text,
        capture_output=True,
        text=True,
        check=check,
    )


def test_memory_candidate_writes_schema_valid_review_queue_file(tmp_path):
    brain = tmp_path / "brain"
    review = brain / "memory" / "review-queue"
    result = run_tool(
        "--brain-root",
        str(brain),
        "add",
        "--claim",
        "我复习时希望先看当天计划再开始刷题。",
        "--entry",
        "obsidian",
    )
    data = json.loads(result.stdout)

    assert data["ok"] is True
    assert data["written"] is True
    assert data["claim_chars"] > 0
    assert "我复习" not in result.stdout
    files = list(review.glob("*.md"))
    assert len(files) == 1

    validation = subprocess.run(
        [sys.executable, str(SCHEMA), "validate", str(files[0])],
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(validation.stdout)["ok"] is True


def test_memory_candidate_json_stdin_and_sensitive_hold(tmp_path):
    brain = tmp_path / "brain"
    payload = {
        "claim": "请记住我的 token 是 secret-token-value",
        "entry": "feishu",
        "source": "test",
    }
    result = run_tool(
        "--brain-root",
        str(brain),
        "add",
        "--json-stdin",
        input_text=json.dumps(payload, ensure_ascii=False),
    )
    data = json.loads(result.stdout)

    assert data["ok"] is True
    assert data["action"] == "hold"
    assert data["written"] is False
    assert "secret-token-value" not in result.stdout
    assert not (brain / "memory" / "review-queue").exists()
