# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bin" / "rtime-qq-code"


def _run(
    *args: str,
    env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    merged_env = os.environ.copy()
    if env:
        merged_env.update(env)
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        check=False,
        capture_output=True,
        text=True,
        env=merged_env,
    )


def test_request_writes_metadata_only_signal(tmp_path):
    target = tmp_path / "qq-qr-request"

    proc = _run(
        "request",
        "--path",
        str(target),
        "--requester",
        "ou_owner_secret",
        "--reason",
        "我的 QQ 小号掉线了，帮我把码发过来",
    )

    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert output["ok"] is True
    assert output["action"] == "request"
    assert output["path"] == str(target)
    assert output["privacy"]["qr_image_returned"] is False
    assert output["privacy"]["requester_value_returned"] is False
    assert output["privacy"]["reason_text_returned"] is False
    assert "ou_owner_secret" not in proc.stdout
    assert "小号掉线" not in proc.stdout

    target_text = target.read_text(encoding="utf-8")
    payload = json.loads(target_text)
    assert payload["source"] == "rtime-qq-code"
    assert payload["requester_open_id"] == "ou_owner_secret"
    assert payload["reason_chars"] > 0
    assert "小号掉线" not in target_text


def test_request_uses_env_path_and_dry_run_does_not_write(tmp_path):
    target = tmp_path / "env-request"

    proc = _run(
        "request",
        "--dry-run",
        env={"RTIME_QQ_QR_REQUEST_FILE": str(target)},
    )

    assert proc.returncode == 0, proc.stderr
    output = json.loads(proc.stdout)
    assert output["dry_run"] is True
    assert output["path"] == str(target)
    assert not target.exists()
