# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""模型目录与选择子系统(能力/目录/刷新/选择)。共享件来自 _common,不依赖 gateway。"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from _common import csv_env, _read_secret  # noqa: F401 (_read_secret re-exported for providers/tests)
import rtime_models


def model_selection_supports_images(model_selection: dict | None) -> bool:
    capabilities = (model_selection or {}).get("capabilities") or {}
    return bool(capabilities.get("vision"))


def model_selection_supports_file_extract(model_selection: dict | None) -> bool:
    capabilities = (model_selection or {}).get("capabilities") or {}
    return bool(capabilities.get("file_extract")) or (model_selection or {}).get(
        "provider_id"
    ) in rtime_models.file_extract_provider_ids()


MODEL_PROTOCOLS = {"claude-wrapper/agent-tools", "anthropic-compatible", "openai-chat"}


def _capabilities(
    *,
    agent_tools: bool,
    code: bool,
    chat: bool = True,
    vision: bool = False,
    file_extract: bool = False,
    long_context: int | None = None,
    thinking: str = "provider-default",
) -> dict:
    return {
        "agent_tools": agent_tools,
        "code": code,
        "chat": chat,
        "vision": vision,
        "file_extract": file_extract,
        "long_context": long_context,
        "thinking": thinking,
    }


def _model(id_: str, label: str, protocol: str, capabilities: dict, **extra) -> dict:
    item = {
        "id": id_,
        "label": label,
        "protocol": protocol,
        "capabilities": capabilities,
    }
    item.update(extra)
    return item


def _moonshot_static_capabilities(model_id: str) -> dict:
    """Offline capability rule for a Moonshot/Kimi chat model id (used for env-added
    ids not described in the registry; refresh_model_catalog uses live API fields)."""
    return _capabilities(
        agent_tools=False,
        code=model_id.startswith("kimi-k2"),
        vision=model_id in {"kimi-k2.7-code", "kimi-k2.6"},
        file_extract=True,
        long_context=256000 if model_id.startswith("kimi-k2") else None,
        thinking="required" if model_id == "kimi-k2.7-code" else "supported",
    )


def _ustc_static_capabilities(model_id: str) -> dict:
    """Offline capability rule for a USTC chat model id (env-added ids)."""
    return _capabilities(
        agent_tools=False,
        code=False,
        long_context=None,
        thinking="supported" if "reason" in model_id else "provider-default",
    )


_CAPABILITY_RULES = {
    "moonshot": _moonshot_static_capabilities,
    "ustc": _ustc_static_capabilities,
}


def _capabilities_via_rule(rule: str | None, model_id: str) -> dict:
    fn = _CAPABILITY_RULES.get(rule or "")
    return fn(model_id) if fn else _capabilities(agent_tools=False, code=False)


def static_model_catalog(cfg: dict, errors: list[dict] | None = None) -> dict:
    """Non-secret model directory — a thin projection of
    packages/rtime-models/model-registry.json (catalog providers only). Still the
    offline fallback and the post-refresh shape; it never exposes key paths.

    Default model lists come from the registry yet stay env-overridable
    (RTIME_KIMI_OPENAI_MODELS / RTIME_USTC_MODELS via each provider's
    ``catalog_models_env``). A model present in the registry uses its stored
    capabilities; an env-added id falls back to the provider's capability rule kept
    in code, so dynamic ids keep working. base_url stays cfg-overridable."""
    providers: list[dict] = []
    for prov in rtime_models.catalog_providers():
        protocol = prov["protocol"]
        entry: dict = {"id": prov["id"], "label": prov["label"], "protocol": protocol}
        catalog_models = prov.get("catalog_models")
        if catalog_models is None:
            # Fixed-model providers (gateway-default, kimi-code-wrapper).
            models_out = []
            for reg_model in prov.get("models", []):
                extra: dict = {}
                if reg_model.get("default"):
                    extra["default"] = True
                cli_model = reg_model.get("cli_model")
                if cli_model:
                    extra["cli_model"] = cli_model
                models_out.append(
                    _model(
                        reg_model["id"],
                        reg_model.get("label", reg_model["id"]),
                        protocol,
                        dict(reg_model.get("capabilities", {})),
                        **extra,
                    )
                )
            entry["models"] = models_out
        else:
            # OpenAI-compatible providers (moonshot, ustc) with an env-overridable list.
            cfg_key = prov.get("base_url_cfg_key")
            base_url = cfg.get(cfg_key, prov.get("base_url")) if cfg_key else prov.get("base_url")
            entry["base_url_label"] = base_url
            env_name = prov.get("catalog_models_env")
            model_ids = csv_env(env_name, catalog_models) if env_name else list(catalog_models)
            rule = prov.get("capability_rule")
            models_out = []
            for model_id in model_ids:
                reg_model = rtime_models.model(prov["id"], model_id)
                if reg_model is not None:
                    capabilities = dict(reg_model.get("capabilities", {}))
                    label = reg_model.get("label", model_id)
                else:
                    capabilities = _capabilities_via_rule(rule, model_id)
                    label = model_id
                models_out.append(_model(model_id, label, protocol, capabilities))
            entry["models"] = models_out
        providers.append(entry)
    return {
        "schema_version": 1,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "providers": providers,
        "errors": errors or [],
    }


