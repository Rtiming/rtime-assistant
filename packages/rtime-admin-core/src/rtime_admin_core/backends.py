# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Storage backends for a deployment's config.

A backend persists two things for one deployment instance:
  - the non-secret config (a nested ``{module: {field: value}}`` mapping);
  - the secrets, kept in a SEPARATE store so the main config file never contains a
    credential in plaintext (docs/audit §二: secrets go to a secret file, not the
    schema-driven config).

The default :class:`FileBackend` writes JSON files. It is deliberately dumb: it
does NOT validate, redact, snapshot, or merge env — those are the ConfigStore's
job. A backend only reads and writes bytes so the store's logic is backend-
agnostic (a future TOML or SQLite backend just implements the same 4 methods).

env overlay is read-only and NOT a backend concern: the store reads env at
``get``/``get_all`` time and never writes it back (docs/development-plan §五 L1:
"env 覆盖只读不写").
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any, Protocol


class ConfigBackend(Protocol):
    """The persistence contract the ConfigStore depends on."""

    def load_config(self) -> dict[str, dict[str, Any]]:
        """Read the non-secret config as nested ``{module: {field: value}}``."""

    def save_config(self, config: dict[str, dict[str, Any]]) -> None:
        """Persist the non-secret config (full overwrite)."""

    def load_secrets(self) -> dict[str, dict[str, Any]]:
        """Read secrets as nested ``{module: {field: value}}``."""

    def save_secrets(self, secrets: dict[str, dict[str, Any]]) -> None:
        """Persist secrets (full overwrite, restrictive file mode)."""


class FileBackend:
    """JSON file backend: one config file + one separate secrets file.

    The secrets file is written with mode 0600. Both files hold a nested
    ``{module: {field: value}}`` object. Missing files read as ``{}`` (fresh
    deployment). Writes are atomic (write temp + ``os.replace``) so a crash cannot
    leave a half-written config.
    """

    def __init__(self, config_path: str | Path, secrets_path: str | Path) -> None:
        self.config_path = Path(config_path)
        self.secrets_path = Path(secrets_path)

    # --- config ---------------------------------------------------------------
    def load_config(self) -> dict[str, dict[str, Any]]:
        return self._read(self.config_path)

    def save_config(self, config: dict[str, dict[str, Any]]) -> None:
        self._write(self.config_path, config, mode=None)

    # --- secrets --------------------------------------------------------------
    def load_secrets(self) -> dict[str, dict[str, Any]]:
        return self._read(self.secrets_path)

    def save_secrets(self, secrets: dict[str, dict[str, Any]]) -> None:
        self._write(self.secrets_path, secrets, mode=0o600)

    # --- helpers --------------------------------------------------------------
    @staticmethod
    def _read(path: Path) -> dict[str, dict[str, Any]]:
        if not path.exists():
            return {}
        text = path.read_text(encoding="utf-8").strip()
        if not text:
            return {}
        data = json.loads(text)
        if not isinstance(data, dict):
            raise ValueError(f"config store file is not a JSON object: {path}")
        return data

    @staticmethod
    def _write(path: Path, data: dict[str, Any], *, mode: int | None) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        payload = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        # Create the temp file at its final restrictive mode from the START — never
        # let a secret file briefly exist world-readable (0644) before a chmod
        # (defect #11). O_EXCL avoids reusing a hostile pre-existing tmp; the umask
        # is neutralised because os.open sets the mode explicitly.
        create_mode = 0o600 if mode is None else mode
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        fd = os.open(str(tmp), flags, create_mode)
        try:
            # Ensure the mode is exactly what we asked for even if the file already
            # existed (O_CREAT does not re-chmod an existing file).
            os.fchmod(fd, create_mode)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
                fh.flush()
                # fsync the file before rename so a completed os.replace implies the
                # data is durable; without this a power loss can leave an empty file
                # and _read would silently fall back to defaults (defect #13).
                os.fsync(fh.fileno())
        except BaseException:
            # never leave a partial temp file behind
            try:
                os.unlink(str(tmp))
            except OSError:  # pragma: no cover
                pass
            raise
        os.replace(str(tmp), str(path))
        # fsync the directory so the rename itself is durable.
        try:
            dir_fd = os.open(str(path.parent), os.O_RDONLY)
            try:
                os.fsync(dir_fd)
            finally:
                os.close(dir_fd)
        except OSError:  # pragma: no cover - some platforms disallow dir fsync
            pass


class MemoryBackend:
    """In-memory backend for tests: holds config + secrets in dicts."""

    def __init__(
        self,
        config: dict[str, dict[str, Any]] | None = None,
        secrets: dict[str, dict[str, Any]] | None = None,
    ) -> None:
        self._config = json_copy(config or {})
        self._secrets = json_copy(secrets or {})

    def load_config(self) -> dict[str, dict[str, Any]]:
        return json_copy(self._config)

    def save_config(self, config: dict[str, dict[str, Any]]) -> None:
        self._config = json_copy(config)

    def load_secrets(self) -> dict[str, dict[str, Any]]:
        return json_copy(self._secrets)

    def save_secrets(self, secrets: dict[str, dict[str, Any]]) -> None:
        self._secrets = json_copy(secrets)


def json_copy(obj: dict[str, Any]) -> dict[str, Any]:
    """A cheap deep copy for plain JSON-shaped dicts (isolates callers' mutations)."""
    return json.loads(json.dumps(obj))
