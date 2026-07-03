# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make the shared in-repo packages importable for this app.

Mirrors ``apps/qq-bridge/qq_bridge/_runtime_path.py``: web-chat is the third
channel on the shared chat runtime (model_runner / session_store / tool_policy /
run_log / sse), and — since T5b — also consumes the git profile layer, so it needs
the schema-driven settings base ``rtime-config`` and the ``rtime-admin-core``
ConfigStore/registry (the profile-consumption stack, same as the QQ bridge). This
keeps every import working whether the app runs from a repo checkout, a
uv-managed venv (path source in pyproject), or a Docker image that COPYs the
packages onto a known path (``RTIME_*_SRC``).

Imported for side effect by ``web_chat.__init__`` as early as possible, *before*
importing ``rtime_chat_runtime`` / ``rtime_config``. Idempotent: a no-op for any
package that is already importable.

``rtime_config`` / ``rtime_admin_core`` are only touched on the profile path
(``load_profiles`` compiles ``profiles/``; ``WebChatConfig.from_profile`` builds
the ConfigStore) — but wiring them here keeps them importable in the same three
environments so a profile-bound instance can build the layer.
"""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path

# module name -> (env var overriding the package's src dir, repo path to the dir
# CONTAINING the top-level package). Most live under packages/<name>/src; the
# qq-bridge CONFIG module (needed only to satisfy the profile loader's secret door
# for the qq.* projection targets — see below) lives at the app root apps/qq-bridge.
_REPO_ROOT_FROM_HERE = 3  # apps/web-chat/web_chat/_runtime_path.py -> repo root
_SHARED_PACKAGES: dict[str, tuple[str, str]] = {
    "rtime_chat_runtime": ("RTIME_CHAT_RUNTIME_SRC", "packages/rtime-chat-runtime/src"),
    "rtime_config": ("RTIME_CONFIG_SRC", "packages/rtime-config/src"),
    "rtime_admin_core": ("RTIME_ADMIN_CORE_SRC", "packages/rtime-admin-core/src"),
    # qq_bridge is NOT a web-chat runtime dependency. Its CONFIG module (pydantic
    # only — no aiohttp/napcat) is needed solely because the git profile projection
    # targets qq.* keys (design §2.3), so the shared loader's x-secret door must
    # classify them against the qq schema. Wired lazily like the others; a bare
    # web-chat checkout that never compiles profiles/ (uses RTIME_WEB_CHAT_PROFILES)
    # never touches it. The Docker image copies apps/qq-bridge/qq_bridge/config.py
    # (config-only) or sets QQ_BRIDGE_SRC — it does NOT install the QQ runtime.
    "qq_bridge": ("QQ_BRIDGE_SRC", "apps/qq-bridge"),
}


def _ensure_on_path() -> None:
    for module, (env_name, rel_dir) in _SHARED_PACKAGES.items():
        if importlib.util.find_spec(module) is not None:
            continue  # already importable: uv workspace editable install or PYTHONPATH

        candidates: list[Path] = []
        override = os.environ.get(env_name, "").strip()
        if override:
            candidates.append(Path(override))
        # Repo checkout: this file is apps/web-chat/web_chat/_runtime_path.py, so the
        # repo root is parents[3]; in a Docker image it may sit shallower (e.g.
        # /app/web_chat), so guard the index and rely on the env override.
        parents = Path(__file__).resolve().parents
        if len(parents) > _REPO_ROOT_FROM_HERE:
            candidates.append(parents[_REPO_ROOT_FROM_HERE] / rel_dir)

        for src in candidates:
            if (src / module / "__init__.py").is_file():
                sys.path.insert(0, str(src))
                break


_ensure_on_path()
