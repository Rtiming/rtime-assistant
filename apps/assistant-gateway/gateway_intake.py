# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Intake entry-adapter for the assistant gateway.

Carved out of gateway.py (P6, see docs/maintainability-standards.zh-CN.md §三).
Reuses scripts/brain-intake/intake_ticket.py so every entry adapter produces
byte-identical ticket schemas; writes only _inbox/<source>/… (never final
directories) and fires a best-effort Feishu confirm/received notice. No model
run, no queue slot. Behavior-invariant move.
"""

from __future__ import annotations

import base64
import binascii
import json
import subprocess
import time
import uuid
from pathlib import Path


INTAKE_SOURCES = {"obsidian", "webdav-upload", "feishu", "cli"}
_INTAKE_MODULE = None


def load_intake_module():
    """Import scripts/brain-intake/intake_ticket.py from the deployed tree.

    The gateway reuses its classify()/build_ticket() so every entry adapter
    produces byte-identical ticket schemas."""
    global _INTAKE_MODULE
    if _INTAKE_MODULE is not None:
        return _INTAKE_MODULE
    import importlib.util
    from importlib.machinery import SourceFileLoader

    script = Path(__file__).resolve().parents[2] / "scripts" / "brain-intake" / "intake_ticket.py"
    if not script.is_file():
        raise FileNotFoundError(f"intake_ticket.py not found at {script}")
    loader = SourceFileLoader("rtime_intake_ticket", str(script))
    spec = importlib.util.spec_from_loader(loader.name, loader)
    module = importlib.util.module_from_spec(spec)
    loader.exec_module(module)
    _INTAKE_MODULE = module
    return _INTAKE_MODULE


def process_intake(body: dict, cfg: dict) -> tuple[int, dict]:
    """Entry-adapter contract: write _inbox/<source>/<date>/ + .intake.json
    ticket, never final directories. Sensitive/hold files get a Feishu confirm
    message; everything else a received notice. No model run, no queue slot."""
    name = str(body.get("name") or "").strip()
    encoded = body.get("content_base64")
    if not name or not isinstance(encoded, str) or not encoded:
        return 400, {"ok": False, "error": "name and content_base64 are required"}
    max_bytes = int(cfg.get("intake_max_mb", 64)) * 1024 * 1024
    if len(encoded) * 3 // 4 > max_bytes:
        return 413, {"ok": False, "error": f"file exceeds {cfg.get('intake_max_mb', 64)}MB intake limit"}
    try:
        data = base64.b64decode(encoded, validate=True)
    except (binascii.Error, ValueError):
        return 400, {"ok": False, "error": "content_base64 is not valid base64"}
    if len(data) > max_bytes:
        return 413, {"ok": False, "error": f"file exceeds {cfg.get('intake_max_mb', 64)}MB intake limit"}
    source = str(body.get("source") or "obsidian")
    if source not in INTAKE_SOURCES:
        return 400, {"ok": False, "error": f"source must be one of {sorted(INTAKE_SOURCES)}"}
    try:
        mod = load_intake_module()
    except (FileNotFoundError, ImportError) as exc:
        return 503, {"ok": False, "error": f"intake module unavailable: {exc}"}

    brain_root = Path(cfg["brain_root"])
    inbox_root = brain_root / "_inbox"
    tmp_dir = inbox_root / f".tmp-intake-{uuid.uuid4().hex[:8]}"
    tmp_path = tmp_dir / mod.safe_name(name)
    try:
        tmp_dir.mkdir(parents=True, exist_ok=True)
        tmp_path.write_bytes(data)
        ticket = mod.build_ticket(
            tmp_path,
            source=source,
            received_at=time.strftime("%Y-%m-%dT%H:%M:%S%z"),
            inbox_root=inbox_root,
            requested_action=str(body.get("requested_action") or "inbox"),
            target_hint=str(body.get("target_hint") or ""),
            privacy_hint=str(body.get("privacy_hint") or ""),
        )
        dest = Path(ticket["inbox_path"])
        if dest.exists():
            if mod.sha256_file(dest) != ticket["sha256"]:
                return 409, {"ok": False, "error": f"inbox already has a different file named {dest.name}"}
        else:
            dest.parent.mkdir(parents=True, exist_ok=True)
            tmp_path.replace(dest)
        ticket["status"] = "inbox"
        ticket["source_path"] = f"(uploaded via {source})"
        Path(ticket["ticket_path"]).write_text(
            json.dumps(ticket, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
    finally:
        try:
            if tmp_path.exists():
                tmp_path.unlink()
            if tmp_dir.exists():
                tmp_dir.rmdir()
        except OSError:
            pass

    needs_confirm = ticket["decision"].startswith("hold") or ticket["privacy_hint"] == "personal"
    candidate = mod.candidate_destination(ticket)
    if needs_confirm:
        message = (
            f"📥 新文件待确认：{ticket['original_name']}"
            f"［{ticket['class']}/{ticket['privacy_hint']}］→ 建议 {candidate}。"
            f"已暂存收件箱，回复助手确认后归位。"
        )
    else:
        message = f"📥 已入收件箱：{ticket['original_name']} → _inbox/{source}/（候选 {candidate}）"
    notify = _send_intake_notify(message, cfg, ticket["sha256"])

    public = {
        key: ticket[key]
        for key in ("original_name", "class", "privacy_hint", "decision", "sha256", "size", "inbox_path", "ticket_path")
    }
    return 200, {"ok": True, "ticket": public, "needs_confirm": needs_confirm, "notify": notify}


def _send_intake_notify(message: str, cfg: dict, sha: str) -> str:
    target = cfg.get("notify_target") or ""
    register = Path(str(cfg.get("reminder_register") or "")).expanduser()
    if not target or not register.is_file():
        return "skipped"
    cmd = [
        str(register),
        "add",
        "--mode",
        "notify",
        "--due",
        time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "--message",
        message,
        "--target",
        target,
        "--id",
        f"intake-{int(time.time()) % 100000:05d}-{sha[:6]}",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=10, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return "failed"
    return "sent" if proc.returncode == 0 else "failed"
