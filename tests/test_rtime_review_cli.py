# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-review" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("rtime_review.cli")


def _make_repo_fixture(root: Path) -> Path:
    repo = root / "repo"
    for tool in (
        "brain-docpack",
        "brain-library",
        "brain-citation",
        "rtime-assistant-runtime",
        "rtime-hub-connector",
        "rtime-context",
        "rtime-profile",
        "rtime-automation",
        "rtime-review",
    ):
        package_name = tool
        module_name = tool.replace("-", "_")
        if tool == "rtime-assistant-runtime":
            module_name = "rtime_assistant_runtime"
        (repo / "packages" / package_name / "src" / module_name).mkdir(parents=True)
        (repo / "skills" / package_name).mkdir(parents=True)
        (repo / "skills" / package_name / "SKILL.md").write_text(
            f"---\nname: {tool}\ndescription: test\n---\n", encoding="utf-8"
        )
        (repo / "plugins" / package_name / ".codex-plugin").mkdir(parents=True)
        (repo / "plugins" / package_name / ".codex-plugin" / "plugin.json").write_text(
            json.dumps({"name": tool}), encoding="utf-8"
        )
        (repo / "plugins" / package_name / ".mcp.json").write_text("{}", encoding="utf-8")
        (repo / "tests").mkdir(parents=True, exist_ok=True)
        (repo / "tests" / f"test_{module_name}_cli.py").write_text("def test_ok(): pass\n", encoding="utf-8")
    (repo / "packages" / "rtime-review" / "src" / "rtime_review" / "cli.py").write_text(
        "# cli\n", encoding="utf-8"
    )
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "review-console.md").write_text("# Review\n", encoding="utf-8")
    schema_src = ROOT / "scripts" / "brain-intake" / "memory_schema.py"
    schema_dest = repo / "scripts" / "brain-intake" / "memory_schema.py"
    schema_dest.parent.mkdir(parents=True)
    schema_dest.write_text(schema_src.read_text(encoding="utf-8"), encoding="utf-8")
    audit = repo / "work" / "standards-audit" / "20260610-000000"
    audit.mkdir(parents=True)
    (audit / "snapshot").mkdir()
    (audit / "result-summary.md").write_text(
        "# Result Summary\n\n## Status\n\npass-with-known-gaps\n\n"
        "## Whole-Project Validation\n\n- ok\n\n"
        "## Independent Review / Rerun\n\n- ok\n\n"
        "## Known Gaps\n\n- gap one\n- gap two\n",
        encoding="utf-8",
    )
    (audit / "review-packet.md").write_text("# Review packet\n", encoding="utf-8")
    return repo


def _make_run_log(path: Path) -> Path:
    records = [
        {
            "run_id": "run-1",
            "timestamp": "2026-06-10T00:00:00Z",
            "entry": "feishu",
            "event": "run_started",
            "permission_mode": "default",
            "api_key": "secret",
        },
        {
            "run_id": "run-1",
            "timestamp": "2026-06-10T00:00:01Z",
            "entry": "feishu",
            "event": "run_completed",
            "status": "ok",
            "memory_candidate_count": 2,
        },
        {
            "run_id": "run-2",
            "timestamp": "2026-06-10T00:01:00Z",
            "entry": "cli",
            "event": "run_failed",
            "status": "failed",
            "failure_reason": "boom",
        },
    ]
    path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")
    return path


