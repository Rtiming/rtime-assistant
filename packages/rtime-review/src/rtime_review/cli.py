# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only review-console data surface CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_AUDIT_LIMIT = 10
DEFAULT_LOG_LIMIT = 20
DEFAULT_BRAIN_ROOTS = (
    Path("/mnt/brain"),
    Path.home() / "brain",
    Path.home() / "OrangePi-Store" / "sync" / "brain",
)
KNOWN_TOOLS = (
    "brain-docpack",
    "brain-library",
    "brain-citation",
    "rtime-assistant-runtime",
    "rtime-hub-connector",
    "rtime-context",
    "rtime-profile",
    "rtime-automation",
    "rtime-review",
)
SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "app_secret",
    "identity",
    "id_card",
    "address",
    "身份证",
    "地址",
)
STATUS_RE = re.compile(r"^## Status\s*\n+([^\n]+)", re.MULTILINE)
KNOWN_GAP_RE = re.compile(r"^## Known Gaps\s*\n+(.*?)(?:\n## |\Z)", re.MULTILINE | re.DOTALL)

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
            (root / "docs" / "review-console.md").is_file()
            and (root / "packages" / "rtime-review").is_dir()
            and (root / "skills" / "rtime-review").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def _default_repo_root(raw: Path | None = None) -> Path:
    if raw:
        return raw.expanduser().resolve()
    return find_repo_root()


def _default_runtime_log() -> Path:
    raw = os.environ.get("RTIME_ASSISTANT_RUN_LOG")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.local/state/rtime-assistant/run-log.jsonl").expanduser().resolve()


def _default_context_log() -> Path:
    raw = os.environ.get("RTIME_CONTEXT_MCP_RUN_LOG")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.local/state/rtime-assistant/context-mcp.jsonl").expanduser().resolve()


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = "[REDACTED]" if _is_sensitive_key(key_text) else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def _read_jsonl(path: Path, *, limit: int | None = None) -> tuple[list[JsonObject], list[JsonObject]]:
    records: list[JsonObject] = []
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
            records.append(redact(loaded))
    if limit is not None and limit >= 0:
        records = records[-limit:]
    return records, errors


