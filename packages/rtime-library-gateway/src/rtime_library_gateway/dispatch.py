# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Subprocess dispatch for the rtime library gateway.

Each gateway method maps to either an existing read CLI (``READ_DISPATCH``) or
one of the three ``deploy/bin`` narrow-write executables (``WRITE_DISPATCH``).
The two tables have *disjoint* key sets and the three write executables appear
only in ``WRITE_DISPATCH`` -- a read method can never reach a write target.

A dispatch builds an argv plus the environment (per-target ``PYTHONPATH`` rooted
at ``RTIME_ASSISTANT_ROOT/packages/<pkg>/src``, plus brain/hub/reminders roots).
:func:`run_cli` runs it with ``subprocess.run(check=False, capture_output=True,
text=True, timeout=...)`` and parses the single JSON object the tool prints.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from .gate import GateError

JsonObject = dict[str, Any]

DEFAULT_TIMEOUT = 60

# Portable fallbacks mirror the public install examples.
_BRAIN_FALLBACKS = (
    "/mnt/brain",
)
_HUB_FALLBACKS = (
    str(Path.home() / "rtime-hub"),
)
_REMINDERS_FALLBACKS = (
    "/mnt/brain/_system/reminders.jsonl",
    str(Path.home() / "brain" / "_system" / "reminders.jsonl"),
)


class DispatchError(GateError):
    """A dispatch-level failure (bad op, missing argument, non-JSON output)."""


def repo_root() -> Path:
    raw = os.environ.get("RTIME_ASSISTANT_ROOT")
    if raw:
        return Path(raw).expanduser().resolve()
    # Fall back to the repository that contains this package.
    return Path(__file__).resolve().parents[4]


def brain_root() -> Path:
    raw = os.environ.get("BRAIN_ROOT") or os.environ.get("RTIME_BRAIN_ROOT")
    if raw:
        return Path(raw).expanduser()
    for candidate in _BRAIN_FALLBACKS:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(_BRAIN_FALLBACKS[0])


def hub_root() -> Path:
    raw = os.environ.get("RTIME_HUB_ROOT")
    if raw:
        return Path(raw).expanduser()
    for candidate in _HUB_FALLBACKS:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(_HUB_FALLBACKS[0])


def reminders_path() -> Path:
    raw = os.environ.get("RTIME_REMINDERS_PATH")
    if raw:
        return Path(raw).expanduser()
    for candidate in _REMINDERS_FALLBACKS:
        if Path(candidate).exists():
            return Path(candidate)
    return Path(_REMINDERS_FALLBACKS[0])


def default_index() -> str:
    """Resolve the BM25 index path when a search/get call omits ``index``.

    Mirrors ``scripts/brain-search``: ``BRAIN_LIBRARY_INDEX`` wins, otherwise the
    standard derived-cache location under the user's state dir. This lets callers
    issue ``lib.search {query}`` without knowing the machine-local index path.
    """
    raw = os.environ.get("BRAIN_LIBRARY_INDEX")
    if raw:
        return str(Path(raw).expanduser())
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return str(state / "rtime-assistant" / "brain-library" / "brain-library.sqlite")


@dataclass(frozen=True)
class Target:
    """A resolved subprocess invocation built from method arguments."""

    argv: list[str]
    package: str | None = None  # e.g. "brain-library" for PYTHONPATH; None for deploy/bin
    extra_env: dict[str, str] = field(default_factory=dict)
    stdin: str | None = None
    redact_force: bool = False  # contacts lane forces redaction on
    timeout: int | None = None  # per-target subprocess timeout; None -> DEFAULT_TIMEOUT


def _python() -> str:
    return os.environ.get("PYTHON", sys.executable)


def _module_argv(module: str, args: list[str]) -> list[str]:
    return [_python(), "-m", module, *args]


def _choice(arguments: JsonObject, key: str, allowed: set[str], default: str) -> str:
    value = arguments.get(key, default)
    if not isinstance(value, str) or not value:
        value = default
    if value not in allowed:
        raise DispatchError(f"{key} must be one of: {', '.join(sorted(allowed))}")
    return value


