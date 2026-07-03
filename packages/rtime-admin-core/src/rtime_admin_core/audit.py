# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Append-only JSONL audit log for config changes.

Every apply / rollback writes one line (docs/development-plan.zh-CN.md §五):

    {ts, actor, source, action, path, before/after diff, snapshot_id, outcome}

Design constraints:
  - ``ts`` is injected by the caller, never read from ``time.now()`` inside the
    core — so tests are deterministic and the same clock the upper layer trusts is
    the one recorded.
  - secret values in the diff are hashed placeholders, never plaintext (redaction
    is done by the diff producer before it reaches here; :class:`AuditEntry`
    carries an already-redacted diff).
  - append-only: entries are only ever added. The backends here (in-memory list,
    JSONL file) never rewrite history.

An ``audit`` hook is just ``Callable[[AuditEntry], None]``; the ConfigStore calls
it on apply/rollback. Two ready backends: :class:`InMemoryAuditSink` (tests) and
:class:`JsonlAuditSink` (a real deployment). Custom sinks (HTTP, syslog) implement
the same one-method protocol.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Callable

# One outcome vocabulary so downstream filters are stable.
OUTCOME_OK = "ok"
OUTCOME_ERROR = "error"


@dataclass(frozen=True)
class AuditEntry:
    """One audit record. ``diff`` is a mapping ``path -> {before, after}`` with
    secret values ALREADY replaced by hashed placeholders."""

    ts: str  # ISO-8601 timestamp, injected by the caller
    actor: str  # who (token id / username / "system")
    source: str  # how (http | mcp | cli | panel | test)
    action: str  # what (apply | rollback | ...)
    outcome: str  # OUTCOME_OK | OUTCOME_ERROR
    paths: list[str] = field(default_factory=list)  # affected config paths
    diff: dict[str, Any] = field(default_factory=dict)  # redacted before/after
    snapshot_id: str | None = None  # snapshot taken before the change
    detail: str | None = None  # error message / free note

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json(self) -> str:
        # sort_keys for a stable, diff-friendly line; ensure_ascii=False for CJK.
        return json.dumps(self.to_dict(), ensure_ascii=False, sort_keys=True)


# An audit hook: called once per apply/rollback with the finished entry.
AuditHook = Callable[[AuditEntry], None]


class InMemoryAuditSink:
    """Collects entries in a list; handy for tests and dry inspection."""

    def __init__(self) -> None:
        self.entries: list[AuditEntry] = []

    def __call__(self, entry: AuditEntry) -> None:
        self.entries.append(entry)


class JsonlAuditSink:
    """Appends one JSON line per entry to a file (creates parent dirs).

    Thread-safe append under a lock; each ``__call__`` opens in append mode so a
    crash mid-run cannot corrupt earlier lines.

    The log is created 0600 (owner-only): although secret VALUES are redacted,
    the log still carries actor names, changed paths, notes and timestamps —
    operational metadata that lives next to the secrets file and should not be
    world-readable (defect #10). The parent dir is created 0700 for the same
    reason; existing files/dirs are re-chmod'd to the intended mode so an
    already-0644 log from before this fix is tightened on next append.
    """

    _FILE_MODE = 0o600
    _DIR_MODE = 0o700

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self._lock = threading.Lock()

    def __call__(self, entry: AuditEntry) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.path.parent.chmod(self._DIR_MODE)
        except OSError:  # pragma: no cover - platform without chmod
            pass
        line = entry.to_json()
        with self._lock:
            # O_CREAT at 0600 so the file is never briefly world-readable; append
            # (O_APPEND) preserves the crash-safe one-open-per-entry behaviour.
            fd = os.open(
                str(self.path),
                os.O_WRONLY | os.O_CREAT | os.O_APPEND,
                self._FILE_MODE,
            )
            try:
                # Tighten mode even if the file pre-existed (O_CREAT does not
                # re-chmod an existing file), so a legacy 0644 log is fixed.
                os.fchmod(fd, self._FILE_MODE)
                with os.fdopen(fd, "a", encoding="utf-8") as fh:
                    fh.write(line + "\n")
            except BaseException:
                try:
                    os.close(fd)
                except OSError:
                    pass
                raise

    def read_all(self) -> list[dict[str, Any]]:
        """Parse the log back to a list of dicts (for CLI/tests; not hot path)."""
        if not self.path.exists():
            return []
        out: list[dict[str, Any]] = []
        for raw in self.path.read_text(encoding="utf-8").splitlines():
            raw = raw.strip()
            if raw:
                out.append(json.loads(raw))
        return out
