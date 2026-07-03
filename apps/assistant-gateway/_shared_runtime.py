# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make the shared in-repo packages importable for the assistant-gateway.

Two package groups are pulled in:

  * ``rtime_models`` — the model registry loader the gateway reads for provider
    base URLs / catalogs. Historically the gateway put this on ``sys.path`` via
    ``_common._ensure_rtime_models_path()``; this shim also covers it so the
    import-safe ``gateway_config_schema`` can resolve it in ``from_env`` without
    depending on ``_common`` import order.
  * ``rtime_config`` — the schema-driven pydantic-settings foundation for
    ``gateway_config_schema.AssistantGatewayConfig`` (P2 config 收编 批 2, see
    docs/design/config-full-coverage-plan-2026-07.zh-CN.md §二 批 2). Resolved from
    the sibling in-repo package.

The gateway is deployed from a repo checkout (systemd runs
apps/assistant-gateway/gateway.py; see deploy/systemd/user/assistant-gateway.service),
so a small path bootstrap keeps the imports working without requiring a pip install.

Import this module for its side effect, as early as possible, *before* importing
rtime_config / rtime_models. Idempotent: a no-op for any package that is already
importable (editable install / PYTHONPATH). Mirrors apps/feishu-bridge/
_shared_runtime.py.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

# apps/assistant-gateway/_shared_runtime.py -> repo (or /app) root is parents[2].
_REPO_ROOT = Path(__file__).resolve().parents[2]


def _insert(src: Path, module: str) -> bool:
    """Prepend ``src`` to sys.path if it holds ``module``; return whether it did."""
    if (src / module / "__init__.py").is_file():
        sys.path.insert(0, str(src))
        return True
    return False


def _ensure_on_path() -> None:
    # rtime_config: the schema-driven config base (P2 收编 批 2).
    if importlib.util.find_spec("rtime_config") is None:
        _insert(_REPO_ROOT / "packages" / "rtime-config" / "src", "rtime_config")

    # rtime_models: the model registry loader (also handled by _common, kept here so
    # the import-safe config module can resolve it independently of import order).
    if importlib.util.find_spec("rtime_models") is None:
        _insert(_REPO_ROOT / "packages" / "rtime-models" / "src", "rtime_models")


_ensure_on_path()
