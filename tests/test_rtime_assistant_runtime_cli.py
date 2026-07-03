# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLI = (
    ROOT
    / "packages"
    / "rtime-assistant-runtime"
    / "src"
    / "rtime_assistant_runtime"
    / "cli.py"
)


def _load_cli():
    spec = importlib.util.spec_from_file_location("rtime_assistant_runtime_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["rtime_assistant_runtime_cli"] = module
    spec.loader.exec_module(module)
    return module


def test_runtime_doctor_reports_repo_and_templates(capfd):
    cli = _load_cli()

    assert cli.main(["doctor"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["repo_root"] == str(ROOT)
    assert data["checks"]["feishu_bridge_main"] == "ok"
    assert data["templates"]["ok"] is True
    assert data["docker_prod"]["ok"] is True


def test_run_log_summary_counts_events_and_redacts_sensitive_fields(tmp_path, capfd):
    cli = _load_cli()
    log_path = tmp_path / "run-log.jsonl"
    records = [
        {
            "schema_version": 1,
            "event": "run_started",
            "timestamp": "2026-06-10T00:00:00Z",
            "run_id": "run-1",
            "entry": "feishu",
            "api_key": "secret",
        },
        {
            "schema_version": 1,
            "event": "run_completed",
            "timestamp": "2026-06-10T00:00:01Z",
            "run_id": "run-1",
            "entry": "feishu",
            "status": "ok",
        },
    ]
    log_path.write_text("\n".join(json.dumps(record) for record in records) + "\n", encoding="utf-8")

    assert cli.main(["run-log", "summary", str(log_path)]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["record_count"] == 2
    assert data["run_count"] == 1
    assert data["events"]["run_started"] == 1
    assert data["latest_timestamp"] == "2026-06-10T00:00:01Z"


def test_run_log_tail_redacts_and_reports_malformed(tmp_path, capfd):
    cli = _load_cli()
    log_path = tmp_path / "run-log.jsonl"
    log_path.write_text(
        json.dumps({"event": "run_started", "token": "secret"}) + "\n"
        "{not-json}\n"
        + json.dumps({"event": "run_failed", "nested": {"password": "secret"}})
        + "\n",
        encoding="utf-8",
    )

    assert cli.main(["run-log", "tail", str(log_path), "--limit", "5"]) == 1
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is False
    assert data["malformed_count"] == 1
    assert data["records"][0]["token"] == "[REDACTED]"
    assert data["records"][1]["nested"]["password"] == "[REDACTED]"


def test_templates_check_passes_for_current_repo(capfd):
    cli = _load_cli()

    assert cli.main(["templates", "check"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    names = {item["name"] for item in data["templates"]}
    assert "feishu-bridge-python.service" in names
    reminder = next(item for item in data["templates"] if item["name"] == "reminder.service")
    assert reminder["has_install"] is False
    assert reminder["requires_install"] is False
    assert data["missing"] == []
    assert data["forbidden_env_keys"] == []


def test_docker_prod_check_reports_static_contract_without_secret_values(tmp_path, capfd):
    cli = _load_cli()
    env_file = tmp_path / "docker.env"
    env_file.write_text(
        "\n".join(
            [
                "ALLOWED_USERS=ou_secret_user",
                "BRAIN_ROOT=/mnt/brain",
                "CALLBACK_BIND=127.0.0.1",
                "CLAUDE_CLI_PATH=/usr/local/bin/claude-kimi",
                "CLAUDE_CONFIG_JSON=/etc/rtime-assistant/.claude.json",
                "CLAUDE_KIMI_KEYFILE=/run/secrets/claude-kimi-key",
                "CLAUDE_STATE_ROOT=/var/lib/rtime-assistant/claude",
                "FEISHU_CONFIG_JSON=/etc/rtime-assistant/feishu.json",
                "INSTALL_CLAUDE_CODE=1",
                "MESSAGE_DEBOUNCE_SECONDS=2.0",
                "MESSAGE_DEBOUNCE_MAX_MESSAGES=20",
                "MESSAGE_DEBOUNCE_MAX_CHARS=12000",
                "RTIME_ASSISTANT_ROOT=/srv/rtime-assistant",
                "RTIME_ASSISTANT_STATE_DIR=/var/lib/rtime-assistant",
                "ANTHROPIC_AUTH_TOKEN=secret-token-value",
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    env_file.chmod(0o600)

    assert cli.main(["docker-prod", "check", "--env-file", str(env_file)]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["compose"]["checks"]["has_healthcheck"] is True
    assert data["compose"]["checks"]["has_healthz"] is True
    assert data["dockerignore"]["checks"]["excludes_env_files"] is True
    assert data["dockerignore"]["checks"]["excludes_runtime_state"] is True
    assert data["dockerfile"]["checks"]["has_runtime_stage"] is True
    assert data["dockerfile"]["checks"]["copies_bridge_source"] is True
    assert data["dockerfile"]["checks"]["copies_rtime_qq_code"] is True
    assert data["helper"]["checks"]["has_smoke_action"] is True
    assert data["helper"]["checks"]["has_one_shot_smoke"] is True
    assert data["helper"]["checks"]["clears_proxy_build_args"] is True
    assert data["helper"]["checks"]["supports_host_proxy_build_args"] is True
    assert data["bridge"]["checks"]["healthz_reports_debounce_counts"] is True
    assert data["bridge"]["checks"]["simulation_reports_process_count"] is True
    assert data["bridge"]["checks"]["simulation_uses_runner_monkeypatch"] is True
    assert data["bridge"]["checks"]["simulation_access_override_is_local"] is True
    assert data["docs"]["checks"]["has_mac_to_server_loop"] is True
    assert data["env_file"]["missing_keys"] == []
    assert data["env_file"]["permissions_too_open"] is False
    assert "ALLOWED_USERS" in data["env_file"]["keys_present"]
    assert "ou_secret_user" not in captured.out
    assert "secret-token-value" not in captured.out
