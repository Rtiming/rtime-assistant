# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "brain-intake" / "m9_extract.py"


def _write_jsonl(path: Path, records: list[dict]) -> Path:
    path.write_text("\n".join(json.dumps(record, ensure_ascii=False) for record in records) + "\n", encoding="utf-8")
    return path


def test_m9_extract_plan_and_apply_writes_candidate_and_journal(tmp_path):
    brain = tmp_path / "brain"
    run_dir = tmp_path / "run-03"
    input_log = _write_jsonl(
        tmp_path / "gateway.jsonl",
        [
            {
                "ts": "2026-06-11T12:00:00+0800",
                "entry": "gateway",
                "message_excerpt": "请记住：我复习固体物理时更希望先看页图，再核对公式转写。",
            }
        ],
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan",
            "--brain-root",
            str(brain),
            "--run-dir",
            str(run_dir),
            "--input-log",
            str(input_log),
            "--date",
            "2026-06-11",
            "--source-id",
            "run03-test",
        ],
        capture_output=True,
        text=True,
    )
    assert proc.returncode == 0, proc.stderr
    plan_path = run_dir / "m9-extract-plan.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["summary"]["write_candidate"] == 1
    assert any(action["action"] == "append_journal" for action in plan["actions"])

    apply_proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--apply",
            "--approved-plan",
            str(plan_path),
            "--brain-root",
            str(brain),
            "--run-dir",
            str(run_dir),
            "--input-log",
            str(input_log),
        ],
        capture_output=True,
        text=True,
    )
    assert apply_proc.returncode == 0, apply_proc.stderr
    candidates = list((brain / "memory" / "review-queue").glob("*.md"))
    assert len(candidates) == 1
    text = candidates[0].read_text(encoding="utf-8")
    assert "type: memory-card" in text
    assert "用户复习固体物理时更希望先看页图" in text
    journal = (brain / "memory" / "journal" / "2026-06-11.md").read_text(encoding="utf-8")
    assert "[entry: gateway]" in journal
    assert "[source: run03-test]" in journal


def test_m9_extract_rejects_sensitive_and_noop(tmp_path):
    brain = tmp_path / "brain"
    run_dir = tmp_path / "run-03"
    input_log = _write_jsonl(
        tmp_path / "gateway.jsonl",
        [
            {"message_excerpt": "我的身份证是123，请记住"},
            {"message_excerpt": "今天热力学怎么复习？"},
        ],
    )

    proc = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "--plan",
            "--brain-root",
            str(brain),
            "--run-dir",
            str(run_dir),
            "--input-log",
            str(input_log),
            "--date",
            "2026-06-11",
        ],
        capture_output=True,
        text=True,
    )

    assert proc.returncode == 0, proc.stderr
    plan = json.loads((run_dir / "m9-extract-plan.json").read_text(encoding="utf-8"))
    assert plan["summary"]["write_candidate"] == 0
    assert plan["summary"]["hold"] == 1
    assert plan["summary"]["noop"] == 1
