# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import os
import subprocess
import sys


def test_load_feishu_credentials_from_json(tmp_path, monkeypatch):
    import bot_config

    cfg = tmp_path / "feishu.json"
    cfg.write_text(
        json.dumps({"appId": "cli_json_app", "appSecret": "json_secret"}),
        encoding="utf-8",
    )

    monkeypatch.delenv("FEISHU_APP_ID", raising=False)
    monkeypatch.delenv("FEISHU_APP_SECRET", raising=False)
    monkeypatch.setenv("FEISHU_CONFIG_JSON", str(cfg))

    assert bot_config._load_feishu_credentials() == ("cli_json_app", "json_secret")


def test_default_permission_mode_is_not_bypass():
    env = os.environ.copy()
    env["FEISHU_APP_ID"] = "test_app_id"
    env["FEISHU_APP_SECRET"] = "test_app_secret"
    env.pop("PERMISSION_MODE", None)

    result = subprocess.run(
        [sys.executable, "-c", "import bot_config; print(bot_config.PERMISSION_MODE)"],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.strip() == "default"


def test_model_aliases_json_extends_defaults():
    env = os.environ.copy()
    env["FEISHU_APP_ID"] = "test_app_id"
    env["FEISHU_APP_SECRET"] = "test_app_secret"
    env["MODEL_ALIASES_JSON"] = json.dumps({"kimi": "", "qwen": "qwen-code"})

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "import bot_config; print(bot_config.MODEL_ALIASES['kimi']); print(bot_config.MODEL_ALIASES['qwen']); print(bot_config.MODEL_ALIASES['sonnet'])",
        ],
        cwd=os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        env=env,
        text=True,
        capture_output=True,
        check=True,
    )

    assert result.stdout.splitlines() == [
        "",
        "qwen-code",
        "claude-sonnet-4-6",
    ]
