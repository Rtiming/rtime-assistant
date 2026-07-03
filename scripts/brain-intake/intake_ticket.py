#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Multi-entry intake ticket helper for brain/_inbox.

This script is intentionally conservative: it creates tickets and optionally
copies source files into an inbox source/date folder. It never writes final
knowledge directories, Zotero, brain-notes, or memory.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import mimetypes
import os
import shutil
import subprocess
import time
from pathlib import Path
from typing import Any

DEFAULT_BRAIN_ROOT = Path.home() / "OrangePi-Store" / "sync" / "brain"
TICKET_SCHEMA = "rtime-intake-ticket-v1"
DEFAULT_REGISTER_CMD = os.environ.get("RTIME_REMINDER_REGISTER", "rtime-reminder-register")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def safe_name(name: str) -> str:
    cleaned = " ".join(name.strip().split())
    for bad in ("/", "\\", ":", "\0"):
        cleaned = cleaned.replace(bad, "_")
    return cleaned or "unnamed"


def classify(path: Path, target_hint: str = "", privacy_hint: str = "") -> dict[str, str]:
    name = path.name.lower()
    hint = f"{target_hint} {privacy_hint}".lower()
    if any(word in name for word in ("公安备案", "icp", "备案", "域名", "证书", "domain", "rtime.site")):
        return {
            "class": "operations-compliance",
            "privacy_hint": privacy_hint or "personal",
            "target_hint": target_hint or "operations/rtime-site/compliance",
            "decision": "hold-sensitive-review",
            "reason": "site compliance or domain asset material may contain personal identity data",
        }
    if any(word in hint for word in ("course", "课程", "课件")) or path.suffix.lower() in {".ppt", ".pptx"}:
        return {
            "class": "course-material",
            "privacy_hint": privacy_hint or "normal",
            "target_hint": target_hint,
            "decision": "plan-course-batch",
            "reason": "course material requires course id/term/type triage before final filing",
        }
    return {
        "class": "general-material",
        "privacy_hint": privacy_hint or "normal",
        "target_hint": target_hint,
        "decision": "inbox-only",
        "reason": "needs normal brain-intake triage",
    }


def build_ticket(
    path: Path,
    *,
    source: str,
    received_at: str,
    inbox_root: Path,
    requested_action: str,
    target_hint: str,
    privacy_hint: str,
) -> dict[str, Any]:
    stat = path.stat()
    mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    classification = classify(path, target_hint, privacy_hint)
    dest_dir = inbox_root / source / received_at[:10]
    dest_name = safe_name(path.name)
    dest = dest_dir / dest_name
    ticket_path = dest.with_suffix(dest.suffix + ".intake.json")
    return {
        "schema": TICKET_SCHEMA,
        "source": source,
        "received_at": received_at,
        "original_name": path.name,
        "sha256": sha256_file(path),
        "size": stat.st_size,
        "mime": mime,
        "source_path": str(path),
        "requested_action": requested_action,
        "privacy_hint": classification["privacy_hint"],
        "target_hint": classification["target_hint"],
        "class": classification["class"],
        "decision": classification["decision"],
        "reason": classification["reason"],
        "status": "planned",
        "inbox_path": str(dest),
        "ticket_path": str(ticket_path),
        "report_path": "",
        "redaction": {
            "ticket_contains_body": False,
            "ticket_contains_secrets": False,
        },
    }


def build_plan(args: argparse.Namespace) -> dict[str, Any]:
    inbox_root = args.inbox_root or (args.brain_root / "_inbox")
    received_at = args.received_at or time.strftime("%Y-%m-%dT%H:%M:%S%z")
    tickets = [
        build_ticket(
            Path(file).expanduser().resolve(),
            source=args.source,
            received_at=received_at,
            inbox_root=inbox_root.expanduser().resolve(),
            requested_action=args.requested_action,
            target_hint=args.target_hint,
            privacy_hint=args.privacy_hint,
        )
        for file in args.file
    ]
    return {
        "schema": "rtime-intake-plan-v1",
        "run_id": args.run_id,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "source": args.source,
        "inbox_root": str(inbox_root),
        "tickets": tickets,
        "summary": {
            "file_count": len(tickets),
            "hold_count": sum(1 for t in tickets if t["decision"].startswith("hold")),
            "inbox_only": True,
        },
    }