def _str_arg(arguments: JsonObject, key: str, *, required: bool = False) -> str | None:
    value = arguments.get(key)
    if value is None or value == "":
        if required:
            raise DispatchError(f"missing required argument: {key}")
        return None
    if not isinstance(value, str):
        raise DispatchError(f"{key} must be a string")
    return value


def _int_arg(arguments: JsonObject, key: str, default: int) -> int:
    value = arguments.get(key, default)
    if isinstance(value, bool) or not isinstance(value, int):
        raise DispatchError(f"{key} must be an integer")
    return value


# --------------------------------------------------------------------------
# READ targets
# --------------------------------------------------------------------------


def _build_search(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    query = _str_arg(arguments, "query", required=True)
    limit = _int_arg(arguments, "limit", 10)
    args = ["index", "query", str(index), str(query), "--limit", str(limit), "--json"]
    suffix = _str_arg(arguments, "suffix")
    if suffix:
        args.extend(["--suffix", suffix])
    prefix = _str_arg(arguments, "path_prefix")
    if prefix:
        args.extend(["--path-prefix", prefix])
    if arguments.get("title_only"):
        args.append("--title-only")
    for key, flag in (
        ("doc_type", "--doc-type"), ("dept", "--dept"), ("category", "--category"),
        ("date_from", "--date-from"), ("date_to", "--date-to"),
    ):
        value = _str_arg(arguments, key)
        if value:
            args.extend([flag, value])
    order_by = arguments.get("order_by")
    if order_by in ("relevance", "date"):
        args.extend(["--order-by", order_by])
    mode = arguments.get("mode")
    if mode in ("bm25", "vector", "hybrid"):
        args.extend(["--mode", mode])
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_courses(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    args = ["index", "courses", str(index), "--limit", str(_int_arg(arguments, "limit", 200)), "--json"]
    for key, flag in (
        ("code", "--code"), ("name_like", "--name-like"), ("program_path", "--program-path"),
        ("dept", "--dept"), ("grade", "--grade"),
    ):
        value = _str_arg(arguments, key)
        if value:
            args.extend([flag, value])
    min_credits = arguments.get("min_credits")
    if isinstance(min_credits, (int, float)) and not isinstance(min_credits, bool):
        args.extend(["--min-credits", str(min_credits)])
    if arguments.get("required_only"):
        args.append("--required-only")
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_get(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    args = ["index", "status", str(index), "--json"]
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_read(arguments: JsonObject) -> Target:
    args = ["read", "--path", _str_arg(arguments, "path", required=True), "--json"]
    if "offset" in arguments:
        args.extend(["--offset", str(_int_arg(arguments, "offset", 0))])
    if "limit" in arguments:
        args.extend(["--limit", str(_int_arg(arguments, "limit", 0))])
    if "max_bytes" in arguments:
        args.extend(["--max-bytes", str(_int_arg(arguments, "max_bytes", 2000000))])
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_tree(arguments: JsonObject) -> Target:
    args = ["tree", "--json"]
    path = _str_arg(arguments, "path")
    if path:
        args.extend(["--path", path])
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_stat(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    args = ["stat", "--path", _str_arg(arguments, "path", required=True),
            "--index", index, "--json"]
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_recent(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    args = ["index", "recent", str(index), "--limit", str(_int_arg(arguments, "limit", 20)), "--json"]
    suffix = _str_arg(arguments, "suffix")
    if suffix:
        args.extend(["--suffix", suffix])
    prefix = _str_arg(arguments, "path_prefix")
    if prefix:
        args.extend(["--path-prefix", prefix])
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_freshness(arguments: JsonObject) -> Target:
    index = _str_arg(arguments, "index") or default_index()
    args = ["index", "freshness", str(index), "--brain-root", str(brain_root()), "--json"]
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_list(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"docpacks", "scan"}, "docpacks")
    root = _str_arg(arguments, "root") or str(brain_root())
    sample_limit = _int_arg(arguments, "sample_limit", 20)
    args = [op, root, "--sample-limit", str(sample_limit), "--json"]
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_meta(arguments: JsonObject) -> Target:
    root = _str_arg(arguments, "root") or str(brain_root())
    args = ["meta", root, "--json"]
    name = _str_arg(arguments, "name")
    if name:
        args.extend(["--name", name])
    query = _str_arg(arguments, "query")
    if query:
        args.extend(["--query", query])
    if "max_bytes" in arguments:
        args.extend(["--max-bytes", str(_int_arg(arguments, "max_bytes", 200000))])
    return Target(_module_argv("brain_library.cli", args), package="brain-library")


def _build_docpack(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"audit", "select-samples", "validate", "doctor"}, "doctor")
    args: list[str] = [op]
    if op == "audit":
        args.append(_str_arg(arguments, "path") or str(brain_root() / "knowledge"))
    elif op == "select-samples":
        root = _str_arg(arguments, "path") or str(brain_root() / "knowledge")
        args.extend([root, "--json"])
    elif op == "validate":
        args.extend([_str_arg(arguments, "path", required=True), "--json"])
    # doctor takes no positional
    return Target(_module_argv("brain_docpack.cli", args), package="brain-docpack")


def _build_citation(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"scan", "panel", "doctor"}, "panel")
    root = _str_arg(arguments, "root") or str(brain_root())
    args = [op, root]
    return Target(_module_argv("brain_citation.cli", args), package="brain-citation")


def _build_hub(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"panel", "scan", "contacts", "doctor"}, "panel")
    root = _str_arg(arguments, "hub_root") or _str_arg(arguments, "root") or str(hub_root())
    args = [op, root]
    return Target(
        _module_argv("rtime_hub_connector.cli", args),
        package="rtime-hub-connector",
        redact_force=(op == "contacts"),
    )


def _build_context(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"doctor", "plan", "pack", "explain"}, "doctor")
    args: list[str] = [op]
    if op != "doctor":
        args.append(_str_arg(arguments, "request", required=True))
    return Target(_module_argv("rtime_context.cli", args), package="rtime-context")


