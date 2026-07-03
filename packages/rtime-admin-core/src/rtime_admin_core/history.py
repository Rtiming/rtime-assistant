# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Snapshot history: full-state snapshots taken before every change, with a cap.

``apply`` and ``rollback`` snapshot the *entire* config+secrets state before
writing, so any change is reversible (docs/development-plan §五 L1). Snapshots are
kept newest-last; when the count exceeds ``max_history`` the oldest are dropped.

A snapshot id is caller-supplied (from the injected clock/uuid, never generated
inside the core) to keep the store deterministic and testable. The store only
enforces uniqueness and ordering.

Backends: :class:`InMemoryHistory` (tests) and :class:`FileHistory` (one JSON file
per snapshot under a directory). Both implement the same small protocol so the
ConfigStore is history-backend agnostic.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

from .backends import json_copy


@dataclass(frozen=True)
class Snapshot:
    """A full-state snapshot. ``config``/``secrets`` are nested
    ``{module: {field: value}}`` copies; ``secrets`` is stored as-is (the history
    store lives next to the secrets file and inherits its protection)."""

    id: str
    ts: str  # ISO-8601, injected
    config: dict[str, dict[str, Any]] = field(default_factory=dict)
    secrets: dict[str, dict[str, Any]] = field(default_factory=dict)
    note: str | None = None

    def to_meta(self) -> dict[str, Any]:
        """Lightweight descriptor for ``list_history`` (no payload)."""
        return {"id": self.id, "ts": self.ts, "note": self.note}


class HistoryStore(Protocol):
    def add(self, snapshot: Snapshot) -> None: ...
    def get(self, snapshot_id: str) -> Snapshot | None: ...
    def list(self) -> list[Snapshot]: ...  # oldest -> newest
    def prune(self, max_history: int) -> list[str]: ...  # returns dropped ids


class InMemoryHistory:
    """Ordered in-memory snapshot list (oldest first)."""

    def __init__(self) -> None:
        self._items: list[Snapshot] = []

    def add(self, snapshot: Snapshot) -> None:
        if any(s.id == snapshot.id for s in self._items):
            raise ValueError(f"duplicate snapshot id: {snapshot.id!r}")
        self._items.append(snapshot)

    def get(self, snapshot_id: str) -> Snapshot | None:
        for s in self._items:
            if s.id == snapshot_id:
                return s
        return None

    def list(self) -> list[Snapshot]:
        return list(self._items)

    def prune(self, max_history: int) -> list[str]:
        if max_history < 0:
            raise ValueError("max_history must be >= 0")
        dropped: list[str] = []
        while len(self._items) > max_history:
            dropped.append(self._items.pop(0).id)
        return dropped


class FileHistory:
    """One JSON file per snapshot under ``dir``; ordered by ``ts`` then insertion.

    Files are named ``<ts>__<seq>__<id>.json`` where ``seq`` is a zero-padded
    monotonic insertion counter. A lexical sort therefore orders by timestamp
    first and by insertion order for EQUAL timestamps — so eviction never drops a
    newer snapshot in favour of an older one just because its id sorts later
    (defect #3: the old ``<ts>__<id>`` naming ordered ties by lexical id). Written
    0600 (snapshots contain secrets).
    """

    _SEQ_WIDTH = 12

    def __init__(self, directory: str | Path) -> None:
        self.dir = Path(directory)

    def _files(self) -> list[Path]:
        if not self.dir.exists():
            return []
        return sorted(self.dir.glob("*.json"))

    def _next_seq(self) -> int:
        """Max existing sequence + 1 (0 for an empty dir), so ordering survives
        across process restarts on the same directory."""
        top = -1
        for p in self._files():
            parts = p.name.split("__")
            if len(parts) >= 3 and parts[1].isdigit():
                top = max(top, int(parts[1]))
        return top + 1

    def add(self, snapshot: Snapshot) -> None:
        if self.get(snapshot.id) is not None:
            raise ValueError(f"duplicate snapshot id: {snapshot.id!r}")
        # 0700: snapshot files hold secrets (written 0600 below); the directory
        # that lists them should not be world-readable/traversable either
        # (defect #11). chmod after mkdir so an existing 0755 dir is tightened.
        self.dir.mkdir(parents=True, exist_ok=True)
        try:
            self.dir.chmod(0o700)
        except OSError:  # pragma: no cover - platform without chmod
            pass
        safe_ts = snapshot.ts.replace(":", "").replace(" ", "_")
        seq = f"{self._next_seq():0{self._SEQ_WIDTH}d}"
        path = self.dir / f"{safe_ts}__{seq}__{snapshot.id}.json"
        payload = {
            "id": snapshot.id,
            "ts": snapshot.ts,
            "config": snapshot.config,
            "secrets": snapshot.secrets,
            "note": snapshot.note,
        }
        path.write_text(
            json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        path.chmod(0o600)

    @staticmethod
    def _load(path: Path) -> Snapshot:
        data = json.loads(path.read_text(encoding="utf-8"))
        return Snapshot(
            id=data["id"],
            ts=data["ts"],
            config=data.get("config", {}),
            secrets=data.get("secrets", {}),
            note=data.get("note"),
        )

    def get(self, snapshot_id: str) -> Snapshot | None:
        for path in self._files():
            snap = self._load(path)
            if snap.id == snapshot_id:
                return snap
        return None

    def list(self) -> list[Snapshot]:
        return [self._load(p) for p in self._files()]

    def prune(self, max_history: int) -> list[str]:
        if max_history < 0:
            raise ValueError("max_history must be >= 0")
        files = self._files()
        dropped: list[str] = []
        while len(files) > max_history:
            oldest = files.pop(0)
            dropped.append(self._load(oldest).id)
            oldest.unlink()
        return dropped


def snapshot_state(
    snapshot_id: str,
    ts: str,
    config: dict[str, dict[str, Any]],
    secrets: dict[str, dict[str, Any]],
    note: str | None = None,
) -> Snapshot:
    """Build a :class:`Snapshot` with defensive deep copies of the state."""
    return Snapshot(
        id=snapshot_id,
        ts=ts,
        config=json_copy(config),
        secrets=json_copy(secrets),
        note=note,
    )
