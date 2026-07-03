# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Slim per-(user,chat) session store (shared core).

Maps a chat conversation to a claude CLI ``session_id`` (for ``--resume`` continuity),
plus its model / permission-mode / cwd / stream preference. Persisted as one JSON file
under a per-channel directory (each channel passes its own ``sessions_dir`` so they
never collide). Promoted from the QQ bridge into the shared core (channel-unification
P1). Deliberately minimal — the Feishu bridge's heavier session_store (with identity
heuristics + an out-of-band summarizer) is reconciled separately at P3.
"""

from __future__ import annotations

import json
import os
from dataclasses import asdict, dataclass


@dataclass
class Session:
    session_id: str | None = None
    model: str = ""
    permission_mode: str = "default"
    cwd: str = ""
    stream: bool | None = None  # None => use the channel default


class SessionStore:
    def __init__(
        self,
        sessions_dir: str,
        *,
        default_model: str = "",
        default_permission_mode: str = "default",
        default_cwd: str = "",
    ) -> None:
        self._dir = sessions_dir
        self._path = os.path.join(sessions_dir, "sessions.json")
        self._defaults = Session(
            session_id=None,
            model=default_model,
            permission_mode=default_permission_mode,
            cwd=default_cwd,
        )
        self._data: dict[str, dict] = {}
        self._load()

    @staticmethod
    def _key(user_id: str, chat_id: str) -> str:
        return f"{user_id}:{chat_id}"

    def _load(self) -> None:
        try:
            with open(self._path, encoding="utf-8") as fh:
                loaded = json.load(fh)
            if isinstance(loaded, dict):
                self._data = loaded
        except (FileNotFoundError, ValueError):
            self._data = {}

    def _save(self) -> None:
        try:
            os.makedirs(self._dir, exist_ok=True)
            tmp = self._path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as fh:
                json.dump(self._data, fh, ensure_ascii=False, indent=2)
            os.replace(tmp, self._path)
        except Exception:
            pass  # best-effort persistence; in-memory state still correct

    def get(self, user_id: str, chat_id: str) -> Session:
        raw = self._data.get(self._key(user_id, chat_id))
        if not raw:
            return Session(**asdict(self._defaults))
        merged = {**asdict(self._defaults), **raw}
        return Session(**{k: merged[k] for k in asdict(self._defaults)})

    def on_response(
        self, user_id: str, chat_id: str, new_session_id: str | None
    ) -> None:
        if not new_session_id:
            return
        s = self.get(user_id, chat_id)
        s.session_id = new_session_id
        self._data[self._key(user_id, chat_id)] = asdict(s)
        self._save()

    def set_model(self, user_id: str, chat_id: str, model: str) -> None:
        s = self.get(user_id, chat_id)
        s.model = model
        self._data[self._key(user_id, chat_id)] = asdict(s)
        self._save()

    def set_stream(self, user_id: str, chat_id: str, stream: bool | None) -> None:
        s = self.get(user_id, chat_id)
        s.stream = stream
        self._data[self._key(user_id, chat_id)] = asdict(s)
        self._save()

    def reset(self, user_id: str, chat_id: str) -> None:
        """Drop the session id (a fresh conversation), keep model preference."""
        s = self.get(user_id, chat_id)
        s.session_id = None
        self._data[self._key(user_id, chat_id)] = asdict(s)
        self._save()
