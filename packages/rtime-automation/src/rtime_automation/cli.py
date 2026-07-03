# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only automation and reminder diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MAX_REQUEST_PREVIEW = 120

REMINDER_REPEAT_VALUES = {"none", "hourly", "daily", "weekly"}
AUTOMATION_TERMS: dict[str, tuple[str, ...]] = {
    "reminder": ("reminder", "remind", "提醒", "闹钟", "到点", "定时"),
    "scheduler": ("scheduler", "schedule", "timer", "cron", "systemd", "调度", "定时任务"),
    "notification": ("notification", "notify", "feishu", "lark", "push", "通知", "飞书", "推送"),
    "workflow": ("workflow", "automation", "runner", "queue", "callback", "自动化", "流程"),
    "runtime_action": ("deploy", "restart", "service", "docker", "部署", "重启", "服务"),
}

JsonObject = dict[str, Any]


def _json_print(data: JsonObject) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _candidate_repo_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_ASSISTANT_ROOT")
    if env_root:
        roots.append(Path(env_root))
    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    roots.extend([PACKAGE_ROOT, *PACKAGE_ROOT.parents])
    return roots


def find_repo_root() -> Path:
    for root in _candidate_repo_roots():
        if (
            (root / "apps" / "reminder-sender" / "reminder-sender.js").is_file()
            and (root / "deploy" / "systemd" / "user" / "reminder.timer").is_file()
            and (root / "docs" / "workflows.md").is_file()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def candidate_reminder_paths() -> list[Path]:
    paths: list[Path] = []
    env_path = os.environ.get("RTIME_REMINDERS_PATH")
    if env_path:
        paths.append(Path(env_path).expanduser())
    paths.extend(
        [
            Path("/mnt/brain/_system/reminders.jsonl"),
            Path.home() / "brain" / "_system" / "reminders.jsonl",
            Path.home() / "OrangePi-Store" / "sync" / "brain" / "_system" / "reminders.jsonl",
        ]
    )
    return _unique_paths(paths)


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve() if path.exists() else path.expanduser()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def default_reminder_path() -> Path:
    for path in candidate_reminder_paths():
        if path.exists():
            return path.resolve()
    return candidate_reminder_paths()[0]


def _parse_now(raw: str | None) -> datetime:
    if not raw:
        return datetime.now(timezone.utc)
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    value = datetime.fromisoformat(text)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _parse_due(raw: Any) -> datetime | None:
    if not isinstance(raw, str) or not raw.strip():
        return None
    text = raw.strip()
    if text.endswith("Z"):
        text = f"{text[:-1]}+00:00"
    try:
        value = datetime.fromisoformat(text)
    except ValueError:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _counter(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def _read_jsonl(path: Path) -> tuple[list[tuple[int, JsonObject]], list[JsonObject]]:
    records: list[tuple[int, JsonObject]] = []
    errors: list[JsonObject] = []
    if not path.exists():
        return records, [{"line": None, "error": "file does not exist"}]
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "error": exc.msg})
                continue
            if not isinstance(loaded, dict):
                errors.append({"line": line_no, "error": "record must be an object"})
                continue
            records.append((line_no, loaded))
    return records, errors


def _reminder_meta(line_no: int, item: JsonObject, now: datetime) -> JsonObject:
    due = _parse_due(item.get("due"))
    status = item.get("status")
    repeat = item.get("repeat") or "none"
    message = item.get("message")
    last_error = item.get("last_error") if isinstance(item.get("last_error"), dict) else None
    error_msg = last_error.get("msg") if last_error else None
    failed = status == "failed"
    is_overdue = bool(status == "pending" and due and due < now)
    return {
        "line": line_no,
        "id": item.get("id") if isinstance(item.get("id"), str) else None,
        "status": status,
        "due": item.get("due") if isinstance(item.get("due"), str) else None,
        "due_valid": due is not None,
        "is_due": bool(status == "pending" and due and due <= now),
        "is_overdue": is_overdue,
        "failed": failed,
        "failed_at": item.get("failed_at") if isinstance(item.get("failed_at"), str) else None,
        "last_error_code": last_error.get("code") if last_error else None,
        "last_error_msg_chars": len(error_msg) if isinstance(error_msg, str) else 0,
        "mode": item.get("mode") if isinstance(item.get("mode"), str) else None,
        "repeat": repeat,
        "repeat_known": repeat in REMINDER_REPEAT_VALUES,
        "has_target": bool(item.get("target")),
        "message_chars": len(message) if isinstance(message, str) else 0,
        "message_returned": False,
        "target_returned": False,
    }