def _build_profile(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"panel", "scan", "plan", "doctor"}, "panel")
    args: list[str] = [op]
    if op == "plan":
        args.append(_str_arg(arguments, "request", required=True))
    return Target(_module_argv("rtime_profile.cli", args), package="rtime-profile")


def _build_review(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"panel", "audits", "run-logs", "tooling", "doctor"}, "panel")
    args: list[str] = [op]
    if op == "run-logs":
        args.append(_str_arg(arguments, "path", required=True))
    return Target(_module_argv("rtime_review.cli", args), package="rtime-review")


def _build_automation(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"panel", "reminders", "health", "doctor"}, "panel")
    args: list[str] = [op]
    return Target(_module_argv("rtime_automation.cli", args), package="rtime-automation")


def _build_jobs_get(arguments: JsonObject) -> Target:
    job_id = _str_arg(arguments, "id", required=True)
    return Target(_module_argv("rtime_jobs.cli", ["get", str(job_id)]), package="rtime-jobs")


def _build_jobs_list(arguments: JsonObject) -> Target:
    args = ["list", "--limit", str(_int_arg(arguments, "limit", 50))]
    status = _str_arg(arguments, "status")
    if status:
        args.extend(["--status", status])
    return Target(_module_argv("rtime_jobs.cli", args), package="rtime-jobs")


def _build_runtime(arguments: JsonObject) -> Target:
    op = _choice(arguments, "op", {"doctor", "run-log-summary"}, "doctor")
    if op == "run-log-summary":
        args = ["run-log", "summary"]
        path = _str_arg(arguments, "path")
        if path:
            args.append(path)
    else:
        args = ["doctor"]
    return Target(_module_argv("rtime_assistant_runtime.cli", args), package="rtime-assistant-runtime")


