# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Test bootstrap: make ``qq_bridge`` and ``rtime_chat_runtime`` importable, and
isolate the run log to a temp file so tests never touch real logs.
"""

import sys
from pathlib import Path

import pytest

APP_ROOT = Path(__file__).resolve().parents[1]  # apps/qq-bridge
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

# T2 profile-consumption tests project onto the REAL qq module via admin-core's
# ConfigStore; best-effort add rtime-admin-core (and rtime-config) src for direct
# ``pytest apps/qq-bridge/tests`` runs without an editable install. Tests that need
# them guard with importlib.util.find_spec and skip cleanly if unavailable.
_REPO = APP_ROOT.parents[1]  # repo root
for _src in (
    _REPO / "packages" / "rtime-admin-core" / "src",
    _REPO / "packages" / "rtime-config" / "src",
):
    if _src.is_dir() and str(_src) not in sys.path:
        sys.path.insert(0, str(_src))

import qq_bridge  # noqa: E402,F401 — side effect: puts rtime_chat_runtime on sys.path


@pytest.fixture(autouse=True)
def _isolate_run_log(tmp_path, monkeypatch):
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(tmp_path / "qq-bridge-run.jsonl"))