def summarize_reminders(path: Path, *, now: datetime | None = None, sample_limit: int = 10) -> JsonObject:
    now = now or datetime.now(timezone.utc)
    records, errors = _read_jsonl(path)
    metas = [_reminder_meta(line_no, item, now) for line_no, item in records]
    due = [item for item in metas if item["is_due"]]
    pending = [item for item in metas if item["status"] == "pending"]
    missing_target = [item for item in metas if item["status"] == "pending" and not item["has_target"]]
    invalid_due = [item for item in metas if item["status"] == "pending" and not item["due_valid"]]
    unknown_repeat = [item for item in metas if not item["repeat_known"]]
    failed = [item for item in metas if item["failed"]]
    risks: list[str] = []
    if errors:
        risks.append("reminders_read_error")
    if failed:
        risks.append("failed_reminders")
    if invalid_due:
        risks.append("pending_reminders_with_invalid_due")
    if missing_target:
        risks.append("pending_reminders_missing_target")
    if unknown_repeat:
        risks.append("unknown_repeat_values")
    return {
        "ok": path.exists()
        and not errors
        and not failed
        and not invalid_due
        and not missing_target
        and not unknown_repeat,
        "path": str(path),
        "exists": path.exists(),
        "now": now.isoformat(),
        "record_count": len(records),
        "malformed_count": len(errors),
        "errors": errors[:20],
        "status_counts": _counter(item.get("status") for _, item in records),
        "repeat_counts": _counter((item.get("repeat") or "none") for _, item in records),
        "pending_count": len(pending),
        "due_pending_count": len(due),
        "failed_count": len(failed),
        "target_missing_count": len(missing_target),
        "invalid_due_count": len(invalid_due),
        "unknown_repeat_count": len(unknown_repeat),
        "due_samples": due[:sample_limit],
        "failed_samples": failed[:sample_limit],
        "risk_samples": (failed + invalid_due + missing_target + unknown_repeat)[:sample_limit],
        "risks": risks,
        "privacy": {
            "message_text_returned": False,
            "target_values_returned": False,
            "secret_values_read": False,
        },
    }


def reminder_health(path: Path, *, now: datetime | None = None, sample_limit: int = 10) -> JsonObject:
    """Surface failed or stuck reminders so silent failures stop hiding in the JSONL.

    ``failed`` (status == "failed", e.g. a wake task that hit TimeoutError and never
    advanced its due) is the hard signal and flips ``ok``. ``overdue`` (a pending
    reminder whose due is already in the past) is a softer warning that does not flip
    ``ok`` on its own, since it can also mean the sender simply has not run yet.
    Message bodies, targets, and raw error text are never returned.
    """
    now = now or datetime.now(timezone.utc)
    records, errors = _read_jsonl(path)
    metas = [_reminder_meta(line_no, item, now) for line_no, item in records]
    failed = [item for item in metas if item["failed"]]
    overdue = [item for item in metas if item["is_overdue"]]
    risks: list[str] = []
    if errors:
        risks.append("reminders_read_error")
    if failed:
        risks.append("failed_reminders")
    warnings: list[str] = []
    if overdue:
        warnings.append("overdue_pending_reminders")
    return {
        "ok": path.exists() and not errors and not failed,
        "path": str(path),
        "exists": path.exists(),
        "now": now.isoformat(),
        "record_count": len(records),
        "malformed_count": len(errors),
        "errors": errors[:20],
        "failed_count": len(failed),
        "overdue_pending_count": len(overdue),
        "failed_samples": failed[:sample_limit],
        "overdue_samples": overdue[:sample_limit],
        "risks": risks,
        "warnings": warnings,
        "privacy": {
            "message_text_returned": False,
            "target_values_returned": False,
            "last_error_message_returned": False,
        },
    }


