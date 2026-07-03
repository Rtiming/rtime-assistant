#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_MANIFEST = ROOT / "module-submit.json"


@dataclass(frozen=True)
class CommandSpec:
    module_id: str
    tier: str
    command: str
    cwd: Path
    env: dict[str, str]


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run per-module submission checks from module-submit.json.",
    )
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST), help="module manifest path")
    parser.add_argument("--list", action="store_true", help="list modules and exit")
    parser.add_argument("--module", action="append", default=[], help="module id to check; repeatable")
    parser.add_argument("--changed", action="store_true", help="select modules with git status changes")
    parser.add_argument(
        "--tier",
        action="append",
        choices=["quick", "docker", "submit", "all"],
        default=[],
        help="check tier; repeatable; default quick",
    )
    parser.add_argument("--dry-run", action="store_true", help="print commands without running them")
    parser.add_argument("--json", action="store_true", help="print machine-readable result JSON")
    parser.add_argument("--report", help="write a markdown report to this path")
    parser.add_argument("--report-dir", help="write a timestamped markdown report under this directory")
    parser.add_argument(
        "--timeout",
        type=int,
        default=int(os.environ.get("MODULE_CHECK_TIMEOUT", "600")),
        help="per-command timeout in seconds; default 600 or MODULE_CHECK_TIMEOUT",
    )
    return parser.parse_args(argv)


def load_manifest(path: Path) -> dict[str, Any]:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SystemExit(f"manifest not found: {path}") from exc
    if data.get("schema_version") != 1:
        raise SystemExit("unsupported module manifest schema_version")
    modules = data.get("modules")
    if not isinstance(modules, list):
        raise SystemExit("module manifest must contain modules[]")
    return data


def modules_by_id(manifest: dict[str, Any]) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    for module in manifest["modules"]:
        module_id = module.get("id")
        if not isinstance(module_id, str) or not module_id:
            raise SystemExit("each module requires a non-empty id")
        if module_id in result:
            raise SystemExit(f"duplicate module id: {module_id}")
        result[module_id] = module
    return result


def print_module_list(manifest: dict[str, Any]) -> None:
    for module in manifest["modules"]:
        module_id = module["id"]
        title = module.get("title", "")
        kind = module.get("kind", "")
        print(f"{module_id}\t{kind}\t{title}")


def git_status_paths(root: Path) -> list[str]:
    completed = subprocess.run(
        ["git", "status", "--porcelain=v1", "--untracked-files=all"],
        cwd=root,
        text=True,
        capture_output=True,
        check=False,
    )
    if completed.returncode != 0:
        raise SystemExit(completed.stderr.strip() or "git status failed")
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if len(line) < 4:
            continue
        payload = line[3:]
        if " -> " in payload:
            paths.extend(item.strip() for item in payload.split(" -> ") if item.strip())
        elif payload.strip():
            paths.append(payload.strip())
    return paths


def path_matches_module(path: str, module: dict[str, Any]) -> bool:
    normalized = path.replace("\\", "/")
    for raw_prefix in module.get("paths", []):
        prefix = str(raw_prefix).replace("\\", "/").rstrip("/")
        if normalized == prefix or normalized.startswith(f"{prefix}/"):
            return True
    return False


def changed_modules(manifest: dict[str, Any], root: Path) -> list[str]:
    dirty_paths = git_status_paths(root)
    selected: list[str] = []
    for module in manifest["modules"]:
        if any(path_matches_module(path, module) for path in dirty_paths):
            selected.append(module["id"])
    return selected


def selected_tiers(raw_tiers: list[str]) -> list[str]:
    tiers = raw_tiers or ["quick"]
    if "all" in tiers:
        return ["quick", "submit", "docker"]
    deduped: list[str] = []
    for tier in tiers:
        if tier not in deduped:
            deduped.append(tier)
    return deduped


def command_specs(module: dict[str, Any], tiers: list[str], root: Path) -> list[CommandSpec]:
    checks = module.get("checks", {})
    specs: list[CommandSpec] = []
    for tier in tiers:
        for item in checks.get(tier, []):
            command = item.get("command")
            if not isinstance(command, str) or not command:
                raise SystemExit(f"module {module['id']} tier {tier} has an invalid command")
            cwd = root / item.get("cwd", ".")
            env = {str(key): str(value) for key, value in item.get("env", {}).items()}
            specs.append(CommandSpec(module["id"], tier, command, cwd, env))
    return specs


