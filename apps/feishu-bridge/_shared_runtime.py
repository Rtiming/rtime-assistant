# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make the shared in-repo packages importable for this app.

Two package groups are pulled in:

  * ``rtime_chat_runtime`` — the five channel-agnostic primitives run_log /
    run_control / access_policy / chat_queue / attachment_directives extracted into
    packages/rtime-chat-runtime (P5, see docs/maintainability-standards.zh-CN.md §三).
    Its src dir may be overridden with ``RTIME_CHAT_RUNTIME_SRC`` (deploy shim).
  * ``rtime_config`` — the schema-driven pydantic-settings foundation for
    ``bot_config.FeishuBridgeConfig`` (P2 config 收编 批 1, see
    docs/design/config-full-coverage-plan-2026-07.zh-CN.md §二 批 1). Resolved from
    the sibling in-repo package (no separate env override; the uv workspace / Docker
    COPY put it in reach).

The Feishu bridge is deployed as a self-contained tree (systemd runs
apps/feishu-bridge/main.py from a full repo checkout; the Docker image COPYs the
packages onto /app/packages — see docker/feishu-bridge.Dockerfile), so a small path
bootstrap keeps the imports working in every shape without requiring a pip install.

Import this module for its side effect, as early as possible (main.py, bot_config,
and the test conftest do), *before* importing rtime_chat_runtime / rtime_config.
Idempotent: a no-op for any package that is already importable (editable install /
PYTHONPATH).
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# apps/feishu-bridge/_shared_runtime.py -> repo (or /app) root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _insert(src: Path, module: str) -> bool:
    """Prepend ``src`` to sys.path if it holds ``module``; return whether it did."""
    if (src / module / "__init__.py").is_file():
        sys.path.insert(0, str(src))
        return True
    return False


def _ensure_on_path() -> None:
    # rtime_chat_runtime: env override (deploy shim) then the repo packages/ dir.
    if importlib.util.find_spec("rtime_chat_runtime") is None:
        override = os.environ.get("RTIME_CHAT_RUNTIME_SRC", "").strip()
        candidates = [Path(override)] if override else []
        candidates.append(_REPO_ROOT / "packages" / "rtime-chat-runtime" / "src")
        for src in candidates:
            if _insert(src, "rtime_chat_runtime"):
                break

    # rtime_config: the schema-driven config base (P2 收编 批 1).
    if importlib.util.find_spec("rtime_config") is None:
        _insert(_REPO_ROOT / "packages" / "rtime-config" / "src", "rtime_config")


_ensure_on_path()
