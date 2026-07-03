# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make the shared in-repo packages importable for this app.

Mirrors ``apps/feishu-bridge/_shared_runtime.py``: the QQ bridge reuses the five
channel-agnostic primitives (run_log / run_control / access_policy / chat_queue /
attachment_directives) extracted into ``packages/rtime-chat-runtime`` (P5, see
docs/maintainability-standards.zh-CN.md), and — since the P2 config pilot — the
schema-driven settings base ``packages/rtime-config`` (pydantic-settings
foundation for ``qq_bridge.config``). This keeps both imports working whether the
app runs from a repo checkout, a uv-managed venv (path source in pyproject), or a
Docker image that COPYs the packages onto a known path.

Imported for side effect by ``qq_bridge.__init__`` as early as possible, *before*
importing ``rtime_chat_runtime`` / ``rtime_config``. Idempotent: a no-op for any
package that is already importable.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# module name -> (env var overriding the package's src dir, repo packages/ dir name)
# rtime_admin_core is only needed on the T2 profile-consumption path (imported lazily
# by QQBridgeConfig.from_profile); listing it here makes it importable in the same
# three environments (repo checkout / venv path source / Docker COPY + env) so a
# profile-bound bridge can build the ConfigStore.
_SHARED_PACKAGES: dict[str, tuple[str, str]] = {
    "rtime_chat_runtime": ("RTIME_CHAT_RUNTIME_SRC", "rtime-chat-runtime"),
    "rtime_config": ("RTIME_CONFIG_SRC", "rtime-config"),
    "rtime_admin_core": ("RTIME_ADMIN_CORE_SRC", "rtime-admin-core"),
}


def _ensure_on_path() -> None:
    for module, (env_name, pkg_dir) in _SHARED_PACKAGES.items():
        if importlib.util.find_spec(module) is not None:
            continue  # already importable: editable install or PYTHONPATH

        candidates: list[Path] = []
        override = os.environ.get(env_name, "").strip()
        if override:
            candidates.append(Path(override))
        # In a repo checkout this file is apps/qq-bridge/qq_bridge/_runtime_path.py,
        # so the repo root is parents[3]; in a Docker image it may sit shallower
        # (e.g. /app/qq_bridge), so guard the index and rely on the env override.
        parents = Path(__file__).resolve().parents
        if len(parents) > 3:
            candidates.append(parents[3] / "packages" / pkg_dir / "src")

        for src in candidates:
            if (src / module / "__init__.py").is_file():
                sys.path.insert(0, str(src))
                break


_ensure_on_path()
