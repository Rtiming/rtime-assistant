# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for the rtime-models registry + loader (packages/rtime-models).

These pin the registry to the values every consumer relied on before P3, so a
registry edit that would change live model routing fails loudly here. The
cross-consumer drift (gateway catalog projection, bash model-defaults.sh,
claude-rtime / Feishu fallbacks) is asserted in the respective consumer tests and
in tests/test_check_entrypoint_drift.py.
"""

from __future__ import annotations

from pathlib import Path

import rtime_models as r

ROOT = Path(__file__).resolve().parents[1]


def test_validate_passes():
    from rtime_models.manage import validate_registry

    assert validate_registry(r.load_registry()) == []


def test_default_model_is_empty_kimi_route():
    # Canonical default routes through the wrapper default (claude-kimi -> kimi-code).
    assert r.default_model_id() == ""


def test_catalog_providers_are_exactly_the_four_served_today():
    assert [p["id"] for p in r.catalog_providers()] == [
        "gateway-default",
        "kimi-code-wrapper",
        "moonshot-openai",
        "ustc-openai",
    ]


def test_base_urls_match_known_defaults():
    assert r.base_url("moonshot-openai") == "https://api.moonshot.ai/v1"
    assert r.base_url("ustc-openai") == "https://api.llm.ustc.edu.cn/v1"
    assert r.base_url("deepseek-code") == "https://api.deepseek.com/anthropic"
    assert r.base_url("qwen-code") == "https://dashscope-intl.aliyuncs.com/apps/anthropic"
    assert r.base_url("kimi-code-wrapper") == "https://api.kimi.com/coding"


def test_secret_env_names_cover_every_consumer_lookup():
    assert r.secret_env_names("moonshot-openai") == [
        "RTIME_MOONSHOT_API_KEY",
        "MOONSHOT_API_KEY",
        "KIMI_API_KEY",
        "RTIME_MOONSHOT_API_KEY_FILE",
        "MOONSHOT_API_KEY_FILE",
        "KIMI_API_KEY_FILE",
    ]
    assert r.secret_env_names("ustc-openai") == ["RTIME_USTC_API_KEY", "RTIME_USTC_API_KEY_FILE"]
    assert r.file_extract_provider_ids() == {"moonshot-openai"}


def test_alias_layers_match_legacy_tables():
    # claude-rtime DEFAULT_ALIASES (USTC chat).
    assert r.alias_map(["ustc-openai"]) == {
        "ds": "deepseek-v4-flash-ascend",
        "deepseek": "deepseek-v4-flash-ascend",
        "qwen": "qwen3.6-chat",
        "qianwen": "qwen3.6-chat",
        "qwen-chat": "qwen3.6-chat",
        "qwen-reasoner": "qwen3.6-reasoner",
    }
    # claude-rtime DEFAULT_CODE_ALIASES.
    assert r.alias_map(["deepseek-code", "qwen-code"]) == {
        "deepseek-code": "deepseek-v4-pro[1m]",
        "ds-code": "deepseek-v4-pro[1m]",
        "deepseek-coder": "deepseek-v4-pro[1m]",
        "qwen-code": "qwen3-coder-next",
        "qwen-coder": "qwen3-coder-next",
    }
    # bot_config base aliases.
    assert r.alias_map(["claude-anthropic"]) == {
        "opus": "claude-opus-4-6",
        "sonnet": "claude-sonnet-4-6",
        "haiku": "claude-haiku-4-5-20251001",
    }


def test_model_sets_match_legacy_defaults():
    assert set(r.code_model_ids("deepseek-code")) == {
        "deepseek-v4-pro",
        "deepseek-v4-pro[1m]",
        "deepseek-v4-flash",
    }
    assert set(r.code_model_ids("qwen-code")) == {
        "qwen3-coder-next",
        "qwen3-coder-plus",
        "qwen3-coder-plus-2025-09-23",
        "qwen3-coder-flash",
    }
    assert set(r.routing_model_ids("ustc-openai")) == {
        "deepseek-v4-flash-ascend",
        "qwen-chat",
        "qwen-reasoner",
        "qwen3.6-chat",
        "qwen3.6-reasoner",
        "smart/default",
        "smart/reasoning",
    }
    assert r.catalog_model_ids("ustc-openai") == [
        "deepseek-v4-flash-ascend",
        "qwen3.6-chat",
        "qwen3.6-reasoner",
    ]
    assert r.catalog_model_ids("moonshot-openai") == ["kimi-k2.7-code", "kimi-k2.6", "kimi-k2.5"]


def test_code_models_with_aliases_matches_feishu_recognition_sets():
    assert r.code_models_with_aliases("deepseek-code") == {
        "deepseek-v4-pro",
        "deepseek-v4-pro[1m]",
        "deepseek-v4-flash",
        "deepseek-code",
        "ds-code",
        "deepseek-coder",
    }
    assert r.code_models_with_aliases("qwen-code") == {
        "qwen3-coder-next",
        "qwen3-coder-plus",
        "qwen3-coder-plus-2025-09-23",
        "qwen3-coder-flash",
        "qwen-code",
        "qwen-coder",
    }


def test_tiers_match_wrapper_defaults():
    assert r.tiers("deepseek-code") == {"default": "deepseek-v4-pro[1m]", "fast": "deepseek-v4-flash"}
    assert r.tiers("qwen-code") == {
        "default": "qwen3-coder-next",
        "fast": "qwen3-coder-flash",
        "quality": "qwen3-coder-plus",
    }
    assert r.tiers("ustc-openai") == {"default": "deepseek-v4-flash-ascend"}
    assert r.tiers("ollama") == {"default": "qwen3.5:9b"}


def test_ustc_models_expose_agent_tools_via_litellm():
    # Flipped true in 2026-07: full agent tools through claude-ustc -> LiteLLM
    # protocol translation (deploy/litellm/config.yaml). The chat-only fallback
    # stays behind RTIME_USTC_AGENT=0 in claude-rtime.
    for model_id in ("deepseek-v4-flash-ascend", "qwen3.6-chat", "qwen3.6-reasoner"):
        caps = r.model_capabilities("ustc-openai", model_id)
        assert caps["agent_tools"] is True, model_id


def test_ollama_provider_matches_poc_defaults():
    # PoC-verified values (srv03, Ollama 0.17.7 native Anthropic endpoint).
    assert r.base_url("ollama") == "http://127.0.0.1:11434"
    assert r.secret_env_names("ollama") == []  # token is the fixed literal "ollama"
    assert set(r.routing_model_ids("ollama")) == {"qwen3.5:9b", "qwen2.5:3b"}
    assert r.alias_map(["ollama"]) == {
        "ollama": "qwen3.5:9b",
        "qwen-local": "qwen3.5:9b",
    }
    assert r.model_capabilities("ollama", "qwen3.5:9b")["agent_tools"] is True
    assert r.model_capabilities("ollama", "qwen2.5:3b")["agent_tools"] is False
    # Not in the gateway/Obsidian catalog: CLI/bridge routing only.
    assert "ollama" not in [p["id"] for p in r.catalog_providers()]


def test_bash_defaults_file_is_in_sync_with_registry():
    committed = (ROOT / "deploy" / "bin" / "model-defaults.sh").read_text(encoding="utf-8")
    assert committed == r.render_bash_defaults(), (
        "deploy/bin/model-defaults.sh is stale; regenerate: "
        "python -m rtime_models gen-bash-defaults > deploy/bin/model-defaults.sh"
    )


def test_registry_carries_no_secret_values():
    text = (ROOT / "packages" / "rtime-models" / "model-registry.json").read_text(encoding="utf-8")
    # Secret *names* (…_API_KEY, …_KEYFILE) are fine; secret *values* must never appear.
    assert "sk-" not in text
    assert "Bearer " not in text


# --------------------------------------------------------------- K2 manage.py
# Registry management verbs: every edit validates the merged result before it can
# be saved; probe reads only "is the secret SET", never its value.

def _mini_reg() -> dict:
    caps = dict.fromkeys(r.CAPABILITY_KEYS, True)
    return {
        "schema_version": 1,
        "default_model": "",
        "providers": [
            {
                "id": "p1",
                "label": "P1",
                "protocol": "openai-chat",
                "base_url": "https://p1.example/v1",
                "secret_env_names": ["P1_KEY", "P1_KEY_FILE"],
                "models": [{"id": "m1", "aliases": ["a1"], "capabilities": caps}],
            }
        ],
    }


def test_manage_validate_rejects_unknown_default():
    from rtime_models.manage import validate_registry

    reg = _mini_reg()
    reg["default_model"] = "no-such-model"
    assert any("default_model" in e for e in validate_registry(reg))


def test_manage_add_provider_validates_merged_result():
    from rtime_models.manage import add_provider

    reg = _mini_reg()
    caps = dict.fromkeys(r.CAPABILITY_KEYS, False)
    good = {
        "id": "p2",
        "label": "P2",
        "protocol": "openai-chat",
        "models": [{"id": "m2", "aliases": [], "capabilities": caps}],
    }
    merged, errors = add_provider(reg, good)
    assert errors == [] and [p["id"] for p in merged["providers"]] == ["p1", "p2"]
    assert [p["id"] for p in reg["providers"]] == ["p1"]  # 输入不被改动

    # 重复 id 拒绝
    dup, errors = add_provider(merged, good)
    assert dup is None and "already exists" in errors[0]
    # 别名撞现有模型拒绝(整体校验兜住)
    clash = {**good, "id": "p3", "models": [{"id": "m3", "aliases": ["a1"], "capabilities": caps}]}
    bad, errors = add_provider(merged, clash)
    assert bad is None and any("alias" in e for e in errors)


def test_manage_remove_provider_guards_default():
    from rtime_models.manage import remove_provider, set_default_model

    reg = _mini_reg()
    with_default, errors = set_default_model(reg, "a1")  # alias 也合法
    assert errors == []
    refused, errors = remove_provider(with_default, "p1")
    assert refused is None and "set-default first" in errors[0]
    # 清默认后可删
    cleared, _ = set_default_model(with_default, "")
    removed, errors = remove_provider(cleared, "p1")
    assert errors == [] and removed["providers"] == []
    # 不存在的 id
    none_, errors = remove_provider(reg, "ghost")
    assert none_ is None and "no provider" in errors[0]


def test_manage_set_default_rejects_unknown():
    from rtime_models.manage import set_default_model

    bad, errors = set_default_model(_mini_reg(), "ghost-model")
    assert bad is None and errors


def test_manage_save_registry_roundtrip(tmp_path):
    import json

    from rtime_models.manage import save_registry

    path = tmp_path / "reg.json"
    save_registry(_mini_reg(), path)
    assert json.loads(path.read_text(encoding="utf-8")) == _mini_reg()
    assert not (tmp_path / "reg.json.tmp").exists()  # 原子写不留临时文件


def test_probe_secret_env_and_keyfile(tmp_path):
    from rtime_models.manage import probe_provider

    p = _mini_reg()["providers"][0]
    # 什么都没设 -> secret_present False
    res = probe_provider(p, env={}, check_url=False)
    assert res["secret_required"] is True and res["secret_present"] is False
    assert res["reachable"] is None  # check_url=False 不碰网络
    # 直接 env 设了 -> True
    res = probe_provider(p, env={"P1_KEY": "x"}, check_url=False)
    assert res["secret_present"] is True and res["secret_env_found"] == ["P1_KEY"]
    # *_FILE 指向不存在的文件 -> 不算,记进 keyfile_missing
    res = probe_provider(p, env={"P1_KEY_FILE": str(tmp_path / "nope")}, check_url=False)
    assert res["secret_present"] is False and res["keyfile_missing"] == ["P1_KEY_FILE"]
    # *_FILE 指向存在的文件 -> 算
    keyfile = tmp_path / "key"
    keyfile.write_text("secret\n", encoding="utf-8")
    res = probe_provider(p, env={"P1_KEY_FILE": str(keyfile)}, check_url=False)
    assert res["secret_present"] is True
    # 无密钥要求的 provider -> secret_present None(无所谓有没有)
    res = probe_provider({"id": "open", "secret_env_names": []}, env={}, check_url=False)
    assert res["secret_required"] is False and res["secret_present"] is None


def test_probe_reachability_against_local_http():
    import http.server
    import threading

    from rtime_models.manage import probe_provider

    server = http.server.HTTPServer(("127.0.0.1", 0), http.server.BaseHTTPRequestHandler)
    thread = threading.Thread(target=server.handle_request, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        # BaseHTTPRequestHandler 对未实现的 GET 回 501 —— 有 HTTP 应答就算活着
        res = probe_provider(
            {"id": "local", "secret_env_names": [], "base_url": f"http://127.0.0.1:{port}/"},
            env={},
            timeout=3.0,
        )
        assert res["reachable"] is True and res["http_status"] == 501
    finally:
        server.server_close()
        thread.join(timeout=5)
    # 关掉的端口 -> reachable False + error
    res = probe_provider(
        {"id": "dead", "secret_env_names": [], "base_url": f"http://127.0.0.1:{port}/"},
        env={},
        timeout=0.5,
    )
    assert res["reachable"] is False and res["error"]


def test_cli_edit_roundtrip_on_tmp_registry(tmp_path, monkeypatch, capsys):
    import json

    from rtime_models.__main__ import main

    path = tmp_path / "reg.json"
    path.write_text(json.dumps(_mini_reg(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    monkeypatch.setenv("RTIME_MODEL_REGISTRY", str(path))

    caps = dict.fromkeys(r.CAPABILITY_KEYS, False)
    newp = tmp_path / "p2.json"
    newp.write_text(
        json.dumps({"id": "p2", "label": "P2", "protocol": "openai-chat",
                    "models": [{"id": "m2", "aliases": [], "capabilities": caps}]}),
        encoding="utf-8",
    )
    assert main(["add-provider", str(newp)]) == 0
    assert main(["set-default", "m2"]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [p["id"] for p in saved["providers"]] == ["p1", "p2"]
    assert saved["default_model"] == "m2"
    # 默认还路由在 p2 上,删 p2 被拒
    assert main(["remove-provider", "p2"]) == 1
    assert main(["set-default", ""]) == 0
    assert main(["remove-provider", "p2"]) == 0
    saved = json.loads(path.read_text(encoding="utf-8"))
    assert [p["id"] for p in saved["providers"]] == ["p1"]
    # probe --no-net 输出 JSON
    assert main(["probe", "--no-net"]) == 0
    out = capsys.readouterr().out
    assert '"results"' in out
    r.load_registry(force_reload=True)  # 还原缓存,避免污染后续用真 registry 的测试