def test_doctor_reports_repo_surfaces(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["--repo-root", str(repo), "doctor"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["checks"]["repo_package"] == "ok"
    assert data["checks"]["repo_docs"] == "ok"


def test_repo_root_can_follow_subcommand(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["doctor", "--repo-root", str(repo)]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["repo_root"] == str(repo)


def test_run_logs_summary_redacts_and_counts(tmp_path, capfd):
    cli = _load_cli()
    log_path = _make_run_log(tmp_path / "run.jsonl")

    assert cli.main(["run-logs", str(log_path), "--limit", "2"]) == 0
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is True
    assert data["record_count"] == 3
    assert data["run_count"] == 2
    assert data["memory_candidate_total"] == 2
    assert data["failed_count"] == 1
    assert data["tail"][0]["event"] == "run_completed"
    assert data["privacy"]["redacted"] is True


def test_missing_run_log_is_read_error_not_malformed_jsonl(tmp_path, capfd):
    cli = _load_cli()
    missing = tmp_path / "missing.jsonl"

    assert cli.main(["run-logs", str(missing)]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["exists"] is False
    assert data["malformed_count"] == 0
    assert data["read_error_count"] == 1


def test_audits_and_tooling(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)

    assert cli.main(["--repo-root", str(repo), "audits"]) == 0
    audits = json.loads(capfd.readouterr().out)
    assert audits["count"] == 1
    assert audits["samples"][0]["status"] == "pass-with-known-gaps"
    assert audits["samples"][0]["known_gap_count"] == 2
    assert audits["samples"][0]["archive_type"] == "task_audit"
    assert audits["sampled_type_counts"] == {"task_audit": 1}

    assert cli.main(["--repo-root", str(repo), "tooling"]) == 0
    tooling = json.loads(capfd.readouterr().out)
    assert tooling["ok"] is True
    assert len(tooling["tools"]) == 9


def test_panel_combines_review_surfaces(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    runtime_log = _make_run_log(tmp_path / "runtime.jsonl")
    context_log = _make_run_log(tmp_path / "context.jsonl")

    assert (
        cli.main(
            [
                "--repo-root",
                str(repo),
                "panel",
                "--runtime-log",
                str(runtime_log),
                "--context-log",
                str(context_log),
                "--brain-root",
                str(tmp_path / "brain"),
                "--log-limit",
                "1",
            ]
        )
        == 1
    )
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert "runtime_failures_present" in data["risks"]
    assert data["panels"]["reminder_health"]["failed_count"] == 0
    assert data["panels"]["memory_candidates"]["runtime_total"] == 2
    assert data["panels"]["memory_candidates"]["context_total"] == 2
    assert data["panels"]["failed_runs"]["runtime_failed_count"] == 1
    assert data["panels"]["standards_audit"]["count"] == 1


def test_review_packet_archive_does_not_create_missing_summary_risk(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    packet_only = repo / "work" / "standards-audit" / "20260610-000001"
    packet_only.mkdir(parents=True)
    (packet_only / "review-packet.md").write_text("# Review packet\n", encoding="utf-8")

    assert cli.main(["--repo-root", str(repo), "audits"]) == 0
    audits = json.loads(capfd.readouterr().out)
    assert audits["count"] == 2
    assert audits["missing_result_summary"] == 0
    assert audits["sampled_type_counts"] == {"review_packet": 1, "task_audit": 1}
    assert audits["samples"][0]["archive_type"] == "review_packet"

    assert cli.main(["--repo-root", str(repo), "panel", "--brain-root", str(tmp_path / "brain")]) == 0
    panel = json.loads(capfd.readouterr().out)
    assert panel["ok"] is True
    assert "audit_summary_missing" not in panel["risks"]
    assert "reminder_failures_present" not in panel["risks"]


def test_panel_reads_memory_review_queue(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    brain = tmp_path / "brain"
    review_queue = brain / "memory" / "review-queue"
    review_queue.mkdir(parents=True)
    (review_queue / "candidate.md").write_text(
        """---
type: memory-card
claim: 用户复习时偏好先看页图
scope: assistant-personalization
source: run03-test line 1
observed_at: 2026-06-11
confidence: user-stated
layer: situational
expires: 2026-09-09
supersedes: []
sensitivity: normal
---
候选说明。
""",
        encoding="utf-8",
    )

    assert cli.main(["--repo-root", str(repo), "panel", "--brain-root", str(brain)]) == 0
    data = json.loads(capfd.readouterr().out)
    memory = data["panels"]["memory_candidates"]
    assert memory["review_queue_count"] == 1
    assert memory["type_counts"] == {"memory-card": 1}
    assert memory["schema_ok"] is True


def test_panel_surfaces_reminder_failures(tmp_path, capfd):
    cli = _load_cli()
    repo = _make_repo_fixture(tmp_path)
    brain = tmp_path / "brain"
    (brain / "_system").mkdir(parents=True)
    (brain / "_system" / "reminders.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "id": "rtime-daily-digest",
                        "status": "failed",
                        "due": "2026-06-17T12:30:00+08:00",
                        "repeat": "daily",
                        "mode": "wake",
                        "target": "ou_secret",
                        "message": "daily digest body",
                        "failed_at": "2026-06-17T12:35:49+08:00",
                        "last_error": {"code": None, "msg": "TimeoutError: secret detail"},
                    }
                ),
                json.dumps(
                    {"id": "ok-one", "status": "pending", "due": "2026-06-21T21:00:00+08:00", "target": "ou_x", "message": "x"}
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    assert cli.main(["--repo-root", str(repo), "panel", "--brain-root", str(brain)]) == 1
    data = json.loads(capfd.readouterr().out)

    assert data["ok"] is False
    assert "reminder_failures_present" in data["risks"]
    health = data["panels"]["reminder_health"]
    assert health["failed_count"] == 1
    sample = health["failed_samples"][0]
    assert sample["id"] == "rtime-daily-digest"
    assert sample["mode"] == "wake"
    assert sample["last_error_msg_chars"] == len("TimeoutError: secret detail")
    assert "message" not in sample
    assert "target" not in sample
    assert "last_error" not in sample
    assert health["privacy"]["last_error_message_returned"] is False