def _fetch_openai_models(base_url: str, token: str, timeout: float) -> list[dict]:
    request = urllib.request.Request(
        f"{base_url.rstrip('/')}/models",
        headers={"Authorization": f"Bearer {token}"},
        method="GET",
    )
    with urllib.request.urlopen(request, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    models = data.get("data")
    if not isinstance(models, list):
        raise RuntimeError("models response missing data[]")
    return [item for item in models if isinstance(item, dict) and isinstance(item.get("id"), str)]


def _replace_provider_models(catalog: dict, provider_id: str, models: list[dict]) -> None:
    for provider in catalog.get("providers", []):
        if provider.get("id") == provider_id:
            provider["models"] = models
            return


def refresh_model_catalog(cfg: dict) -> dict:
    errors: list[dict] = []
    catalog = static_model_catalog(cfg)
    timeout = float(cfg.get("model_refresh_timeout", 8))

    moonshot_key = _read_secret(
        ["RTIME_MOONSHOT_API_KEY", "MOONSHOT_API_KEY", "KIMI_API_KEY"],
        ["RTIME_MOONSHOT_API_KEY_FILE", "MOONSHOT_API_KEY_FILE", "KIMI_API_KEY_FILE"],
    )
    if moonshot_key:
        try:
            refreshed = []
            for item in _fetch_openai_models(cfg["moonshot_base_url"], moonshot_key, timeout):
                model_id = item["id"]
                refreshed.append(
                    _model(
                        model_id,
                        model_id,
                        "openai-chat",
                        _capabilities(
                            agent_tools=False,
                            code=model_id.startswith("kimi-k2"),
                            vision=bool(item.get("supports_image_in") or item.get("supports_video_in")),
                            file_extract=True,
                            long_context=item.get("context_length") if isinstance(item.get("context_length"), int) else None,
                            thinking="required" if model_id == "kimi-k2.7-code" else (
                                "supported" if item.get("supports_reasoning") else "provider-default"
                            ),
                        ),
                        owned_by=item.get("owned_by"),
                    )
                )
            if refreshed:
                _replace_provider_models(catalog, "moonshot-openai", refreshed)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append({"provider": "moonshot-openai", "type": type(exc).__name__, "message": str(exc)[:160]})
    else:
        errors.append({"provider": "moonshot-openai", "type": "missing_key", "message": "MOONSHOT/Kimi API key not configured"})

    ustc_key = _read_secret(["RTIME_USTC_API_KEY"], ["RTIME_USTC_API_KEY_FILE"], [cfg.get("ustc_api_key_file")])
    if ustc_key:
        try:
            refreshed = []
            for item in _fetch_openai_models(cfg["ustc_base_url"], ustc_key, timeout):
                model_id = item["id"]
                refreshed.append(
                    _model(
                        model_id,
                        model_id,
                        "openai-chat",
                        _capabilities(
                            agent_tools=False,
                            code=False,
                            long_context=item.get("context_length") if isinstance(item.get("context_length"), int) else None,
                            thinking="supported" if "reason" in model_id else "provider-default",
                        ),
                    )
                )
            if refreshed:
                _replace_provider_models(catalog, "ustc-openai", refreshed)
        except (OSError, urllib.error.URLError, urllib.error.HTTPError, json.JSONDecodeError, RuntimeError) as exc:
            errors.append({"provider": "ustc-openai", "type": type(exc).__name__, "message": str(exc)[:160]})
    else:
        errors.append({"provider": "ustc-openai", "type": "static_fallback", "message": "using RTIME_USTC_MODELS/static defaults"})

    catalog["errors"] = errors
    catalog["last_refreshed"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
    try:
        path = Path(cfg["model_catalog_path"])
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    except OSError as exc:
        catalog["errors"].append({"provider": "cache", "type": type(exc).__name__, "message": "model cache write failed"})
    return catalog


def get_model_catalog(cfg: dict) -> dict:
    path = Path(cfg.get("model_catalog_path", ""))
    if path.is_file():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if data.get("schema_version") == 1 and isinstance(data.get("providers"), list):
                return data
        except (OSError, json.JSONDecodeError):
            pass
    return static_model_catalog(cfg)


def resolve_model_selection(body: dict, cfg: dict) -> tuple[dict | None, str | None]:
    options = body.get("options") if isinstance(body.get("options"), dict) else {}
    provider_id = str(options.get("model_provider_id") or "").strip()
    model_id = str(options.get("model_id") or "").strip()
    protocol = str(options.get("model_protocol") or "").strip()
    if not provider_id and not model_id and not protocol:
        return None, None
    catalog = get_model_catalog(cfg)
    for provider in catalog.get("providers", []):
        if provider.get("id") != provider_id:
            continue
        provider_protocol = provider.get("protocol")
        for model in provider.get("models", []):
            if model.get("id") == model_id and model.get("protocol") == protocol and protocol in MODEL_PROTOCOLS:
                return {
                    "provider_id": provider_id,
                    "model_id": model_id,
                    "protocol": protocol,
                    "cli_model": model.get("cli_model", model_id),
                    "base_url": provider.get("base_url_label"),
                    "provider_protocol": provider_protocol,
                    "capabilities": model.get("capabilities") or {},
                }, None
    return None, f"模型选择不可用，已回退默认模型：provider={provider_id or '-'} model={model_id or '-'} protocol={protocol or '-'}"
