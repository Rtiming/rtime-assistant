# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""``rtime-jobs`` command line: submit / get / list / worker / doctor.

Every subcommand prints a single JSON object on stdout, so the library gateway
can dispatch to it the same way it dispatches to the other read CLIs (a status
poll is ``rtime-jobs get <id>``; the gateway never runs the worker).

The chat entry only ever reaches ``get`` / ``list`` (read) and — via the narrow
``deploy/bin/rtime-jobs-submit`` write tool — ``submit``. ``worker`` is operated
out of band (systemd/cron/manual); it is intentionally not a gateway method.
"""

from __future__ import annotations

import argparse
import json
import sys
from typing import Any, Sequence

from .handlers import HANDLERS, known_types
from .runner import run_pending, worker_loop
from .schema import ALL_STATUSES
from .store import JobStore

JsonObject = dict[str, Any]


def _print(data: JsonObject) -> int:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))
    return 0 if data.get("ok", False) else 1


def _store(args: argparse.Namespace) -> JobStore:
    return JobStore(getattr(args, "db", None) or None)


def _read_params(args: argparse.Namespace) -> JsonObject:
    """Resolve job params from ``--params`` (JSON string) or ``--params-stdin``.

    Stdin keeps params off argv (and out of the gateway audit / process listing),
    mirroring how lib.contribute / memory-candidate pass their bodies.
    """
    raw: str | None
    if getattr(args, "params_stdin", False):
        raw = sys.stdin.read()
    else:
        raw = getattr(args, "params", None)
    if not raw or not raw.strip():
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--params must be valid JSON: {exc.msg}")
    if not isinstance(value, dict):
        raise SystemExit("--params must be a JSON object")
    return value


def cmd_submit(args: argparse.Namespace) -> int:
    job_type = args.type
    if job_type not in HANDLERS and not args.allow_unknown_type:
        return _print(
            {
                "ok": False,
                "error": f"unknown job type: {job_type}",
                "known_types": known_types(),
                "hint": "pass --allow-unknown-type only if a worker registers it dynamically",
            }
        )
    params = _read_params(args)
    job = _store(args).submit(job_type, params)
    return _print(
        {
            "ok": True,
            "op": "submit",
            "job_id": job.id,
            "type": job.type,
            "status": job.status,
            "created_at": job.created_at,
            "next_step": f"poll: rtime-jobs get {job.id}  (a worker must be running to execute it)",
        }
    )


def cmd_get(args: argparse.Namespace) -> int:
    job = _store(args).get(args.id)
    if job is None:
        return _print({"ok": False, "error": f"no such job: {args.id}"})
    data = job.to_dict()
    data["ok"] = True
    return _print(data)


def cmd_list(args: argparse.Namespace) -> int:
    if args.status and args.status not in ALL_STATUSES:
        return _print(
            {"ok": False, "error": f"unknown status: {args.status}", "statuses": sorted(ALL_STATUSES)}
        )
    store = _store(args)
    jobs = store.list(status=args.status, limit=args.limit)
    return _print(
        {
            "ok": True,
            "op": "list",
            "count": len(jobs),
            "counts": store.counts(),
            "jobs": [j.to_dict() for j in jobs],
        }
    )


def cmd_worker(args: argparse.Namespace) -> int:
    store = _store(args)
    if args.once:
        summary = run_pending(
            store,
            max_jobs=args.max,
            recover_stale_seconds=args.recover_stale_seconds,
            max_attempts=args.max_attempts,
        )
    else:
        summary = worker_loop(
            store,
            poll_seconds=args.poll_seconds,
            idle_exit_seconds=args.idle_exit if args.idle_exit > 0 else None,
            max_attempts=args.max_attempts,
        )
    return _print(summary)


def cmd_doctor(args: argparse.Namespace) -> int:
    store = _store(args)
    return _print(
        {
            "ok": True,
            "op": "doctor",
            "db_path": str(store.path),
            "db_exists": store.path.is_file(),
            "known_types": known_types(),
            "counts": store.counts(),
        }
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-jobs",
        description="Minimal local job queue + worker (P7 long-task isolation).",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # --db is shared by every subcommand (via a parent parser) so it can appear
    # AFTER the subcommand — e.g. `rtime-jobs worker --db X --once` — which is how
    # the rtime-jobs-worker launcher forwards its args.
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--db", help="queue DB path; defaults to RTIME_JOBS_DB or the state dir")

    p_submit = sub.add_parser("submit", parents=[common], help="enqueue a pending job")
    p_submit.add_argument("--type", required=True, help=f"job type (one of: {', '.join(known_types())})")
    p_submit.add_argument("--params", help="job params as a JSON object string")
    p_submit.add_argument("--params-stdin", action="store_true", help="read params JSON from stdin")
    p_submit.add_argument(
        "--allow-unknown-type",
        action="store_true",
        help="permit a type with no built-in handler (a worker must register one)",
    )
    p_submit.set_defaults(func=cmd_submit)

    p_get = sub.add_parser("get", parents=[common], help="print one job by id (status/result/error)")
    p_get.add_argument("id")
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser("list", parents=[common], help="list jobs (most recent first)")
    p_list.add_argument("--status", help="filter by status")
    p_list.add_argument("--limit", type=int, default=50)
    p_list.set_defaults(func=cmd_list)

    p_worker = sub.add_parser("worker", parents=[common], help="run pending jobs (loop, or --once)")
    p_worker.add_argument("--once", action="store_true", help="drain currently-pending jobs then exit")
    p_worker.add_argument("--max", type=int, default=None, help="max jobs to process in --once mode")
    p_worker.add_argument("--poll-seconds", type=float, default=2.0, help="loop poll interval")
    p_worker.add_argument(
        "--idle-exit",
        type=float,
        default=0.0,
        help="self-exit after this many idle seconds (0 = run forever)",
    )
    p_worker.add_argument(
        "--max-attempts",
        type=int,
        default=3,
        help="requeue a job whose worker crashed mid-run up to this many attempts, then fail it",
    )
    p_worker.add_argument(
        "--recover-stale-seconds",
        type=float,
        default=None,
        help="(--once) before draining, requeue jobs stuck 'running' longer than this (loop mode recovers all at startup)",
    )
    p_worker.set_defaults(func=cmd_worker)

    p_doctor = sub.add_parser("doctor", parents=[common], help="resolved DB path, known types, counts")
    p_doctor.set_defaults(func=cmd_doctor)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
