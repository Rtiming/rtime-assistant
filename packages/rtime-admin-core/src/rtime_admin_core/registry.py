# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema registry — the map from a config *module* name to its settings model.

This is the L1 view of the L0 schemas (docs/development-plan.zh-CN.md §五). One
module = one pydantic-settings model = one JSON Schema. The registry is what an
admin API / panel enumerates to build the config tree and the auto-generated
forms; the ConfigStore reads it to know which fields exist, their defaults, and
their x-secret / x-reload / x-scope metadata.

A module name is the top-level segment of every dotted config path:

    models.default_model
    library-gateway.http_port
    channel-common.read_only

Kept deliberately small and explicit: registration is a plain dict, not import
magic, so the set of manageable modules is auditable at a glance.
"""

from __future__ import annotations

from typing import Any

from pydantic_settings import BaseSettings

from .schemas import (
    ChannelCommonConfig,
    ChatRuntimeConfig,
    LibraryGatewayConfig,
    ModelsConfig,
    SyncIntegrationConfig,
)


class Registry:
    """A registry of config modules (module name -> settings model class)."""

    def __init__(self) -> None:
        self._modules: dict[str, type[BaseSettings]] = {}

    def register(self, module: str, model: type[BaseSettings]) -> None:
        """Register ``model`` under ``module``. Re-registering is an error.

        ``module`` must be a non-empty string without a ``.`` (it is the first
        segment of a dotted path; a dot would make addressing ambiguous).
        """
        if not module or not isinstance(module, str):
            raise ValueError("module name must be a non-empty string")
        if "." in module:
            raise ValueError(f"module name must not contain '.': {module!r}")
        if module in self._modules:
            raise ValueError(f"module already registered: {module!r}")
        if not (isinstance(model, type) and issubclass(model, BaseSettings)):
            raise TypeError(f"model must be a BaseSettings subclass, got {model!r}")
        self._modules[module] = model

    def list_modules(self) -> list[str]:
        """Registered module names, sorted for stable output."""
        return sorted(self._modules)

    def has(self, module: str) -> bool:
        return module in self._modules

    def model(self, module: str) -> type[BaseSettings]:
        """The settings model class for ``module`` (raises ``KeyError`` if absent)."""
        try:
            return self._modules[module]
        except KeyError:
            raise KeyError(
                f"unknown config module: {module!r} "
                f"(known: {', '.join(self.list_modules()) or '<none>'})"
            ) from None

    def get_schema(self, module: str) -> dict[str, Any]:
        """The JSON Schema for ``module`` (``model_json_schema(by_alias=False)``).

        ``by_alias=False`` keeps property keys as the stable Python field names so
        every consumer addresses the same path; env aliases live under
        ``x-env-aliases`` (see rtime_config.fields).
        """
        return self.model(module).model_json_schema(by_alias=False)


# The qq module's settings model lives in the qq-bridge APP (apps/qq-bridge),
# not in a package admin-core can depend on. To keep admin-core a leaf (no reverse
# dependency on an app), it is imported LAZILY and only when a caller opts in via
# ``default_registry(include_qq=True)`` / ``register_qq_module``. The app puts
# ``qq_bridge`` on sys.path itself (its ``_runtime_path`` shim); admin-core just
# imports the class if it is importable.
_QQ_MODULE = "qq"
# The web-chat module's settings model lives in the web-chat APP (apps/web-chat),
# same as qq: imported LAZILY and only when a caller opts in, so admin-core stays a
# leaf (no reverse dependency on an app).
_WEB_CHAT_MODULE = "web-chat"
# The feishu module's settings model lives in the feishu-bridge APP
# (apps/feishu-bridge, flat module ``feishu_config``), same as qq/web-chat: imported
# LAZILY and only when a caller opts in, so admin-core stays a leaf.
_FEISHU_MODULE = "feishu"
# The assistant-gateway module's settings model lives in the assistant-gateway APP
# (apps/assistant-gateway, flat import-safe module ``gateway_config_schema``), same
# as qq/web-chat/feishu: imported LAZILY and only when a caller opts in, so admin-core
# stays a leaf.
_ASSISTANT_GATEWAY_MODULE = "assistant-gateway"
# The library-gateway module's REAL settings model lives IN admin-core's own
# ``schemas.py`` (LibraryGatewayConfig): the gateway package (packages/rtime-library
# -gateway) is a deliberate zero-dep runtime leaf that reads raw env, so its config
# schema is owned here rather than reverse-depended on. Hence — unlike qq/web-chat —
# registration needs no lazy app import and stays in ``default_registry`` for all
# callers; ``register_library_gateway_module`` mirrors the qq/web-chat opt-in shape
# for callers assembling a custom registry.
_LIBRARY_GATEWAY_MODULE = "library-gateway"
# The chat-runtime module's REAL settings model lives IN admin-core's own
# ``schemas.py`` (ChatRuntimeConfig): packages/rtime-chat-runtime is a deliberate
# zero-dep runtime leaf that reads raw env, so its (single) direct env knob's schema
# is owned here rather than reverse-depended on — same call as library-gateway. Hence
# registration needs no lazy app import and stays in ``default_registry`` for all
# callers; ``register_chat_runtime_module`` mirrors the opt-in shape for custom registries.
_CHAT_RUNTIME_MODULE = "chat-runtime"
# The qq-selfheal module's settings model lives in the qq-bridge APP
# (apps/qq-bridge, module ``qq_bridge.selfheal_config``), imported LAZILY and only when
# a caller opts in, so admin-core stays a leaf. The self-heal DAEMON itself
# (ops/qq_selfheal.py) is stdlib-only and never imports this — the schema is a
# registration/coverage mirror, not the daemon's config source.
_QQ_SELFHEAL_MODULE = "qq-selfheal"
# The ustc-kb module's settings model lives in the ustc-kb PACKAGE
# (packages/ustc-kb, module ``ustc_kb.config_schema``), imported LAZILY and only when a
# caller opts in. The crawler runtime (ustc_kb.config) stays stdlib-only and never
# imports this — the schema is a registration/coverage mirror.
_USTC_KB_MODULE = "ustc-kb"
# The sync module's settings model lives IN admin-core's own ``schemas.py``
# (SyncIntegrationConfig): Syncthing is an EXTERNAL service with no owning app in this
# repo — the module is the assistant-side pointer to it (notes_root + REST endpoint for
# the panel health probe, K3/K5). Same no-lazy-import shape as library-gateway.
_SYNC_MODULE = "sync"

# K1(module manifest 校验用):所有可注册配置模块的名字集合。静态常量,不触发任何
# app 懒导入——module doctor 可在任意环境校验 modules.json 的 config_module 是否真实存在。
# 含 always-on(models/library-gateway/channel-common/chat-runtime/sandbox)与 opt-in
# (qq/web-chat/feishu/assistant-gateway/qq-selfheal/ustc-kb)。
KNOWN_MODULE_NAMES: frozenset[str] = frozenset(
    {
        "models",
        "library-gateway",
        "channel-common",
        "chat-runtime",
        "sandbox",
        _QQ_MODULE,
        _WEB_CHAT_MODULE,
        _FEISHU_MODULE,
        _ASSISTANT_GATEWAY_MODULE,
        _QQ_SELFHEAL_MODULE,
        _USTC_KB_MODULE,
        _SYNC_MODULE,
    }
)


def register_qq_module(registry: Registry, *, module: str = _QQ_MODULE) -> None:
    """Register the real qq-bridge settings model under ``module`` (default ``qq``).

    Lazily imports ``QQBridgeConfig`` from the qq-bridge app so admin-core never
    hard-depends on an app. Raises ``ModuleNotFoundError`` (from the import) if the
    app is not importable — callers that want a soft dependency should guard it.
    """
    from qq_bridge.config import QQBridgeConfig  # lazy: app is not an admin-core dep

    registry.register(module, QQBridgeConfig)


def register_library_gateway_module(
    registry: Registry, *, module: str = _LIBRARY_GATEWAY_MODULE
) -> None:
    """Register the real library-gateway settings model under ``module``.

    Unlike qq/web-chat, ``LibraryGatewayConfig`` lives in admin-core's own
    ``schemas.py`` (the gateway package is a zero-dep runtime leaf), so this is a
    plain register with no lazy import — it never raises ``ModuleNotFoundError``.
    Provided for symmetry so callers building a custom registry can opt this module
    in the same way; ``default_registry`` already registers it for everyone.
    """
    registry.register(module, LibraryGatewayConfig)


def register_chat_runtime_module(
    registry: Registry, *, module: str = _CHAT_RUNTIME_MODULE
) -> None:
    """Register the chat-runtime settings model under ``module`` (default ``chat-runtime``).

    Like library-gateway, ``ChatRuntimeConfig`` lives in admin-core's own
    ``schemas.py`` (rtime-chat-runtime is a zero-dep runtime leaf), so this is a plain
    register with no lazy import — it never raises ``ModuleNotFoundError``. Provided
    for symmetry; ``default_registry`` already registers it for everyone.
    """
    registry.register(module, ChatRuntimeConfig)


def register_qq_selfheal_module(
    registry: Registry, *, module: str = _QQ_SELFHEAL_MODULE
) -> None:
    """Register the real qq-selfheal settings model under ``module`` (default ``qq-selfheal``).

    Lazily imports ``QQSelfhealConfig`` from the qq-bridge app so admin-core never
    hard-depends on an app (mirror of :func:`register_qq_module`). Raises
    ``ModuleNotFoundError`` (from the import) if the app is not importable — callers
    that want a soft dependency should guard it. Registering it makes the ops
    sidecar's ``SELFHEAL_*`` config panel-manageable (全覆盖) without touching the
    stdlib-only daemon.
    """
    from qq_bridge.selfheal_config import (  # lazy: app is not an admin-core dep
        QQSelfhealConfig,
    )

    registry.register(module, QQSelfhealConfig)


def register_ustc_kb_module(
    registry: Registry, *, module: str = _USTC_KB_MODULE
) -> None:
    """Register the real ustc-kb settings model under ``module`` (default ``ustc-kb``).

    Lazily imports ``UstcKbConfig`` from the ustc-kb package's import-safe
    ``config_schema`` module so admin-core never hard-depends on it at import time
    (mirror of :func:`register_qq_module`). Raises ``ModuleNotFoundError`` if it is
    not importable — callers that want a soft dependency should guard it. The crawler
    runtime (``ustc_kb.config``) is untouched (stays stdlib-only).
    """
    from ustc_kb.config_schema import UstcKbConfig  # lazy: not an import-time dep

    registry.register(module, UstcKbConfig)


def register_web_chat_module(
    registry: Registry, *, module: str = _WEB_CHAT_MODULE
) -> None:
    """Register the real web-chat settings model under ``module`` (default ``web-chat``).

    Lazily imports ``WebChatConfig`` from the web-chat app so admin-core never
    hard-depends on an app (mirror of :func:`register_qq_module`). Raises
    ``ModuleNotFoundError`` (from the import) if the app is not importable — callers
    that want a soft dependency should guard it. This is the T5b "coverage lane":
    registering web-chat makes its config fields panel-manageable (全覆盖).
    """
    from web_chat.config import WebChatConfig  # lazy: app is not an admin-core dep

    registry.register(module, WebChatConfig)


def register_feishu_module(registry: Registry, *, module: str = _FEISHU_MODULE) -> None:
    """Register the real feishu-bridge settings model under ``module`` (default ``feishu``).

    Lazily imports ``FeishuBridgeConfig`` from the feishu-bridge app so admin-core
    never hard-depends on an app (mirror of :func:`register_qq_module` /
    :func:`register_web_chat_module`). It imports from the app's ``feishu_config``
    module — which is import-safe (no credential loading, no side effects) — NOT the
    ``bot_config`` compatibility layer (which hard-loads credentials at import). Raises
    ``ModuleNotFoundError`` (from the import) if the app is not importable — callers
    that want a soft dependency should guard it. This is the P2 批 1 "coverage lane":
    registering feishu makes its config fields panel-manageable (全覆盖).
    """
    from feishu_config import FeishuBridgeConfig  # lazy: app is not an admin-core dep

    registry.register(module, FeishuBridgeConfig)


def register_assistant_gateway_module(
    registry: Registry, *, module: str = _ASSISTANT_GATEWAY_MODULE
) -> None:
    """Register the real assistant-gateway settings model under ``module``
    (default ``assistant-gateway``).

    Lazily imports ``AssistantGatewayConfig`` from the assistant-gateway app so
    admin-core never hard-depends on an app (mirror of :func:`register_qq_module` /
    :func:`register_feishu_module`). It imports from the app's import-safe
    ``gateway_config_schema`` module (no ``Path.home()`` / ``rtime_models`` / env
    reads at import) — NOT the ``gateway_config`` compatibility layer (which builds
    the runtime dict). Raises ``ModuleNotFoundError`` (from the import) if the app is
    not importable — callers that want a soft dependency should guard it. This is the
    P2 批 2 "coverage lane": registering assistant-gateway makes its config fields
    panel-manageable (全覆盖).
    """
    # lazy: app is not an admin-core dep
    from gateway_config_schema import AssistantGatewayConfig

    registry.register(module, AssistantGatewayConfig)


def register_sync_module(registry: Registry, *, module: str = _SYNC_MODULE) -> None:
    """Register the sync-integration settings model under ``module`` (default ``sync``).

    Like library-gateway/chat-runtime, ``SyncIntegrationConfig`` lives in admin-core's
    own ``schemas.py`` (Syncthing is an external service, no owning app), so this is a
    plain register with no lazy import — it never raises ``ModuleNotFoundError``.
    Provided for symmetry; ``default_registry`` already registers it for everyone.
    """
    registry.register(module, SyncIntegrationConfig)


def default_registry(
    *,
    include_qq: bool = False,
    include_web_chat: bool = False,
    include_feishu: bool = False,
    include_assistant_gateway: bool = False,
    include_qq_selfheal: bool = False,
    include_ustc_kb: bool = False,
) -> Registry:
    """A ``Registry`` pre-loaded with the core modules.

    Registers the REAL ``models`` module (model directory / routing domain, no single
    owning app so it lives in schemas.py) + the REAL ``library-gateway`` config (via
    :func:`register_library_gateway_module`; the gateway package is a zero-dep leaf so
    its schema is owned here) + the REAL ``chat-runtime`` config (its one direct env
    knob; rtime-chat-runtime is a zero-dep leaf so its schema is owned here too) + the
    still-illustrative ``channel-common`` sample, so the admin core is exercised
    against realistic field shapes end to end.
    ``include_qq=True`` also registers the REAL ``qq`` module (the
    qq-bridge settings model); ``include_web_chat=True`` registers the REAL
    ``web-chat`` module (the web-chat settings model); ``include_feishu=True``
    registers the REAL ``feishu`` module (the feishu-bridge settings model);
    ``include_assistant_gateway=True`` registers the REAL ``assistant-gateway`` module
    (the assistant-gateway settings model). All are used by the profile loader / their
    apps' tests / the coverage doctor; kept opt-in so admin-core's own tests (which do
    not put the apps on the path) and existing callers are unaffected
    ``include_qq_selfheal=True`` registers the REAL ``qq-selfheal`` module (the ops
    sidecar's settings model); ``include_ustc_kb=True`` registers the REAL ``ustc-kb``
    module (the crawler's settings model). All are used by the profile loader / their
    apps' tests / the coverage doctor; kept opt-in so admin-core's own tests (which do
    not put the apps/packages on the path) and existing callers are unaffected
    (backward-compatible default).
    """
    reg = Registry()
    reg.register("models", ModelsConfig)
    register_library_gateway_module(reg)
    register_chat_runtime_module(reg)
    register_sync_module(reg)
    reg.register("channel-common", ChannelCommonConfig)
    if include_qq:
        register_qq_module(reg)
    if include_web_chat:
        register_web_chat_module(reg)
    if include_feishu:
        register_feishu_module(reg)
    if include_assistant_gateway:
        register_assistant_gateway_module(reg)
    if include_qq_selfheal:
        register_qq_selfheal_module(reg)
    if include_ustc_kb:
        register_ustc_kb_module(reg)
    return reg