def run_command(spec: CommandSpec, timeout: int, dry_run: bool) -> dict[str, Any]:
    record: dict[str, Any] = {
        "module": spec.module_id,
        "tier": spec.tier,
        "cwd": str(spec.cwd),
        "command": spec.command,
    }
    if dry_run:
        print(f"dry-run [{spec.module_id}:{spec.tier}] ({spec.cwd}): {spec.command}")
        return record | {"status": "planned", "exit_code": None, "duration_seconds": 0.0}

    env = os.environ.copy()
    env.update(spec.env)
    started = time.monotonic()
    print(f"run [{spec.module_id}:{spec.tier}] ({spec.cwd}): {spec.command}", flush=True)
    try:
        completed = subprocess.run(
            spec.command,
            cwd=spec.cwd,
            env=env,
            shell=True,
            text=True,
            capture_output=True,
            timeout=timeout,
            check=False,
        )
        duration = time.monotonic() - started
    except subprocess.TimeoutExpired as exc:
        return record | {
            "status": "timeout",
            "exit_code": 124,
            "duration_seconds": round(time.monotonic() - started, 3),
            "stdout_tail": (exc.stdout or "")[-2000:] if isinstance(exc.stdout, str) else "",
            "stderr_tail": (exc.stderr or "")[-2000:] if isinstance(exc.stderr, str) else "",
        }

    if completed.stdout:
        print(completed.stdout, end="" if completed.stdout.endswith("\n") else "\n")
    if completed.stderr:
        print(completed.stderr, end="" if completed.stderr.endswith("\n") else "\n", file=sys.stderr)
    return record | {
        "status": "pass" if completed.returncode == 0 else "fail",
        "exit_code": completed.returncode,
        "duration_seconds": round(duration, 3),
        "stdout_tail": completed.stdout[-2000:],
        "stderr_tail": completed.stderr[-2000:],
    }


def report_path(args: argparse.Namespace, root: Path) -> Path | None:
    if args.report:
        return Path(args.report).expanduser()
    if args.report_dir:
        timestamp = time.strftime("%Y%m%d-%H%M%S")
        return Path(args.report_dir).expanduser() / f"module-submit-check-{timestamp}.md"
    return None


def write_report(path: Path, modules: list[str], tiers: list[str], records: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "# Module Submit Check",
        "",
        f"- modules: {', '.join(modules) if modules else '(none)'}",
        f"- tiers: {', '.join(tiers)}",
        "",
        "| module | tier | status | exit | seconds | command |",
        "|---|---|---:|---:|---:|---|",
    ]
    for record in records:
        lines.append(
            "| {module} | {tier} | {status} | {exit_code} | {duration_seconds} | `{command}` |".format(
                module=record["module"],
                tier=record["tier"],
                status=record["status"],
                exit_code="" if record["exit_code"] is None else record["exit_code"],
                duration_seconds=record["duration_seconds"],
                command=record["command"].replace("|", "\\|"),
            ),
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    manifest_path = Path(args.manifest).expanduser()
    root = manifest_path.resolve().parent
    manifest = load_manifest(manifest_path)
    module_index = modules_by_id(manifest)

    if args.list:
        print_module_list(manifest)
        return 0

    requested = list(args.module)
    if args.changed:
        for module_id in changed_modules(manifest, root):
            if module_id not in requested:
                requested.append(module_id)
    if not requested:
        if args.changed:
            tiers = selected_tiers(args.tier)
            records: list[dict[str, Any]] = []
            output_path = report_path(args, root)
            if output_path:
                write_report(output_path, requested, tiers, records)
                print(f"report: {output_path}")
            result = {
                "ok": True,
                "modules": requested,
                "tiers": tiers,
                "records": records,
            }
            if args.json:
                print(json.dumps(result, ensure_ascii=False, indent=2))
            else:
                print("no changed modules")
            return 0
        raise SystemExit("select at least one --module, or use --changed")

    unknown = [module_id for module_id in requested if module_id not in module_index]
    if unknown:
        raise SystemExit(f"unknown module(s): {', '.join(unknown)}")

    tiers = selected_tiers(args.tier)
    specs: list[CommandSpec] = []
    for module_id in requested:
        specs.extend(command_specs(module_index[module_id], tiers, root))

    if not specs:
        raise SystemExit(f"no commands for modules={requested} tiers={tiers}")

    records = [run_command(spec, args.timeout, args.dry_run) for spec in specs]
    output_path = report_path(args, root)
    if output_path:
        write_report(output_path, requested, tiers, records)
        print(f"report: {output_path}")

    result = {
        "ok": all(record["status"] in {"pass", "planned"} for record in records),
        "modules": requested,
        "tiers": tiers,
        "records": records,
    }
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
