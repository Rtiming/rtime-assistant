# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Job-type -> handler registry.

A handler is a callable ``(params: dict) -> dict`` that does the heavy work and
returns a JSON-able result. It runs in the *worker* process, never in the chat
entry. Raising :class:`JobError` (or any exception) marks the job ``failed`` with
the message as the stored error.

Registered types (the minimal P7 set — add more as long tasks are isolated):

- ``echo``               — return the params; a zero-side-effect worker self-test.
- ``index-rebuild``      — rebuild the brain BM25/vector index (idempotent, no
                           owner gate; the deterministic end-to-end sample).
- ``course-intake-apply``— run the owner-token-gated course ingest for an
                           already-approved ``plan_sha``. The heavy convert + md +
                           reindex moves off the chat entry; crucially the owner
                           approval is STILL enforced by the underlying tool, so
                           submitting a job is not the same as approving it.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

JsonObject = dict[str, Any]
Handler = Callable[[JsonObject], JsonObject]


class JobError(Exception):
    """A handler-level failure; its message becomes the job's stored error."""


# --------------------------------------------------------------------------
# Shared helpers
# --------------------------------------------------------------------------
def _repo_root() -> Path | None:
    """Locate the repo so handlers can reach package src + deploy/bin."""
    raw = os.environ.get("RTIME_ASSISTANT_ROOT")
    if raw and (Path(raw) / "packages" / "brain-library" / "src").is_dir():
        return Path(raw)
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / "packages" / "brain-library" / "src").is_dir():
            return parent
    return None


def _brain_root() -> Path:
    raw = os.environ.get("BRAIN_ROOT") or os.environ.get("RTIME_BRAIN_ROOT")
    if raw:
        return Path(raw).expanduser()
    for candidate in (
        Path("/mnt/brain"),
    ):
        if candidate.exists():
            return candidate
    return Path("/mnt/brain")


def _default_index() -> str:
    raw = os.environ.get("BRAIN_LIBRARY_INDEX")
    if raw:
        return str(Path(raw).expanduser())
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return str(state / "rtime-assistant" / "brain-library" / "brain-library.sqlite")


def _run(argv: list[str], *, env: dict[str, str], timeout: int) -> JsonObject:
    """Run a subprocess and parse its single JSON object, raising on failure.

    Mirrors the gateway's ``run_cli`` contract closely enough for handlers: a
    non-zero exit (or unparseable output on failure) becomes a :class:`JobError`
    so the job is recorded ``failed`` with a useful message.
    """
    try:
        completed = subprocess.run(
            argv,
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
        )
    except FileNotFoundError as exc:
        raise JobError(f"backend not found: {exc}") from exc
    except subprocess.TimeoutExpired as exc:
        raise JobError(f"backend timed out after {timeout}s") from exc
    stdout = (completed.stdout or "").strip()
    parsed: JsonObject | None = None
    if stdout:
        try:
            value = json.loads(stdout)
            parsed = value if isinstance(value, dict) else {"value": value}
        except json.JSONDecodeError:
            parsed = None
    if completed.returncode != 0:
        detail = (completed.stderr or "").strip()[-500:] or stdout[-500:]
        raise JobError(f"backend failed (rc={completed.returncode}): {detail}")
    if parsed is None:
        raise JobError("backend produced no JSON output")
    return parsed


def _child_env(extra_pythonpath: str | None = None) -> dict[str, str]:
    env = dict(os.environ)
    env.setdefault("PYTHONUTF8", "1")
    env["PYTHONIOENCODING"] = "utf-8"
    if extra_pythonpath:
        existing = env.get("PYTHONPATH")
        env["PYTHONPATH"] = (
            f"{extra_pythonpath}{os.pathsep}{existing}" if existing else extra_pythonpath
        )
    return env


# --------------------------------------------------------------------------
# Handlers
# --------------------------------------------------------------------------
def handle_echo(params: JsonObject) -> JsonObject:
    """Zero-side-effect self-test: confirms submit -> worker -> result works
    end to end without touching the brain."""
    return {"ok": True, "echo": params}


def handle_index_rebuild(params: JsonObject) -> JsonObject:
    """Rebuild the brain search index (incremental by default).

    params: ``brain_root`` (optional), ``index`` (optional), ``incremental``
    (optional bool, default true). No owner gate: the index is a derived cache.
    """
    repo = _repo_root()
    if repo is None:
        raise JobError("cannot locate repo root for brain_library")
    brain_root = Path(params.get("brain_root") or _brain_root()).expanduser()
    if not brain_root.exists():
        raise JobError(f"brain root does not exist: {brain_root}")
    index = str(params.get("index") or _default_index())
    Path(index).parent.mkdir(parents=True, exist_ok=True)
    incremental = params.get("incremental", True)
    argv = [
        sys.executable,
        "-m",
        "brain_library.cli",
        "index",
        "build",
        str(brain_root),
        "--out",
        index,
        "--json",
    ]
    if incremental:
        argv.append("--incremental")
    env = _child_env(str(repo / "packages" / "brain-library" / "src"))
    env["RTIME_ASSISTANT_ROOT"] = str(repo)
    result = _run(argv, env=env, timeout=1800)
    result.setdefault("index", index)
    return result


def handle_course_intake_apply(params: JsonObject) -> JsonObject:
    """Apply an OWNER-APPROVED course ingest off the chat entry.

    params: ``plan_sha`` (required — the owner-approved plan), ``no_reindex``
    (optional bool), ``brain_root`` (optional).

    The underlying ``deploy/bin/rtime-course-intake apply`` still refuses without
    a ``<sha>.approved`` file, so this job CANNOT bypass owner approval: a job for
    an unapproved plan simply fails here.
    """
    plan_sha = params.get("plan_sha")
    if not isinstance(plan_sha, str) or not plan_sha:
        raise JobError("course-intake-apply requires a 'plan_sha'")
    repo = _repo_root()
    if repo is None:
        raise JobError("cannot locate repo root for deploy/bin/rtime-course-intake")
    tool = repo / "deploy" / "bin" / "rtime-course-intake"
    if not tool.is_file():
        raise JobError(f"course-intake tool not found: {tool}")
    brain_root = Path(params.get("brain_root") or _brain_root()).expanduser()
    argv = [
        sys.executable,
        str(tool),
        "--brain-root",
        str(brain_root),
        "apply",
        "--token",
        plan_sha,
    ]
    if params.get("no_reindex"):
        argv.append("--no-reindex")
    env = _child_env()
    env["RTIME_ASSISTANT_ROOT"] = str(repo)
    env["BRAIN_ROOT"] = str(brain_root)
    return _run(argv, env=env, timeout=1900)


HANDLERS: dict[str, Handler] = {
    "echo": handle_echo,
    "index-rebuild": handle_index_rebuild,
    "course-intake-apply": handle_course_intake_apply,
}


def known_types() -> list[str]:
    """Sorted list of registered job types (for submit-time validation/help)."""
    return sorted(HANDLERS)


def get_handler(job_type: str) -> Handler | None:
    return HANDLERS.get(job_type)
