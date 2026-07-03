# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]
CONSOLIDATE = REPO_ROOT / "scripts" / "brain-intake" / "m9_consolidate.py"
REPLAY = REPO_ROOT / "scripts" / "brain-intake" / "m9_replay.py"


def _card(
    path: Path,
    claim: str,
    *,
    confidence: str = "user-stated",
    sensitivity: str = "normal",
    scope: str = "assistant-personalization",
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "type: memory-card",
                f"claim: {json.dumps(claim, ensure_ascii=False)}",
                f"scope: {scope}",
                "source: test",
                "observed_at: 2026-06-11",
                f"confidence: {confidence}",
                "layer: situational",
                "expires: 2026-09-09",
                "supersedes: []",
                f"sensitivity: {sensitivity}",
                "unlock_hints: [memory-loop]",
                "access: local-only",
                "---",
                "fixture",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _hypothesis(path: Path, claim: str) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "type: hypothesis",
                f"claim: {json.dumps(claim, ensure_ascii=False)}",
                "source: test",
                "observed_at: 2026-06-11",
                "status: testing",
                "confirmations: 1",
                "sensitivity: normal",
                "---",
                "fixture",
                "",
            ]
        ),
        encoding="utf-8",
    )
    return path


def _run(args: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(args, capture_output=True, text=True, env=env)


def test_nightly_auto_merge_gate_and_replay(tmp_path):
    brain = tmp_path / "brain"
    run_dir = tmp_path / "run-05"
    state_dir = tmp_path / "state"
    good = _card(brain / "memory" / "review-queue" / "good.md", "用户希望报告先给结论再列证据")
    _card(
        brain / "memory" / "review-queue" / "assistant.md",
        "助手以后必须自动修改系统提示",
        scope="assistant-behavior",
    )
    _card(brain / "memory" / "review-queue" / "sensitive.md", "用户手机号是123", sensitivity="sensitive")

    proc = _run(
        [
            sys.executable,
            str(CONSOLIDATE),
            "--plan",
            "--mode",
            "nightly",
            "--brain-root",
            str(brain),
            "--run-dir",
            str(run_dir),
            "--state-dir",
            str(state_dir),
            "--date",
            "2026-06-11",
        ]
    )
    assert proc.returncode == 0, proc.stderr
    plan_path = run_dir / "m9-nightly-plan-2026-06-11.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["summary"]["auto_merge"] == 1
    assert any(a["action"] == "keep_in_review_queue" and "assistant-behavior" in a["reason"] for a in plan["actions"])
    assert any(a["action"] == "keep_in_review_queue" and "sensitivity-not-normal" in a["reason"] for a in plan["actions"])

    apply_proc = _run(
        [
            sys.executable,
            str(CONSOLIDATE),
            "--apply",
            "--mode",
            "nightly",
            "--approved-plan",
            str(plan_path),
        ]
    )
    assert apply_proc.returncode == 0, apply_proc.stderr
    copied = brain / "memory" / "cards" / good.name
    assert copied.exists()
    assert good.exists(), "review-queue candidate must be preserved"
    audit = state_dir / "audit.jsonl"
    events = [json.loads(line) for line in audit.read_text(encoding="utf-8").splitlines()]
    auto = [row for row in events if row["action"] == "auto_merge"]
    assert len(auto) == 1
    assert auto[0]["gate"] == {
        "user_stated": True,
        "no_conflict": True,
        "sensitivity_normal": True,
        "non_assistant_behavior": True,
    }

    replay_plan_proc = _run(
        [
            sys.executable,
            str(REPLAY),
            "--plan",
            "--brain-root",
            str(brain),
            "--run-dir",
            str(run_dir),
            "--audit-log",
            str(audit),
            "--date",
            "2026-06-11",
        ]
    )
    assert replay_plan_proc.returncode == 0, replay_plan_proc.stderr
    replay_plan = run_dir / "m9-replay-plan.json"
    replay_apply = _run(
        [
            sys.executable,
            str(REPLAY),
            "--apply",
            "--approved-plan",
            str(replay_plan),
            "--audit-log",
            str(audit),
        ]
    )
    assert replay_apply.returncode == 0, replay_apply.stderr
    assert not copied.exists()
    assert (brain / "memory" / "_archive" / "replay-rollback" / "2026-06-11" / good.name).exists()
    assert good.exists()


def test_nightly_same_day_rerun_is_idempotent(tmp_path):
    brain = tmp_path / "brain"
    run_dir = tmp_path / "run-05"
    state_dir = tmp_path / "state"
    _card(brain / "memory" / "review-queue" / "good.md", "用户希望先读错误摘要")
    base_args = [
        sys.executable,
        str(CONSOLIDATE),
        "--mode",
        "nightly",
        "--brain-root",
        str(brain),
        "--run-dir",
        str(run_dir),
        "--state-dir",
        str(state_dir),
        "--date",
        "2026-06-11",
    ]

    assert _run(base_args[:2] + ["--plan"] + base_args[2:]).returncode == 0
    plan_path = run_dir / "m9-nightly-plan-2026-06-11.json"
    assert _run(base_args[:2] + ["--apply", "--approved-plan", str(plan_path)] + base_args[2:]).returncode == 0
    second = _run(base_args[:2] + ["--plan"] + base_args[2:])
    assert second.returncode == 0, second.stderr
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["summary"]["auto_merge"] == 0
    assert plan["summary"]["noop_existing"] >= 1


def test_weekly_report_and_reminder_are_idempotent(tmp_path, monkeypatch):
    brain = tmp_path / "brain"
    run_dir = tmp_path / "run-05"
    state_dir = tmp_path / "state"
    reminders = tmp_path / "reminders.jsonl"
    _card(brain / "memory" / "cards" / "one.md", "用户希望最终报告包含验收矩阵")
    _hypothesis(brain / "memory" / "hypotheses" / "maybe.md", "用户可能偏好命令行优先")
    failed = tmp_path / "failed-queries.jsonl"
    failed.write_text(json.dumps({"query_excerpt": "run-05 gateway no source"}, ensure_ascii=False) + "\n", encoding="utf-8")
    monkeypatch.setenv("RTIME_REMINDER_DEFAULT_TARGET", "ou_test_target")

    args = [
        sys.executable,
        str(CONSOLIDATE),
        "--mode",
        "weekly",
        "--brain-root",
        str(brain),
        "--run-dir",
        str(run_dir),
        "--state-dir",
        str(state_dir),
        "--date",
        "2026-06-14",
        "--reminders-path",
        str(reminders),
        "--failed-queries",
        str(failed),
    ]
    assert _run(args[:2] + ["--plan"] + args[2:]).returncode == 0
    plan_path = run_dir / "m9-weekly-plan-2026-06-14.json"
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    assert plan["summary"]["hold"] == 0
    append_actions = [a for a in plan["actions"] if a["action"] == "append_weekly_reminder"]
    assert append_actions
    assert "target" not in append_actions[0]["record"]

    assert _run(args[:2] + ["--apply", "--approved-plan", str(plan_path)] + args[2:]).returncode == 0
    report = (run_dir / "weekly-report-draft.md").read_text(encoding="utf-8")
    assert "本周学到1件事+1条待确认" in report
    records = [json.loads(line) for line in reminders.read_text(encoding="utf-8").splitlines()]
    assert len(records) == 1
    assert records[0]["repeat"] == "none"

    assert _run(args[:2] + ["--plan"] + args[2:]).returncode == 0
    second = json.loads(plan_path.read_text(encoding="utf-8"))
    assert second["summary"]["noop_existing"] >= 1