READ_DISPATCH: dict[str, Callable[[JsonObject], Target]] = {
    "lib.search": _build_search,
    "lib.courses": _build_courses,
    "lib.get": _build_get,
    "lib.read": _build_read,
    "lib.tree": _build_tree,
    "lib.stat": _build_stat,
    "lib.recent": _build_recent,
    "lib.freshness": _build_freshness,
    "lib.list": _build_list,
    "lib.meta": _build_meta,
    "lib.docpack": _build_docpack,
    "lib.citation": _build_citation,
    "lib.hub": _build_hub,
    "lib.context": _build_context,
    "lib.profile": _build_profile,
    "lib.review": _build_review,
    "lib.automation": _build_automation,
    "lib.runtime": _build_runtime,
    "lib.jobs.get": _build_jobs_get,
    "lib.jobs.list": _build_jobs_list,
}


# --------------------------------------------------------------------------
# WRITE targets -- only the three deploy/bin narrow tools.
# --------------------------------------------------------------------------


def _deploy_bin(name: str) -> str:
    return str(repo_root() / "deploy" / "bin" / name)


def _build_context_source(arguments: JsonObject, command: str) -> Target:
    argv = [_python(), _deploy_bin("rtime-context-source"), "--brain-root", str(brain_root())]
    manifest = _str_arg(arguments, "manifest")
    if manifest:
        argv.extend(["--manifest", manifest])
    argv.append(command)
    if command == "list":
        status = _str_arg(arguments, "status")
        if status:
            argv.extend(["--status", status])
        argv.extend(["--limit", str(_int_arg(arguments, "limit", 50))])
    elif command == "check":
        pass
    elif command == "add":
        argv.extend(
            [
                "--id",
                _str_arg(arguments, "id", required=True),
                "--kind",
                _str_arg(arguments, "kind", required=True),
                "--title",
                _str_arg(arguments, "title", required=True),
                "--source-path",
                _str_arg(arguments, "source_path", required=True),
            ]
        )
        for flag, key in (
            ("--tags", "tags"),
            ("--status", "status"),
            ("--active-from", "active_from"),
            ("--expires", "expires"),
        ):
            value = _str_arg(arguments, key)
            if value:
                argv.extend([flag, value])
        if "priority" in arguments:
            argv.extend(["--priority", str(_int_arg(arguments, "priority", 0))])
        if "max_chars" in arguments:
            argv.extend(["--max-chars", str(_int_arg(arguments, "max_chars", 4000))])
        if arguments.get("dry_run"):
            argv.append("--dry-run")
    elif command == "deactivate":
        argv.extend(["--id", _str_arg(arguments, "id", required=True)])
        status = _choice(arguments, "status", {"inactive", "cancelled"}, "inactive")
        argv.extend(["--status", status])
        reason = _str_arg(arguments, "reason")
        if reason:
            argv.extend(["--reason", reason])
        if arguments.get("dry_run"):
            argv.append("--dry-run")
    return Target(argv)


def _build_memory_candidate_add(arguments: JsonObject) -> Target:
    """Build the memory-candidate add argv.

    The claim text is passed via stdin (``--json-stdin``) so it never lands in
    argv (and therefore never in process listings or the gateway audit). The
    entry is forced to ``library-gateway``.
    """
    claim = _str_arg(arguments, "claim", required=True)
    payload: JsonObject = {"claim": claim, "entry": "library-gateway"}
    for key in ("scope", "kind", "source", "sensitivity"):
        value = _str_arg(arguments, key)
        if value:
            payload[key] = value
    argv = [_python(), _deploy_bin("rtime-memory-candidate"), "--brain-root", str(brain_root()), "add", "--json-stdin"]
    if "expires_days" in arguments:
        argv.extend(["--expires-days", str(_int_arg(arguments, "expires_days", 90))])
    if arguments.get("dry_run"):
        argv.append("--dry-run")
    return Target(argv, stdin=json.dumps(payload, ensure_ascii=False))


