# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Cross-process advisory lock around config mutations.

The in-process ``threading.Lock`` serialises only threads of ONE process. Once
the L3 CLI (a second process) writes the same store dir, a check-ETag ->
apply sequence in two processes can interleave (TOCTOU, defect #9). An advisory
``flock`` on ``<store>/.lock`` closes that: a mutation acquires the process
lock AND the file lock for the whole read-modify-write.

``flock`` is POSIX-only. On a platform without it (Windows dev boxes), this
degrades to a no-op with a one-time warning — the in-process lock still holds,
and the production deployment (orangepi / Linux) gets the real cross-process
guarantee. The lock is advisory: it only protects writers that also take it
(this API and the future CLI, which will use the same helper).
"""

from __future__ import annotations

import os
import warnings
from pathlib import Path
from types import TracebackType

try:  # POSIX
    import fcntl

    _HAVE_FLOCK = True
except ImportError:  # pragma: no cover - Windows
    fcntl = None  # type: ignore[assignment]
    _HAVE_FLOCK = False

LOCK_FILENAME = ".lock"


class FileLock:
    """A context manager taking an exclusive advisory flock on ``<dir>/.lock``.

    Re-entrant across ``with`` uses on the same instance is NOT supported (each
    ``__enter__`` opens/locks a fresh fd); use one ``with`` block per mutation.
    """

    def __init__(self, directory: str | Path) -> None:
        self._dir = Path(directory)
        self._path = self._dir / LOCK_FILENAME
        self._fd: int | None = None
        self._warned = False

    def __enter__(self) -> FileLock:
        if not _HAVE_FLOCK:
            if not self._warned:
                warnings.warn(
                    "flock unavailable on this platform; cross-process config "
                    "locking is a no-op (in-process lock still applies)",
                    RuntimeWarning,
                    stacklevel=2,
                )
                self._warned = True
            return self
        self._dir.mkdir(parents=True, exist_ok=True)
        # 0600: the lock file lives next to secrets; no reason for it to be
        # world-readable even though it holds no data.
        self._fd = os.open(str(self._path), os.O_CREAT | os.O_RDWR, 0o600)
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        tb: TracebackType | None,
    ) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
            finally:
                os.close(self._fd)
                self._fd = None