def check_surfaces(repo: Path) -> JsonObject:
    sender = repo / "apps" / "reminder-sender" / "reminder-sender.js"
    service = repo / "deploy" / "systemd" / "user" / "reminder.service"
    timer = repo / "deploy" / "systemd" / "user" / "reminder.timer"
    workflow_docs = repo / "docs" / "workflows.md"
    logging_docs = repo / "docs" / "logging-and-audit.md"
    package = repo / "packages" / "rtime-automation" / "src" / "rtime_automation" / "cli.py"
    skill = repo / "skills" / "rtime-automation" / "SKILL.md"
    plugin = repo / "plugins" / "rtime-automation" / ".codex-plugin" / "plugin.json"
    mcp = repo / "plugins" / "rtime-automation" / ".mcp.json"
    sender_text = sender.read_text(encoding="utf-8", errors="ignore") if sender.is_file() else ""
    service_text = service.read_text(encoding="utf-8", errors="ignore") if service.is_file() else ""
    timer_text = timer.read_text(encoding="utf-8", errors="ignore") if timer.is_file() else ""
    checks: JsonObject = {
        "sender_script": "ok" if sender.is_file() else "missing",
        "sender_has_dry_run": "ok" if "--dry-run" in sender_text else "missing",
        "sender_uses_reminders_env": "ok" if "RTIME_REMINDERS_PATH" in sender_text else "missing",
        "sender_uses_feishu_config": "ok" if "RTIME_ASSISTANT_FEISHU_CONFIG" in sender_text else "missing",
        "reminder_service": "ok" if service.is_file() else "missing",
        "service_has_exec_start": "ok" if "ExecStart=" in service_text else "missing",
        "reminder_timer": "ok" if timer.is_file() else "missing",
        "timer_has_install": "ok" if "[Install]" in timer_text else "missing",
        "workflow_docs": "ok" if workflow_docs.is_file() else "missing",
        "logging_docs": "ok" if logging_docs.is_file() else "missing",
        "repo_package": "ok" if package.is_file() else "missing",
        "repo_skill": "ok" if skill.is_file() else "missing",
        "repo_plugin": "ok" if plugin.is_file() else "missing",
        "repo_mcp_config": "ok" if mcp.is_file() else "missing",
    }
    risks = [name for name, value in checks.items() if value != "ok"]
    return {
        "ok": not risks,
        "repo_root": str(repo),
        "checks": checks,
        "risks": risks,
        "paths": {
            "sender_script": str(sender),
            "service": str(service),
            "timer": str(timer),
        },
    }


def doctor(repo: Path | None = None, reminder_path: Path | None = None) -> JsonObject:
    repo_error = ""
    repo_root = repo
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)
    path = reminder_path.expanduser().resolve() if reminder_path else default_reminder_path()
    surfaces = check_surfaces(repo_root) if repo_root else {"ok": False, "risks": ["repo_root_missing"]}
    health = reminder_health(path)
    risks = list(surfaces.get("risks", []))
    if not path.exists():
        risks.append("reminders_file_not_found")
    if health["failed_count"]:
        risks.append("failed_reminders")
    if repo_error:
        risks.append("repo_root_not_found")
    hard_risks = [risk for risk in risks if risk not in {"reminders_file_not_found"}]
    return {
        "ok": not hard_risks,
        "repo_root": str(repo_root) if repo_root else None,
        "reminders_path": str(path),
        "reminders_exists": path.exists(),
        "candidate_reminder_paths": [str(item) for item in candidate_reminder_paths()],
        "surfaces": surfaces,
        "reminder_health": {
            "failed_count": health["failed_count"],
            "overdue_pending_count": health["overdue_pending_count"],
            "failed_samples": health["failed_samples"],
        },
        "risks": risks,
        "repo_error": repo_error,
    }