def _build_reminder(arguments: JsonObject, command: str) -> Target:
    argv = [_python(), _deploy_bin("rtime-reminder-register")]
    path = _str_arg(arguments, "path")
    if path:
        argv.extend(["--path", path])
    argv.append(command)
    if command == "add":
        argv.extend(["--due", _str_arg(arguments, "due", required=True)])
        argv.extend(["--message", _str_arg(arguments, "message", required=True)])
        for flag, key in (
            ("--mode", "mode"),
            ("--repeat", "repeat"),
            ("--prompt", "prompt"),
            ("--cwd", "cwd"),
            ("--model", "model"),
            ("--permission-mode", "permission_mode"),
            ("--id", "id"),
        ):
            value = _str_arg(arguments, key)
            if value:
                argv.extend([flag, value])
        if arguments.get("dry_run"):
            argv.append("--dry-run")
    elif command == "list":
        status = _str_arg(arguments, "status")
        if status:
            argv.extend(["--status", status])
        argv.extend(["--limit", str(_int_arg(arguments, "limit", 20))])
    elif command == "cancel":
        argv.extend(["--id", _str_arg(arguments, "id", required=True)])
    return Target(argv)


def _build_contribute(arguments: JsonObject) -> Target:
    """Stage an agent-authored note into brain/_inbox via deploy/bin/rtime-contribute.

    op ``plan`` previews (no write); op ``stage`` writes into _inbox/agent only.
    The note title/text/note/tags all travel on stdin (``--json-stdin``) so they
    never land in argv, the process listing, or the gateway audit. The tool itself
    constrains writes to _inbox and refuses sensitive material (defense in depth).
    """
    op = _choice(arguments, "op", {"plan", "stage"}, "plan")
    payload: JsonObject = {
        "title": _str_arg(arguments, "title", required=True),
        "text": _str_arg(arguments, "text", required=True),
    }
    note = _str_arg(arguments, "note")
    if note:
        payload["note"] = note
    tags = _str_arg(arguments, "tags")
    if tags:
        payload["tags"] = tags
    argv = [_python(), _deploy_bin("rtime-contribute"), "--brain-root", str(brain_root()), op, "--json-stdin"]
    if op == "stage" and arguments.get("dry_run"):
        argv.append("--dry-run")
    return Target(argv, stdin=json.dumps(payload, ensure_ascii=False))


def _build_finalize(arguments: JsonObject) -> Target:
    """Promote a staged ``_inbox`` item into ``knowledge/`` via deploy/bin/rtime-finalize.

    Only ``op=plan`` (preview, no write) and ``op=apply`` (requires the owner token)
    are reachable here. The owner ``approve`` step that mints the token is
    intentionally NOT exposed to the gateway, so an agent can never self-approve.
    """
    op = _choice(arguments, "op", {"plan", "apply"}, "plan")
    argv = [_python(), _deploy_bin("rtime-finalize"), "--brain-root", str(brain_root()), op]
    timeout: int | None = None
    if op == "plan":
        argv.extend(["--inbox", _str_arg(arguments, "inbox", required=True)])
        argv.extend(["--dest", _str_arg(arguments, "dest", required=True)])
        name = _str_arg(arguments, "name")
        if name:
            argv.extend(["--name", name])
        summary = _str_arg(arguments, "summary")
        if summary:
            argv.extend(["--summary", summary])
        if arguments.get("notify"):
            argv.append("--notify")
    else:  # apply
        argv.extend(["--token", _str_arg(arguments, "plan_sha", required=True)])
        if arguments.get("ocr"):
            argv.append("--ocr")
        if arguments.get("docpack"):
            argv.append("--docpack")
        if arguments.get("no_reindex"):
            argv.append("--no-reindex")
        # bundle extract + parse + OCR/DocPack can exceed the 60s default; the index
        # rebuild itself is now incremental (reuses unchanged docs, ~seconds).
        timeout = 1800
    if arguments.get("dry_run"):
        argv.append("--dry-run")
    return Target(argv, timeout=timeout)


