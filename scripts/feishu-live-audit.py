#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only live Feishu bridge audit.

Outputs non-secret JSON describing which bridge appears active. It does not
read env files, print tokens, restart services, or contact Feishu.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import time
from typing import Any


SERVICES = ("lark-bridge.service", "feishu-bridge-python.service")
SENSITIVE_QUERY = re.compile(r"(?i)(access_key|ticket)=([^&\s]+)")


def _run(cmd: list[str], timeout: float = 8) -> dict[str, Any]:
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"ok": False, "cmd": cmd[:3], "returncode": None, "stdout": "", "stderr": type(exc).__name__}
    return {
        "ok": proc.returncode == 0,
        "cmd": cmd[:3],
        "returncode": proc.returncode,
        "stdout": proc.stdout,
        "stderr": proc.stderr,
    }


def _redact(text: str, limit: int = 4000) -> str:
    return SENSITIVE_QUERY.sub(lambda match: f"{match.group(1)}=REDACTED", text or "")[:limit]


def collect(include_journal: bool = False) -> dict[str, Any]:
    service_states: dict[str, str] = {}
    for service in SERVICES:
        result = _run(["systemctl", "--user", "is-active", service])
        service_states[service] = result["stdout"].strip() or ("error" if not result["ok"] else "unknown")

    units = _run(["systemctl", "--user", "list-units", "--type=service", "--all", "--no-legend"])
    docker = _run(["docker", "ps", "--format", "{{.Names}} {{.Status}} {{.Ports}}"])
    ports = _run(["ss", "-ltnp"])

    evidence: list[str] = []
    if service_states.get("lark-bridge.service") == "active":
        evidence.append("npm:lark-bridge.service active")
    if service_states.get("feishu-bridge-python.service") == "active":
        evidence.append("python:feishu-bridge-python.service active")
    docker_lines = [line for line in docker["stdout"].splitlines() if re.search(r"feishu|lark", line, re.I)]
    if docker_lines:
        evidence.append("docker:feishu/lark container visible")

    active_kinds = {item.split(":", 1)[0] for item in evidence}
    if not evidence:
        live_bridge = "unknown-or-stopped"
    elif len(active_kinds) > 1:
        live_bridge = "mixed"
    else:
        live_bridge = next(iter(active_kinds))

    payload: dict[str, Any] = {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "live_bridge": live_bridge,
        "evidence": evidence,
        "service_states": service_states,
        "units_filtered": [
            line for line in units["stdout"].splitlines()
            if re.search(r"feishu|lark|assistant|rtime", line, re.I)
        ][:80],
        "docker_filtered": docker_lines[:80],
        "ports_filtered": [
            line for line in ports["stdout"].splitlines()
            if re.search(r"8765|8000|8080|9981|3000|3001", line)
        ][:80],
        "privacy": {
            "env_files_read": False,
            "secrets_returned": False,
            "message_bodies_returned": False,
            "targets_returned": False,
        },
    }
    if include_journal:
        journals = {}
        for service in SERVICES:
            result = _run(["journalctl", "--user", "-u", service, "-n", "80", "--no-pager"], timeout=12)
            journals[service] = _redact(result["stdout"])
        payload["recent_journal_sanitized"] = journals
    return payload


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--include-journal", action="store_true")
    args = parser.parse_args(argv)
    print(json.dumps(collect(include_journal=args.include_journal), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
