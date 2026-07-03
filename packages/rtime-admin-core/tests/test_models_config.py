# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""批1 · Lane B — the REAL ``models`` config schema guard.

Mirrors apps/{qq-bridge,web-chat}/tests/test_config_schema.py for the models
module (which lives in admin-core because it has no single owning app):

  1. every field carries rtime metadata (x-env-aliases + x-reload) so panel/docs
     can render it, and every secret field carries x-secret;
  2. field defaults + legacy env names load as declared (compat contract kept);
  3. it is registered as module ``models`` in default_registry() (always on);
  4. the four 批1 · Lane B allowlist keys are now accepted by the schema;
  5. the generated config doc (docs/config/models.md) stays in lockstep.

Behaviour note: registration/coverage only — no model-routing runtime path is
changed here, so the guard is about the schema surface, not routing.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest
from rtime_admin_core.schemas import ModelsConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "config" / "models.md"

# The credential fields (x-secret). A new secret field must be added here.
SECRET_FIELDS = {
    "litellm_master_key",
    "ustc_api_key",
    "ustc_api_key_file",
    "moonshot_api_key",
    "deepseek_api_key",
    "qwen_api_key",
    "kimi_keyfile",
}

# The four env keys carried by the models-area allowlist (TODO-batch:models) that
# this batch registers so the owner can drop them at merge.
LANE_B_ALLOWLIST_KEYS = {
    "MODEL_ALIASES_JSON",
    "RTIME_MODEL_REGISTRY",
    "RTIME_MOONSHOT_BASE_URL",
    "RTIME_USTC_API_KEY_FILE",
}


def _props() -> dict:
    return ModelsConfig.model_json_schema(by_alias=False)["properties"]


def _all_env_aliases() -> list[str]:
    aliases: list[str] = []
    for prop in _props().values():
        aliases.extend(prop.get("x-env-aliases", []))
    return aliases


# --- (1) rtime metadata: x-env-aliases + x-reload on every field, x-secret ------
def test_every_field_carries_env_aliases_and_reload():
    props = _props()
    assert set(props) == set(ModelsConfig.model_fields)
    for name, prop in props.items():
        assert prop.get("x-env-aliases"), f"{name} missing x-env-aliases"
        assert prop.get("x-reload") in {"hot", "restart"}, f"{name} missing x-reload"


def test_every_field_carries_scope():
    for name, prop in _props().items():
        assert prop.get("x-scope") == "write:models", (
            f"{name} missing write:models scope"
        )


def test_secret_fields_marked_and_exhaustive():
    props = _props()
    marked = {n for n, p in props.items() if p.get("x-secret")}
    assert marked == SECRET_FIELDS


def test_default_model_is_hot():
    """default_model is the one live-switchable field (next message picks it up)."""
    props = _props()
    assert props["default_model"]["x-reload"] == "hot"
    # base urls / keys are restart (endpoints/creds re-read on process start).
    assert props["ustc_base_url"]["x-reload"] == "restart"
    assert props["litellm_master_key"]["x-reload"] == "restart"


# --- (2) defaults + legacy env-name loading -------------------------------------
def test_default_model_default_is_claude(monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    assert ModelsConfig().default_model == "claude"


DEFAULTS = {
    "default_model": "claude",
    "model_registry_path": None,
    "model_aliases_json": None,
    "ustc_agent": True,
    "ustc_agent_model": None,
    "ollama_model": None,
    "ustc_base_url": "https://api.llm.ustc.edu.cn/v1",
    "ollama_base_url": "http://127.0.0.1:11434",
    "moonshot_base_url": "https://api.moonshot.ai/v1",
    "deepseek_anthropic_base_url": "https://api.deepseek.com/anthropic",
    "qwen_anthropic_base_url": "https://dashscope-intl.aliyuncs.com/apps/anthropic",
    "litellm_base_url": None,
    "litellm_master_key": None,
    "ustc_api_key": None,
    "ustc_api_key_file": None,
    "moonshot_api_key": None,
    "deepseek_api_key": None,
    "qwen_api_key": None,
    "kimi_keyfile": None,
}


def test_defaults_table_covers_every_field():
    assert set(DEFAULTS) == set(ModelsConfig.model_fields)


@pytest.mark.parametrize("field,expected", sorted(DEFAULTS.items()))
def test_field_default(field, expected, monkeypatch):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    assert getattr(ModelsConfig(), field) == expected


LEGACY_ENV = [
    ("DEFAULT_MODEL", "opus", "default_model", "opus"),
    ("RTIME_MODEL_REGISTRY", "/etc/reg.json", "model_registry_path", "/etc/reg.json"),
    ("MODEL_ALIASES_JSON", '{"x":"y"}', "model_aliases_json", '{"x":"y"}'),
    ("RTIME_USTC_AGENT", "0", "ustc_agent", False),
    ("RTIME_USTC_AGENT_MODEL", "ds", "ustc_agent_model", "ds"),
    ("RTIME_OLLAMA_MODEL", "qwen3.5:9b", "ollama_model", "qwen3.5:9b"),
    ("RTIME_MOONSHOT_BASE_URL", "https://m/v1", "moonshot_base_url", "https://m/v1"),
    ("RTIME_LITELLM_BASE_URL", "https://l/v1", "litellm_base_url", "https://l/v1"),
    ("LITELLM_MASTER_KEY", "sk-master", "litellm_master_key", "sk-master"),
    ("RTIME_USTC_API_KEY", "sk-ustc", "ustc_api_key", "sk-ustc"),
    ("RTIME_USTC_API_KEY_FILE", "/run/ustc", "ustc_api_key_file", "/run/ustc"),
    ("CLAUDE_KIMI_KEYFILE", "/run/kimi", "kimi_keyfile", "/run/kimi"),
    # multi-alias credentials: any legacy name loads the value.
    ("MOONSHOT_API_KEY", "sk-moon", "moonshot_api_key", "sk-moon"),
    ("KIMI_API_KEY", "sk-kimi", "moonshot_api_key", "sk-kimi"),
    ("DEEPSEEK_API_KEY", "sk-ds", "deepseek_api_key", "sk-ds"),
    ("DASHSCOPE_API_KEY", "sk-dash", "qwen_api_key", "sk-dash"),
]


@pytest.mark.parametrize("env,value,field,expected", LEGACY_ENV)
def test_legacy_env_name_loads(monkeypatch, env, value, field, expected):
    for alias in _all_env_aliases():
        monkeypatch.delenv(alias, raising=False)
    monkeypatch.setenv(env, value)
    assert getattr(ModelsConfig(), field) == expected


# --- (3) always-on registration under module ``models`` -------------------------
def test_registered_in_default_registry():
    from rtime_admin_core import default_registry

    reg = default_registry()
    assert reg.has("models")
    assert reg.model("models") is ModelsConfig
    schema = reg.get_schema("models")
    assert schema["properties"]["default_model"]["x-env-aliases"] == ["DEFAULT_MODEL"]


# --- (4) the 批1 · Lane B allowlist keys are now schema-accepted -----------------
def test_lane_b_allowlist_keys_now_registered():
    accepted = set(_all_env_aliases())
    missing = LANE_B_ALLOWLIST_KEYS - accepted
    assert not missing, f"Lane B keys not accepted by schema: {sorted(missing)}"


# --- (5) generated config doc stays in lockstep with the model ------------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "rtime_admin_core.schemas:ModelsConfig",
            "--title",
            "models 配置项",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "rtime_admin_core.schemas:ModelsConfig --title 'models 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/models.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "rtime_admin_core.schemas:ModelsConfig --title 'models 配置项' "
        f"--out {DOC_PATH}"
    )
