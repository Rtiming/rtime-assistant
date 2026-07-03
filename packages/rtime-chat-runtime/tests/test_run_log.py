# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json

from rtime_chat_runtime.run_log import (
    append_run_event,
    hash_value,
    new_run_id,
    summarize_text,
)


def test_run_log_writes_jsonl_and_redacts_sensitive_fields(tmp_path, monkeypatch):
    log_path = tmp_path / "run-log.jsonl"
    monkeypatch.setenv("RTIME_ASSISTANT_RUN_LOG", str(log_path))

    ok = append_run_event(
        "run_started",
        run_id="run_test",
        api_key="secret-value",
        nested={"token": "token-value", "safe": "visible"},
    )

    assert ok is True
    record = json.loads(log_path.read_text(encoding="utf-8"))
    assert record["schema_version"] == 1
    assert record["event"] == "run_started"
    assert record["api_key"] == "[REDACTED]"
    assert record["nested"]["token"] == "[REDACTED]"
    assert record["nested"]["safe"] == "visible"


def test_run_log_helpers_are_stable():
    assert new_run_id("feishu").startswith("feishu-")
    assert hash_value("user_001").startswith("sha256:")
    assert summarize_text("hello\n\nworld", limit=40) == "hello world"
    assert summarize_text("x" * 20, limit=10) == "xxxxxxx..."