def write_plan(plan: dict[str, Any], run_dir: Path) -> Path:
    run_dir.mkdir(parents=True, exist_ok=True)
    out = run_dir / "intake-plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def apply_plan(plan: dict[str, Any], *, approved: bool) -> dict[str, Any]:
    if not approved:
        raise ValueError("refusing to copy inbox files without --approved-plan")
    copied = []
    for ticket in plan.get("tickets", []):
        src = Path(ticket["source_path"])
        dest = Path(ticket["inbox_path"])
        tpath = Path(ticket["ticket_path"])
        dest.parent.mkdir(parents=True, exist_ok=True)
        if dest.exists() and sha256_file(dest) != ticket["sha256"]:
            raise FileExistsError(f"destination exists with different sha256: {dest}")
        if not dest.exists():
            shutil.copy2(src, dest)
        ticket = dict(ticket)
        ticket["status"] = "inbox"
        tpath.write_text(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        copied.append({"source_path": str(src), "inbox_path": str(dest), "ticket_path": str(tpath)})
    return {"ok": True, "copied": copied}


def markdown_report(plan: dict[str, Any], result: dict[str, Any] | None = None) -> str:
    lines = [
        f"# {plan.get('run_id', 'intake')} 多入口入库报告",
        "",
        f"- source: `{plan.get('source')}`",
        f"- inbox_root: `{plan.get('inbox_root')}`",
        f"- file_count: {plan.get('summary', {}).get('file_count', 0)}",
        f"- hold_count: {plan.get('summary', {}).get('hold_count', 0)}",
        "",
        "| file | class | privacy | decision | inbox |",
        "|---|---|---|---|---|",
    ]
    for ticket in plan.get("tickets", []):
        lines.append(
            "| {original_name} | {class_} | {privacy_hint} | {decision} | `{inbox_path}` |".format(
                original_name=ticket["original_name"].replace("|", "\\|"),
                class_=ticket["class"],
                privacy_hint=ticket["privacy_hint"],
                decision=ticket["decision"],
                inbox_path=ticket["inbox_path"],
            )
        )
    if result is not None:
        lines.extend(["", "## Apply Result", "", f"- copied: {len(result.get('copied', []))}"])
    lines.extend(
        [
            "",
            "## Safety",
            "",
            "- 本脚本只写 `_inbox/<source>/<date>/` 和 `.intake.json` ticket。",
            "- 不写最终 `knowledge/`、Zotero、长期记忆或 `brain-notes` 正文。",
            "- 高敏资料报告只保留元数据和候选路径，不摘录正文。",
        ]
    )
    return "\n".join(lines) + "\n"


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def candidate_destination(ticket: dict[str, Any]) -> str:
    hint = (ticket.get("target_hint") or "").strip("/")
    if not hint:
        return "（待分诊定落点）"
    root = "personal-data" if ticket.get("privacy_hint") == "personal" else "knowledge"
    return f"{root}/{hint}/"


def build_notify_message(plan: dict[str, Any], kind: str = "confirm") -> str:
    """Feishu text for intake confirmation/completion. Filenames are allowed —
    this goes to the owner's own reminder channel — but never file bodies."""
    run_id = plan.get("run_id", "intake")
    tickets = plan.get("tickets", [])
    if kind == "done":
        lines = [f"✅ 入库完成（{run_id}，{len(tickets)}个文件）"]
        for i, ticket in enumerate(tickets, 1):
            # finalize rewrites the ticket file; prefer its final_path when present
            current = ticket
            tpath = Path(ticket.get("ticket_path", ""))
            if tpath.is_file():
                try:
                    current = load_json(tpath)
                except (OSError, json.JSONDecodeError):
                    current = ticket
            location = current.get("final_path") or current.get("inbox_path", "")
            lines.append(f"{i}. {current.get('original_name', '?')} → {location}")
        return "\n".join(lines)
    hold = plan.get("summary", {}).get("hold_count", 0)
    lines = [f"📥 入库待确认（{run_id}，{len(tickets)}个文件，敏感hold {hold}个）"]
    for i, ticket in enumerate(tickets, 1):
        lines.append(
            f"{i}. {ticket.get('original_name', '?')}"
            f" [{ticket.get('class')}/{ticket.get('privacy_hint')}]"
            f" → 建议 {candidate_destination(ticket)}"
        )
    lines.append("回复助手「批准入库 " + run_id + "」，或在Mac执行：")
    lines.append("python3 scripts/brain-intake/intake_ticket.py run --plan <plan.json> --approved-plan")
    return "\n".join(lines)


def notify_register_args(message: str, *, register_cmd: str, due: str, target: str) -> list[str]:
    due_value = due or time.strftime("%Y-%m-%dT%H:%M:%S%z")  # past due fires on next timer tick
    args = [register_cmd, "add", "--mode", "notify", "--due", due_value, "--message", message]
    if target:
        args.extend(["--target", target])
    return args


def finalize_ticket(ticket_path: Path, dest_dir: Path, *, approved: bool) -> dict[str, Any]:
    """Move an inbox file to its confirmed final directory inside the same brain root."""
    if not approved:
        raise ValueError("refusing to file inbox content without --approved")
    ticket = load_json(ticket_path)
    if ticket.get("status") != "inbox":
        raise ValueError(f"ticket status is {ticket.get('status')!r}, expected 'inbox' (run apply first)")
    inbox_path = Path(ticket["inbox_path"])
    if not inbox_path.is_file():
        raise FileNotFoundError(f"inbox file missing: {inbox_path}")
    if sha256_file(inbox_path) != ticket["sha256"]:
        raise ValueError(f"inbox file sha256 mismatch: {inbox_path}")
    brain_root = None
    for parent in inbox_path.parents:
        if parent.name == "_inbox":
            brain_root = parent.parent
            break
    if brain_root is None:
        raise ValueError(f"inbox path is not under an _inbox directory: {inbox_path}")
    dest_dir = dest_dir.expanduser().resolve()
    brain_root = brain_root.resolve()
    if not dest_dir.is_relative_to(brain_root):
        raise ValueError(f"destination must stay inside brain root {brain_root}: {dest_dir}")
    if dest_dir.is_relative_to(brain_root / "_inbox"):
        raise ValueError("destination must be a final directory, not _inbox")
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / inbox_path.name
    if dest.exists():
        if sha256_file(dest) != ticket["sha256"]:
            raise FileExistsError(f"destination exists with different sha256: {dest}")
        inbox_path.unlink()
    else:
        shutil.move(str(inbox_path), str(dest))
    ticket["status"] = "filed"
    ticket["final_path"] = str(dest)
    ticket["filed_at"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    ticket_path.write_text(json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return {"ok": True, "final_path": str(dest), "ticket_path": str(ticket_path)}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--brain-root", type=Path, default=DEFAULT_BRAIN_ROOT)
    parser.add_argument("--inbox-root", type=Path)
    parser.add_argument("--run-id", default="run-12")
    sub = parser.add_subparsers(dest="command", required=True)

    p_plan = sub.add_parser("plan")
    p_plan.add_argument("--source", choices=["obsidian", "webdav-upload", "feishu", "cli"], required=True)
    p_plan.add_argument("--file", action="append", required=True)
    p_plan.add_argument("--requested-action", default="inbox")
    p_plan.add_argument("--target-hint", default="")
    p_plan.add_argument("--privacy-hint", default="")
    p_plan.add_argument("--received-at", default="")
    p_plan.add_argument("--run-dir", type=Path, required=True)

    p_run = sub.add_parser("run")
    p_run.add_argument("--plan", type=Path, required=True)
    p_run.add_argument("--approved-plan", action="store_true")
    p_run.add_argument("--report", type=Path)

    p_report = sub.add_parser("report")
    p_report.add_argument("--plan", type=Path, required=True)
    p_report.add_argument("--out", type=Path, required=True)

    p_notify = sub.add_parser("notify", help="send a Feishu confirm/done message via the reminder channel")
    p_notify.add_argument("--plan", type=Path, required=True)
    p_notify.add_argument("--kind", choices=["confirm", "done"], default="confirm")
    p_notify.add_argument("--register-cmd", default=DEFAULT_REGISTER_CMD)
    p_notify.add_argument("--due", default="", help="ISO datetime; default now (fires on next timer tick)")
    p_notify.add_argument("--target", default="", help="Feishu open_id; defaults to register env")
    p_notify.add_argument("--dry-run", action="store_true")

    p_finalize = sub.add_parser("finalize", help="move a confirmed inbox file to its final brain directory")
    p_finalize.add_argument("--ticket", type=Path, action="append", required=True)
    p_finalize.add_argument("--dest-dir", type=Path, required=True)
    p_finalize.add_argument("--approved", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "plan":
        plan = build_plan(args)
        out = write_plan(plan, args.run_dir)
        print(json.dumps({"plan": str(out), **plan["summary"]}, ensure_ascii=False))
        return 0
    if args.command == "run":
        plan = load_json(args.plan)
        result = apply_plan(plan, approved=args.approved_plan)
        if args.report:
            args.report.parent.mkdir(parents=True, exist_ok=True)
            args.report.write_text(markdown_report(plan, result), encoding="utf-8")
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    if args.command == "report":
        plan = load_json(args.plan)
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(markdown_report(plan), encoding="utf-8")
        print(str(args.out))
        return 0
    if args.command == "notify":
        plan = load_json(args.plan)
        message = build_notify_message(plan, kind=args.kind)
        cmd = notify_register_args(message, register_cmd=args.register_cmd, due=args.due, target=args.target)
        if args.dry_run:
            print(json.dumps({"dry_run": True, "message": message, "cmd": cmd[:-1] + ["<message>"]}, ensure_ascii=False, indent=2))
            return 0
        proc = subprocess.run(cmd, capture_output=True, text=True, check=False)
        ok = proc.returncode == 0
        print(json.dumps({"ok": ok, "kind": args.kind, "message_chars": len(message), "register_rc": proc.returncode}, ensure_ascii=False))
        if not ok:
            print(proc.stderr.strip() or proc.stdout.strip())
        return 0 if ok else 1
    if args.command == "finalize":
        results = [finalize_ticket(t, args.dest_dir, approved=args.approved) for t in args.ticket]
        print(json.dumps({"ok": True, "filed": results}, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