def _build_course_intake(arguments: JsonObject) -> Target:
    """Ingest a course folder (under _inbox) into knowledge/courses/<id> with auto
    slides/lectures/exams classify via deploy/bin/rtime-course-intake. plan/apply only;
    the owner ``approve`` step is owner-only (not a gateway method)."""
    op = _choice(arguments, "op", {"plan", "apply"}, "plan")
    argv = [_python(), _deploy_bin("rtime-course-intake"), "--brain-root", str(brain_root()), op]
    if op == "plan":
        argv.extend(["--src", _str_arg(arguments, "src", required=True)])
        argv.extend(["--course-id", _str_arg(arguments, "course_id", required=True)])
        argv.extend(["--course-title", _str_arg(arguments, "course_title", required=True)])
        if arguments.get("notify"):
            argv.append("--notify")
        timeout = 600  # classification reads first-page text of every PDF
    else:
        argv.extend(["--token", _str_arg(arguments, "plan_sha", required=True)])
        if arguments.get("no_reindex"):
            argv.append("--no-reindex")
        timeout = 1800  # convert + md + materials_index; index rebuild is incremental (~seconds)
    if arguments.get("dry_run"):
        argv.append("--dry-run")
    return Target(argv, timeout=timeout)


def _build_jobs_submit(arguments: JsonObject) -> Target:
    """Enqueue a deferred long task via deploy/bin/rtime-jobs-submit.

    The job ``params`` travel on stdin (``--params-stdin``) so they never land in
    argv, the process listing, or the gateway audit — mirroring lib.contribute.
    This is a machine-local-state write (the job queue), NOT a brain-content
    write: the heavy work runs later in a separate worker, and any brain mutation
    that worker performs (e.g. course-intake-apply) still goes through its own
    owner-token gate.
    """
    job_type = _str_arg(arguments, "type", required=True)
    params = arguments.get("params")
    if params is None:
        params = {}
    if not isinstance(params, dict):
        raise DispatchError("params must be an object")
    argv = [_python(), _deploy_bin("rtime-jobs-submit"), "--type", str(job_type), "--params-stdin"]
    return Target(argv, stdin=json.dumps(params, ensure_ascii=False))


WRITE_DISPATCH: dict[str, Callable[[JsonObject], Target]] = {
    "lib.settings.context_source_list": lambda a: _build_context_source(a, "list"),
    "lib.settings.context_source_check": lambda a: _build_context_source(a, "check"),
    "lib.settings.context_source_add": lambda a: _build_context_source(a, "add"),
    "lib.settings.context_source_deactivate": lambda a: _build_context_source(a, "deactivate"),
    "lib.settings.memory_candidate_add": _build_memory_candidate_add,
    "lib.settings.reminder_register": lambda a: _build_reminder(a, "add"),
    "lib.settings.reminder_list": lambda a: _build_reminder(a, "list"),
    "lib.settings.reminder_cancel": lambda a: _build_reminder(a, "cancel"),
    "lib.contribute": _build_contribute,
    "lib.finalize": _build_finalize,
    "lib.course-intake": _build_course_intake,
    "lib.jobs.submit": _build_jobs_submit,
}


# Hard invariant: the read and write tables never share a key, and the three
# narrow-write executables are reachable only through WRITE_DISPATCH.
assert READ_DISPATCH.keys().isdisjoint(WRITE_DISPATCH.keys()), "read/write dispatch keys overlap"

WRITE_EXECUTABLES = (
    "rtime-context-source",
    "rtime-memory-candidate",
    "rtime-reminder-register",
    "rtime-contribute",
    "rtime-finalize",
    "rtime-course-intake",
    "rtime-jobs-submit",
)


def build_target(method: str, arguments: JsonObject) -> Target:
    builder = READ_DISPATCH.get(method) or WRITE_DISPATCH.get(method)
    if builder is None:
        raise DispatchError(f"no dispatch for method: {method}")
    return builder(arguments)