def _counter(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def _candidate_count(record: JsonObject) -> int:
    total = 0
    for key in ("memory_candidate_count", "memory_candidates_count", "memoryCandidatesCount"):
        value = record.get(key)
        if isinstance(value, int):
            total += value
    for key in ("memory_candidates", "memoryCandidates"):
        value = record.get(key)
        if isinstance(value, list):
            total += len(value)
    return total


def summarize_run_log(path: Path, *, limit: int = DEFAULT_LOG_LIMIT) -> JsonObject:
    records, errors = _read_jsonl(path, limit=None)
    tail = records[-limit:] if limit >= 0 else records
    timestamps = [record.get("timestamp") for record in records if record.get("timestamp")]
    run_ids = {record.get("run_id") for record in records if record.get("run_id")}
    memory_candidate_total = sum(_candidate_count(record) for record in records)
    malformed_errors = [error for error in errors if error.get("line") is not None]
    failed = [
        {
            "run_id": record.get("run_id"),
            "timestamp": record.get("timestamp"),
            "entry": record.get("entry"),
            "status": record.get("status"),
            "event": record.get("event"),
            "error": record.get("error") or record.get("failure_reason") or record.get("error_type"),
        }
        for record in records
        if str(record.get("status", "")).lower() in {"failed", "error", "fail"}
        or record.get("error")
        or record.get("failure_reason")
    ]
    return {
        "ok": path.exists() and not errors,
        "path": str(path),
        "exists": path.exists(),
        "record_count": len(records),
        "run_count": len(run_ids),
        "malformed_count": len(malformed_errors),
        "read_error_count": len(errors),
        "errors": errors[:20],
        "events": _counter(record.get("event") for record in records),
        "entries": _counter(record.get("entry") for record in records),
        "statuses": _counter(record.get("status") for record in records),
        "permission_modes": _counter(record.get("permission_mode") for record in records),
        "memory_candidate_total": memory_candidate_total,
        "failed_count": len(failed),
        "failed_samples": failed[-limit:] if limit >= 0 else failed,
        "latest_timestamp": max(timestamps) if timestamps else None,
        "tail": tail,
        "privacy": {
            "redacted": True,
            "body_text_returned": False,
        },
    }


def _audit_dirs(repo: Path) -> list[Path]:
    root = repo / "work" / "standards-audit"
    if not root.is_dir():
        return []
    return sorted([path for path in root.iterdir() if path.is_dir()], reverse=True)


def _bullet_count(text: str) -> int:
    return sum(1 for line in text.splitlines() if line.strip().startswith("- "))


def _parse_result_summary(path: Path) -> JsonObject:
    text = path.read_text(encoding="utf-8", errors="ignore")
    status_match = STATUS_RE.search(text)
    gap_match = KNOWN_GAP_RE.search(text)
    known_gap_text = gap_match.group(1).strip() if gap_match else ""
    return {
        "path": str(path),
        "status": status_match.group(1).strip() if status_match else "unknown",
        "known_gap_count": _bullet_count(known_gap_text),
        "has_snapshot_section": "## Snapshot" in text,
        "has_whole_project_validation": "## Whole-Project Validation" in text,
        "has_review_section": "## Independent Review" in text,
    }


def summarize_audits(repo: Path, *, limit: int = DEFAULT_AUDIT_LIMIT) -> JsonObject:
    samples: list[JsonObject] = []
    missing_result_summary = 0
    type_counts: Counter[str] = Counter()
    dirs = _audit_dirs(repo)
    for directory in dirs[:limit]:
        result_summary = directory / "result-summary.md"
        review_packets = sorted(directory.glob("*review-packet.md"))
        has_result_summary = result_summary.is_file()
        has_snapshot = (directory / "snapshot").exists()
        if has_result_summary or has_snapshot:
            archive_type = "task_audit"
        elif review_packets:
            archive_type = "review_packet"
        else:
            archive_type = "unknown"
        type_counts[archive_type] += 1
        item: JsonObject = {
            "audit_id": directory.name,
            "path": str(directory),
            "archive_type": archive_type,
            "result_summary_exists": has_result_summary,
            "review_packet_count": len(review_packets),
            "snapshot_exists": has_snapshot,
        }
        if has_result_summary:
            item.update(_parse_result_summary(result_summary))
        elif archive_type == "task_audit":
            missing_result_summary += 1
        samples.append(item)
    return {
        "ok": True,
        "repo_root": str(repo),
        "audit_root": str(repo / "work" / "standards-audit"),
        "count": len(dirs),
        "sampled_type_counts": dict(sorted(type_counts.items())),
        "missing_result_summary": missing_result_summary,
        "samples": samples,
    }


def tooling_status(repo: Path) -> JsonObject:
    tools: list[JsonObject] = []
    for tool in KNOWN_TOOLS:
        package_name = tool
        module_name = tool.replace("-", "_")
        if tool == "rtime-assistant-runtime":
            module_name = "rtime_assistant_runtime"
        package = repo / "packages" / package_name
        skill = repo / "skills" / package_name
        plugin = repo / "plugins" / package_name
        mcp = plugin / ".mcp.json"
        tests = sorted((repo / "tests").glob(f"test_{module_name}*.py"))
        tools.append(
            {
                "tool": tool,
                "package_exists": package.is_dir(),
                "skill_exists": (skill / "SKILL.md").is_file(),
                "plugin_exists": (plugin / ".codex-plugin" / "plugin.json").is_file(),
                "mcp_config_exists": mcp.is_file(),
                "test_file_count": len(tests),
            }
        )
    missing = [
        item["tool"]
        for item in tools
        if not (
            item["package_exists"]
            and item["skill_exists"]
            and item["plugin_exists"]
            and item["mcp_config_exists"]
        )
    ]
    return {"ok": not missing, "tools": tools, "missing": missing}


def _default_brain_root(raw: Path | None = None) -> Path | None:
    if raw is not None:
        return raw.expanduser().resolve()
    env = os.environ.get("RTIME_BRAIN_ROOT")
    if env:
        return Path(env).expanduser().resolve()
    for candidate in DEFAULT_BRAIN_ROOTS:
        if candidate.exists():
            return candidate.resolve()
    return DEFAULT_BRAIN_ROOTS[0]


def _load_memory_schema(repo: Path):
    schema_dir = repo / "scripts" / "brain-intake"
    if str(schema_dir) not in sys.path:
        sys.path.insert(0, str(schema_dir))
    try:
        import importlib

        return importlib.import_module("memory_schema")
    except Exception:  # noqa: BLE001 - surfaced as panel data
        return None


def summarize_memory_review_queue(repo: Path, brain_root: Path | None = None) -> JsonObject:
    brain = _default_brain_root(brain_root)
    review_queue = brain / "memory" / "review-queue" if brain else None
    files: list[Path] = []
    if review_queue and review_queue.is_dir():
        files = [p for p in sorted(review_queue.glob("*.md")) if p.name != "README.md"]

    schema = _load_memory_schema(repo)
    errors: dict[str, list[str]] = {}
    warnings: dict[str, list[str]] = {}
    type_counts: Counter[str] = Counter()
    for path in files:
        if schema is None:
            errors[str(path)] = ["memory_schema.py could not be loaded"]
            continue
        card_errors, card_warnings = schema.validate_card(path)
        if card_errors:
            errors[str(path)] = card_errors
        if card_warnings:
            warnings[str(path)] = card_warnings
        fm, parse_error = schema.parse_frontmatter(path.read_text(encoding="utf-8"))
        if not parse_error:
            type_counts[str(fm.get("type") or "unknown")] += 1

    return {
        "brain_root": str(brain) if brain else None,
        "review_queue_path": str(review_queue) if review_queue else None,
        "exists": bool(review_queue and review_queue.is_dir()),
        "review_queue_count": len(files),
        "type_counts": dict(sorted(type_counts.items())),
        "schema_ok": not errors,
        "schema_errors": errors,
        "schema_warning_count": sum(len(values) for values in warnings.values()),
    }


def summarize_reminder_health(brain_root: Path | None = None) -> JsonObject:
    """Surface failed reminders in the review console so silent failures are visible.

    Reads ``<brain>/_system/reminders.jsonl`` shallowly and reports failed reminders
    (status == "failed", e.g. a wake task that hit TimeoutError) without returning
    message bodies, targets, or raw error text. The authoritative diagnostic lives in
    rtime-automation; this is the console roll-up so a stuck reminder stops hiding.
    """
    brain = _default_brain_root(brain_root)
    path = brain / "_system" / "reminders.jsonl" if brain else None
    exists = bool(path and path.is_file())
    failed: list[JsonObject] = []
    read_error = False
    if exists:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
            read_error = True
        for line_no, line in enumerate(lines, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError:
                read_error = True
                continue
            if not isinstance(record, dict) or record.get("status") != "failed":
                continue
            last_error = record.get("last_error") if isinstance(record.get("last_error"), dict) else None
            error_msg = last_error.get("msg") if last_error else None
            failed.append(
                {
                    "line": line_no,
                    "id": record.get("id") if isinstance(record.get("id"), str) else None,
                    "due": record.get("due") if isinstance(record.get("due"), str) else None,
                    "repeat": record.get("repeat") or "none",
                    "mode": record.get("mode") if isinstance(record.get("mode"), str) else None,
                    "failed_at": record.get("failed_at") if isinstance(record.get("failed_at"), str) else None,
                    "last_error_code": last_error.get("code") if last_error else None,
                    "last_error_msg_chars": len(error_msg) if isinstance(error_msg, str) else 0,
                }
            )
    return {
        "brain_root": str(brain) if brain else None,
        "reminders_path": str(path) if path else None,
        "exists": exists,
        "failed_count": len(failed),
        "failed_samples": failed[:20],
        "read_error": read_error,
        "privacy": {
            "message_text_returned": False,
            "target_values_returned": False,
            "last_error_message_returned": False,
        },
    }


def doctor(repo: Path | None = None) -> JsonObject:
    repo_root: Path | None = repo
    repo_error = ""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)
    checks: JsonObject = {
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
        "repo_docs": "missing",
        "audit_root": "missing",
    }
    if repo_root:
        checks["repo_package"] = (
            "ok"
            if (repo_root / "packages" / "rtime-review" / "src" / "rtime_review" / "cli.py").is_file()
            else "missing"
        )
        checks["repo_skill"] = "ok" if (repo_root / "skills" / "rtime-review").is_dir() else "missing"
        checks["repo_plugin"] = "ok" if (repo_root / "plugins" / "rtime-review").is_dir() else "missing"
        checks["repo_docs"] = "ok" if (repo_root / "docs" / "review-console.md").is_file() else "missing"
        checks["audit_root"] = "ok" if (repo_root / "work" / "standards-audit").is_dir() else "missing"
    risks = [name for name, value in checks.items() if value != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": checks["repo_package"] == "ok" and checks["repo_skill"] == "ok",
        "repo_root": str(repo_root) if repo_root else None,
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
    }


def panel(
    repo: Path,
    *,
    runtime_log: Path | None = None,
    context_log: Path | None = None,
    brain_root: Path | None = None,
    audit_limit: int = DEFAULT_AUDIT_LIMIT,
    log_limit: int = DEFAULT_LOG_LIMIT,
) -> JsonObject:
    runtime_log = runtime_log or _default_runtime_log()
    context_log = context_log or _default_context_log()
    runtime = summarize_run_log(runtime_log, limit=log_limit)
    context = summarize_run_log(context_log, limit=log_limit)
    audits = summarize_audits(repo, limit=audit_limit)
    tooling = tooling_status(repo)
    memory_queue = summarize_memory_review_queue(repo, brain_root)
    reminder_health = summarize_reminder_health(brain_root)
    risks: list[str] = []
    if runtime["exists"] and runtime["failed_count"]:
        risks.append("runtime_failures_present")
    if context["exists"] and context["failed_count"]:
        risks.append("context_failures_present")
    if audits["missing_result_summary"]:
        risks.append("audit_summary_missing")
    if tooling["missing"]:
        risks.append("tooling_surface_missing")
    if not memory_queue["schema_ok"]:
        risks.append("memory_review_queue_schema_error")
    if reminder_health["failed_count"]:
        risks.append("reminder_failures_present")
    return {
        "ok": not risks,
        "repo_root": str(repo),
        "panels": {
            "memory_candidates": {
                "runtime_total": runtime["memory_candidate_total"],
                "context_total": context["memory_candidate_total"],
                **memory_queue,
            },
            "failed_runs": {
                "runtime_failed_count": runtime["failed_count"],
                "context_failed_count": context["failed_count"],
                "runtime_samples": runtime["failed_samples"],
                "context_samples": context["failed_samples"],
            },
            "reminder_health": reminder_health,
            "permission_audit": {
                "runtime_permission_modes": runtime["permission_modes"],
                "context_permission_modes": context["permission_modes"],
            },
            "standards_audit": audits,
            "tooling_status": tooling,
            "runtime_log": {
                key: runtime[key]
                for key in (
                    "path",
                    "exists",
                    "record_count",
                    "run_count",
                    "malformed_count",
                    "read_error_count",
                    "events",
                    "entries",
                    "statuses",
                    "latest_timestamp",
                    "privacy",
                )
            },
            "context_log": {
                key: context[key]
                for key in (
                    "path",
                    "exists",
                    "record_count",
                    "run_count",
                    "malformed_count",
                    "read_error_count",
                    "events",
                    "entries",
                    "statuses",
                    "latest_timestamp",
                    "privacy",
                )
            },
        },
        "risks": risks,
    }


def _path_arg(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-review",
        description="Read-only review-console data surfaces.",
    )
    parser.add_argument(
        "--repo-root",
        dest="global_repo_root",
        type=Path,
        default=None,
        help="optional repository root",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_repo_root_option(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--repo-root", type=Path, default=None, help="optional repository root")

    doctor_parser = subparsers.add_parser("doctor", help="check review tool surfaces")
    add_repo_root_option(doctor_parser)

    panel_parser = subparsers.add_parser("panel", help="build a review-console panel summary")
    add_repo_root_option(panel_parser)
    panel_parser.add_argument("--runtime-log", help="runtime JSONL log path")
    panel_parser.add_argument("--context-log", help="context MCP JSONL log path")
    panel_parser.add_argument("--brain-root", type=Path, default=None, help="brain root for memory review queue")
    panel_parser.add_argument("--audit-limit", type=int, default=DEFAULT_AUDIT_LIMIT)
    panel_parser.add_argument("--log-limit", type=int, default=DEFAULT_LOG_LIMIT)

    audits_parser = subparsers.add_parser("audits", help="summarize standards audit archives")
    add_repo_root_option(audits_parser)
    audits_parser.add_argument("--limit", type=int, default=DEFAULT_AUDIT_LIMIT)

    logs_parser = subparsers.add_parser("run-logs", help="summarize one JSONL run log")
    logs_parser.add_argument("path", help="JSONL run log path")
    logs_parser.add_argument("--limit", type=int, default=DEFAULT_LOG_LIMIT)

    tooling_parser = subparsers.add_parser("tooling", help="summarize repository-owned tool surfaces")
    add_repo_root_option(tooling_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_repo_root = getattr(args, "repo_root", None) or args.global_repo_root
    repo_root = raw_repo_root.expanduser().resolve() if raw_repo_root else None

    if args.command == "doctor":
        data = doctor(repo_root)
    else:
        try:
            repo = _default_repo_root(repo_root)
        except RuntimeError as exc:
            _json_print({"ok": False, "errors": [str(exc)]})
            return 1
        if args.command == "panel":
            data = panel(
                repo,
                runtime_log=_path_arg(args.runtime_log),
                context_log=_path_arg(args.context_log),
                brain_root=args.brain_root,
                audit_limit=args.audit_limit,
                log_limit=args.log_limit,
            )
        elif args.command == "audits":
            data = summarize_audits(repo, limit=args.limit)
        elif args.command == "run-logs":
            data = summarize_run_log(Path(args.path).expanduser().resolve(), limit=args.limit)
        elif args.command == "tooling":
            data = tooling_status(repo)
        else:  # pragma: no cover - argparse enforces valid commands
            raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