def panel(
    repo: Path,
    *,
    reminder_path: Path | None = None,
    now: datetime | None = None,
    sample_limit: int = 10,
) -> JsonObject:
    path = reminder_path.expanduser().resolve() if reminder_path else default_reminder_path()
    surfaces = check_surfaces(repo)
    reminders = summarize_reminders(path, now=now, sample_limit=sample_limit)
    risks = list(surfaces["risks"])
    if reminders["risks"]:
        risks.extend(reminders["risks"])
    if not reminders["exists"]:
        risks.append("reminders_file_not_found")
    return {
        "ok": not risks,
        "repo_root": str(repo),
        "panels": {
            "automation_surfaces": surfaces,
            "reminders": reminders,
            "automation_lanes": automation_lanes(),
        },
        "risks": sorted(set(risks)),
    }


def automation_lanes() -> list[JsonObject]:
    return [
        {
            "lane": "reminders",
            "source": "brain/_system/reminders.jsonl",
            "write_policy": "explicit_task_or_review_only",
            "risk": "private_schedule_or_message_content",
        },
        {
            "lane": "scheduler",
            "source": "deploy/systemd/user/reminder.timer and future scheduler config",
            "write_policy": "template_update_plus_deploy_plan",
            "risk": "runtime_behavior_change",
        },
        {
            "lane": "notification",
            "source": "apps/reminder-sender and Feishu config",
            "write_policy": "confirm_before_sending_or_config_change",
            "risk": "external_message_or_secret_use",
        },
        {
            "lane": "workflow_runner",
            "source": "future queue/runner plus run logs",
            "write_policy": "read_only_until_permissions_and_rollback_exist",
            "risk": "automation_can_mutate_files_or_services",
        },
    ]


def _matches(request: str) -> dict[str, list[str]]:
    lowered = request.lower()
    matches: dict[str, list[str]] = {}
    for category, terms in AUTOMATION_TERMS.items():
        found = [term for term in terms if term.lower() in lowered]
        if found:
            matches[category] = found
    return matches


def _request_preview(request: str) -> str:
    return " ".join(request.split())[:MAX_REQUEST_PREVIEW]


def plan_automation(request: str, repo: Path | None = None) -> JsonObject:
    matches = _matches(request)
    recommended: list[JsonObject] = []
    if "reminder" in matches:
        recommended.append(
            {
                "category": "reminder",
                "read_first": ["brain/_system/reminders.jsonl", "apps/reminder-sender/reminder-sender.js"],
                "write_target": "reminder candidate or brain reminder file only after explicit task",
                "permission": "confirm_before_write",
            }
        )
    if "scheduler" in matches:
        recommended.append(
            {
                "category": "scheduler",
                "read_first": ["deploy/systemd/user/reminder.timer", "docs/workflows.md"],
                "write_target": "systemd template or future scheduler config after validation",
                "permission": "deploy_plan_required",
            }
        )
    if "notification" in matches:
        recommended.append(
            {
                "category": "notification",
                "read_first": ["apps/reminder-sender/reminder-sender.js", "docs/logging-and-audit.md"],
                "write_target": "notification adapter config after secret-safe review",
                "permission": "confirm_before_external_message",
            }
        )
    if "workflow" in matches:
        recommended.append(
            {
                "category": "workflow_runner",
                "read_first": ["docs/context-fabric-modules.zh-CN.md", "docs/refactor-roadmap.md"],
                "write_target": "runner design first; implementation only after run logs and rollback",
                "permission": "proposal_first",
            }
        )
    if "runtime_action" in matches:
        recommended.append(
            {
                "category": "runtime_action",
                "read_first": ["docs/deployment.md", "docs/runbook.md", "docs/docker-workflow.md"],
                "write_target": "deployment or service action only after explicit confirmation",
                "permission": "explicit_confirmation_required",
            }
        )
    if not recommended:
        recommended.append(
            {
                "category": "general_automation_review",
                "read_first": ["docs/workflows.md", "docs/logging-and-audit.md"],
                "write_target": "proposal only until automation category is clear",
                "permission": "proposal_first",
            }
        )
    high_risk = {"notification", "runtime_action", "scheduler", "reminder"}
    return {
        "ok": True,
        "repo_root": str(repo) if repo else None,
        "request_preview": _request_preview(request),
        "request_length": len(request),
        "matched_categories": sorted(matches),
        "recommended_changes": recommended,
        "write_enabled": False,
        "requires_confirmation": any(item["category"] in high_risk for item in recommended),
        "next_step": "inspect automation panel and draft a change proposal before writing reminders or runtime config",
        "privacy": {
            "request_body_logged": False,
            "reminder_message_required": False,
            "secret_values_required": False,
        },
    }


