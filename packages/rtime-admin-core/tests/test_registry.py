# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Registry + sample-schema tests."""

from __future__ import annotations

import pytest
from pydantic_settings import BaseSettings, SettingsConfigDict
from rtime_admin_core import Registry, default_registry
from rtime_config import RtimeBaseSettings, config_field


class _Tiny(RtimeBaseSettings):
    model_config = SettingsConfigDict(env_prefix="")
    x: int = config_field(1, description="x")


def test_default_registry_lists_core_modules():
    # The always-on core modules: the two illustrative/real samples (channel-common,
    # models) + the three no-owning-app modules whose schema admin-core owns directly
    # (library-gateway, chat-runtime, sync — Syncthing is an external service, K3).
    # App/package opt-ins (qq/web-chat/feishu/qq-selfheal/ustc-kb) are added only via
    # the include_* flags.
    reg = default_registry()
    assert reg.list_modules() == [
        "channel-common",
        "chat-runtime",
        "library-gateway",
        "models",
        "sync",
    ]


def test_default_registry_include_qq_is_opt_in():
    # Default (include_qq=False) never touches the qq-bridge app, so admin-core's
    # own tests stay app-independent: exactly the 3 sample modules.
    reg = default_registry()
    assert "qq" not in reg.list_modules()


def test_register_qq_module_raises_without_app():
    # register_qq_module lazily imports the app; without qq_bridge on the path it
    # raises ModuleNotFoundError (callers wanting a soft dep guard it). Skip when
    # the app IS importable (e.g. workspace venv with everything on the path).
    import importlib.util

    from rtime_admin_core import Registry, register_qq_module

    if importlib.util.find_spec("qq_bridge") is not None:
        pytest.skip("qq-bridge is importable in this environment")
    reg = Registry()
    with pytest.raises(ModuleNotFoundError):
        register_qq_module(reg)


def test_default_registry_include_web_chat_is_opt_in():
    # Default (include_web_chat=False) never touches the web-chat app: exactly the
    # 3 sample modules (mirror of the qq opt-in).
    reg = default_registry()
    assert "web-chat" not in reg.list_modules()


def test_register_web_chat_module_raises_without_app():
    # register_web_chat_module lazily imports the web-chat app; without web_chat on
    # the path it raises ModuleNotFoundError. Skip when the app IS importable.
    import importlib.util

    from rtime_admin_core import Registry, register_web_chat_module

    if importlib.util.find_spec("web_chat") is not None:
        pytest.skip("web-chat is importable in this environment")
    reg = Registry()
    with pytest.raises(ModuleNotFoundError):
        register_web_chat_module(reg)


def test_get_schema_carries_rtime_metadata():
    reg = default_registry()
    schema = reg.get_schema("models")
    props = schema["properties"]
    # secret marked
    assert props["ustc_api_key"]["x-secret"] is True
    # hot vs restart
    assert props["default_model"]["x-reload"] == "hot"
    assert props["ustc_base_url"]["x-reload"] == "restart"
    # scope present
    assert props["default_model"]["x-scope"] == "write:models"
    # env aliases surfaced (legacy compatibility)
    assert props["default_model"]["x-env-aliases"] == ["DEFAULT_MODEL"]


def test_library_gateway_prefix_and_ranges():
    reg = default_registry()
    schema = reg.get_schema("library-gateway")
    props = schema["properties"]
    port = props["http_port"]
    assert port["maximum"] == 65535 and port["minimum"] == 1
    # idle_timeout is the one hot field here
    assert props["idle_timeout"]["x-reload"] == "hot"


