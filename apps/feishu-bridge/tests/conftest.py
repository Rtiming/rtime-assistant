# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""
全局 test fixtures。
确保所有测试使用临时目录存储 sessions，不污染 ~/.feishu-claude/sessions.json。
"""

import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("FEISHU_APP_ID", "test_app_id")
os.environ.setdefault("FEISHU_APP_SECRET", "test_app_secret")

APP_ROOT = Path(__file__).resolve().parents[1]
if str(APP_ROOT) not in sys.path:
    sys.path.insert(0, str(APP_ROOT))

import _shared_runtime  # noqa: E402,F401 — put rtime_chat_runtime on sys.path

import session_store as _ss  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_sessions(tmp_path, monkeypatch):
    """自动隔离: 将 SESSIONS_DIR / SESSIONS_FILE 指向临时目录"""
    monkeypatch.setattr(_ss, "SESSIONS_DIR", str(tmp_path))
    monkeypatch.setattr(_ss, "SESSIONS_FILE", str(tmp_path / "sessions.json"))
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(tmp_path / "run-log.jsonl"))