def _env_for(target: Target) -> dict[str, str]:
    env = dict(os.environ)
    root = repo_root()
    env["RTIME_ASSISTANT_ROOT"] = str(root)
    if target.package:
        pkg_src = str(root / "packages" / target.package / "src")
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = f"{pkg_src}{os.pathsep}{existing}" if existing else pkg_src
    # Root resolution always exported so subprocesses agree with the gateway.
    env.setdefault("BRAIN_ROOT", str(brain_root()))
    env.setdefault("RTIME_HUB_ROOT", str(hub_root()))
    env.setdefault("RTIME_REMINDERS_PATH", str(reminders_path()))
    # Backends emit UTF-8 JSON (ensure_ascii=False, Chinese bodies). Pin the child
    # interpreter to UTF-8 I/O so it does not encode stdout with the host locale
    # (cp936/GBK on Windows), which mojibakes or raises in the child. Pairs with
    # the UTF-8 decode in run_cli. No-op on Linux/orangepi (already UTF-8).
    # Force (not setdefault): if the gateway itself was launched with a non-UTF-8
    # PYTHONIOENCODING inherited from a wrapper, setdefault would keep that wrong
    # value and re-introduce the mojibake. extra_env below can still override.
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONUTF8"] = "1"
    env.update(target.extra_env)
    return env


def run_cli(target: Target, *, timeout: int = DEFAULT_TIMEOUT) -> tuple[JsonObject, int, str]:
    """Run a target subprocess and return a structured result from its stdout.

    Returns ``(result_dict, returncode, raw_stdout)``. The preferred backend
    contract is a single JSON object on stdout. But some backends emit human text
    (e.g. the docpack audit shell report) or a bare JSON value; rather than raising
    ``"tool did not return JSON"``, such output is WRAPPED into a clean structured
    dict whose ``ok`` tracks the exit code, so the gateway always hands the caller a
    usable result. ``raw_stdout`` is returned so the caller can re-derive a redacted
    text payload.
    """
    try:
        completed = subprocess.run(
            target.argv,
            input=target.stdin,
            check=False,
            capture_output=True,
            text=True,
            # Force UTF-8 both ways: backends emit UTF-8 JSON with Chinese bodies,
            # but text=True otherwise decodes with the platform default (cp936/GBK
            # on Windows), which raises UnicodeDecodeError on the first non-ASCII
            # byte. errors="replace" keeps a transient bad byte from killing a read.
            encoding="utf-8",
            errors="replace",
            timeout=target.timeout or timeout,
            env=_env_for(target),
        )
    except FileNotFoundError as exc:
        return {"ok": False, "error": f"backend not found: {exc}"}, 127, ""
    stdout = completed.stdout or ""
    rc = completed.returncode
    # errors="replace" above turns undecodable bytes into U+FFFD silently; surface
    # that so a corrupted read is visible to the caller rather than passed off as
    # faithful content. Clean UTF-8 output (the normal path) never sets this.
    replaced = "�" in stdout
    if not stdout.strip():
        return {"ok": rc == 0, "empty_output": True}, rc, stdout
    try:
        parsed = json.loads(stdout)
    except json.JSONDecodeError:
        # Non-JSON backend (text report / log / traceback): wrap, don't raise.
        wrapped: JsonObject = {
            "ok": rc == 0,
            "non_json_output": True,
            "raw_output": stdout if len(stdout) <= 20000 else stdout[:20000] + "\n…[truncated]",
        }
        stderr_tail = (completed.stderr or "").strip()[-500:]
        if stderr_tail:
            wrapped["stderr_tail"] = stderr_tail
        if replaced:
            wrapped["decode_replacements"] = True
        return wrapped, rc, stdout
    if not isinstance(parsed, dict):
        # A bare JSON list/str/number: wrap so the result is always an object.
        wrapped_value: JsonObject = {"ok": rc == 0, "value": parsed}
        if replaced:
            wrapped_value["decode_replacements"] = True
        return wrapped_value, rc, stdout
    if replaced:
        parsed.setdefault("decode_replacements", True)
    return parsed, rc, stdout