def test_library_gateway_real_config_env_aliases_and_defaults():
    """The REAL gateway config (collection): every env the gateway reads is
    registered under the right field with its legacy alias, and the prewarm default
    matches the live runtime default (ON)."""
    reg = default_registry()
    props = reg.get_schema("library-gateway")["properties"]
    expected = {
        "http_host": ["RTIME_LIBRARY_GATEWAY_HTTP_HOST"],
        "http_port": ["RTIME_LIBRARY_GATEWAY_HTTP_PORT"],
        "socket_path": ["RTIME_LIBRARY_GATEWAY_SOCKET"],
        "idle_timeout": ["RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT"],
        "prewarm": ["RTIME_LIBRARY_GATEWAY_PREWARM"],
        "policy_path": ["RTIME_LIBRARY_GATEWAY_POLICY"],
        "audit_log": ["RTIME_LIBRARY_GATEWAY_AUDIT_LOG"],
        "index_path": ["BRAIN_LIBRARY_INDEX", "RTIME_LIBRARY_GATEWAY_INDEX"],
        "brain_root": ["BRAIN_ROOT", "RTIME_BRAIN_ROOT"],
        "hub_root": ["RTIME_HUB_ROOT"],
        "reminders_path": ["RTIME_REMINDERS_PATH"],
        "embed_model": ["BRAIN_LIBRARY_EMBED_MODEL"],
        "embed_model_dir": ["BRAIN_LIBRARY_EMBED_MODEL_DIR"],
    }
    for field, aliases in expected.items():
        assert field in props, f"missing field {field}"
        assert props[field]["x-env-aliases"] == aliases, field
        assert props[field]["x-scope"] == "write:library", field
    # prewarm default ON (== runtime default), no drift; no secret fields on this model
    assert props["prewarm"]["default"] is True
    for field in props.values():
        assert field.get("x-secret") is not True


def test_chat_runtime_registered_by_default():
    # chat-runtime is a zero-dep-leaf module owned in admin-core schemas.py (like
    # library-gateway), so it is always on default_registry with no opt-in flag.
    reg = default_registry()
    assert reg.has("chat-runtime")
    props = reg.get_schema("chat-runtime")["properties"]
    assert props["campus_urls_file"]["x-env-aliases"] == ["RTIME_CAMPUS_URLS_FILE"]
    assert props["campus_urls_file"]["x-reload"] == "hot"
    assert props["campus_urls_file"]["x-scope"] == "write:channel"


def test_default_registry_qq_selfheal_and_ustc_kb_are_opt_in():
    # The new app/package modules are added only via their include_* flags, so the
    # default (no flags) never touches those trees.
    reg = default_registry()
    assert "qq-selfheal" not in reg.list_modules()
    assert "ustc-kb" not in reg.list_modules()


def test_register_qq_selfheal_module_raises_without_app():
    import importlib.util

    from rtime_admin_core import Registry, register_qq_selfheal_module

    if importlib.util.find_spec("qq_bridge") is not None:
        pytest.skip("qq-bridge is importable in this environment")
    reg = Registry()
    with pytest.raises(ModuleNotFoundError):
        register_qq_selfheal_module(reg)


def test_register_ustc_kb_module_raises_without_package():
    import importlib.util

    from rtime_admin_core import Registry, register_ustc_kb_module

    if importlib.util.find_spec("ustc_kb.config_schema") is not None:
        pytest.skip("ustc-kb is importable in this environment")
    reg = Registry()
    with pytest.raises(ModuleNotFoundError):
        register_ustc_kb_module(reg)


def test_channel_common_legacy_aliases_kept():
    reg = default_registry()
    props = reg.get_schema("channel-common")["properties"]
    # new RTIME_CHAT_ name first, legacy unprefixed name kept
    assert props["read_only"]["x-env-aliases"] == ["RTIME_CHAT_READ_ONLY", "READ_ONLY"]
    assert "CLAUDE_MAX_TURNS" in props["max_turns"]["x-env-aliases"]


def test_register_rejects_dot_in_name():
    reg = Registry()
    with pytest.raises(ValueError):
        reg.register("a.b", _Tiny)


def test_register_rejects_duplicate():
    reg = Registry()
    reg.register("t", _Tiny)
    with pytest.raises(ValueError):
        reg.register("t", _Tiny)


def test_register_rejects_non_settings():
    reg = Registry()
    with pytest.raises(TypeError):
        reg.register("t", dict)  # type: ignore[arg-type]


def test_unknown_module_raises_keyerror():
    reg = default_registry()
    with pytest.raises(KeyError):
        reg.get_schema("nope")


def test_register_and_use_custom_settings_model():
    reg = Registry()
    reg.register("tiny", _Tiny)
    assert reg.has("tiny")
    assert reg.model("tiny") is _Tiny
    assert isinstance(reg.model("tiny")(), BaseSettings)