def _path_arg(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def _repo_arg(raw: Path | None) -> Path:
    if raw:
        return raw.expanduser().resolve()
    return find_repo_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-automation",
        description="Read-only automation and reminder diagnostics.",
    )
    parser.add_argument("--repo-root", dest="global_repo_root", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_roots(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--repo-root", type=Path, default=None)
        subparser.add_argument("--reminders", help="reminders JSONL path")

    doctor_parser = subparsers.add_parser("doctor", help="check automation surfaces")
    add_roots(doctor_parser)

    reminders_parser = subparsers.add_parser("reminders", help="summarize reminders JSONL")
    reminders_parser.add_argument("path", nargs="?", help="reminders JSONL path")
    reminders_parser.add_argument("--now", help="ISO timestamp for deterministic due checks")
    reminders_parser.add_argument("--sample-limit", type=int, default=10)

    health_parser = subparsers.add_parser("health", help="surface failed or stuck reminders")
    health_parser.add_argument("path", nargs="?", help="reminders JSONL path")
    health_parser.add_argument("--now", help="ISO timestamp for deterministic due checks")
    health_parser.add_argument("--sample-limit", type=int, default=10)

    panel_parser = subparsers.add_parser("panel", help="build automation review panel")
    add_roots(panel_parser)
    panel_parser.add_argument("--now", help="ISO timestamp for deterministic due checks")
    panel_parser.add_argument("--sample-limit", type=int, default=10)

    plan_parser = subparsers.add_parser("plan", help="plan an automation or reminder change")
    plan_parser.add_argument("request", help="automation request")
    plan_parser.add_argument("--repo-root", type=Path, default=None)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_repo = getattr(args, "repo_root", None) or args.global_repo_root

    if args.command == "doctor":
        repo = raw_repo.expanduser().resolve() if raw_repo else None
        data = doctor(repo, _path_arg(args.reminders))
    elif args.command == "reminders":
        path = _path_arg(args.path) or default_reminder_path()
        data = summarize_reminders(path, now=_parse_now(args.now), sample_limit=args.sample_limit)
    elif args.command == "health":
        path = _path_arg(args.path) or default_reminder_path()
        data = reminder_health(path, now=_parse_now(args.now), sample_limit=args.sample_limit)
    elif args.command == "panel":
        try:
            repo = _repo_arg(raw_repo)
        except RuntimeError as exc:
            _json_print({"ok": False, "error": str(exc)})
            return 2
        data = panel(
            repo,
            reminder_path=_path_arg(args.reminders),
            now=_parse_now(args.now),
            sample_limit=args.sample_limit,
        )
    elif args.command == "plan":
        repo = raw_repo.expanduser().resolve() if raw_repo else None
        data = plan_automation(args.request, repo)
    else:  # pragma: no cover - argparse enforces valid commands
        raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
