# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Web-enabled profiles for web-chat — the REAL profile loader (T5b).

``GET /api/profiles`` lists the web-enabled profiles from the git ``profiles/``
tree, compiled by the shared T1 loader (``rtime_config.profile.load_profile``). A
profile is web-enabled iff it declares a ``channels.web`` block (design §5.2). Each
is projected to the ``{id, name, description, system_prompt, read_only,
mcp_config, render}`` shape the server + frontend consume:

    {"id": str, "name": str, "description": str, "system_prompt": str,
     "read_only": bool, "mcp_config": str | None, "render": str}

The four PUBLIC keys (``PUBLIC_KEYS``) are all ``/api/profiles`` ever exposes —
``system_prompt`` / ``mcp_config`` are behaviour, never leaked to the browser.

Per-profile BEHAVIOR (same core as the QQ channel — the acceptance standard):
  - ``system_prompt``  = channels.web.system_prompt_file, else identity.system_prompt_file.
  - ``read_only``      = FAIL-CLOSED UNION of the profile's permissions.read_only OR
                         the process-wide ``WEB_CHAT_READ_ONLY`` env door. Env can only
                         ADD read_only (monotonic); it can NEVER pull a profile's
                         read_only:true down to False — do NOT reintroduce the env=0
                         downgrade bug. The read-only hard door (dontAsk + write-tool
                         deny + closed allowlist) is then enforced by the shared
                         ``ToolPolicy`` in ``web_chat.tool_policy`` exactly like QQ.
  - ``mcp_config``     = channels.web.mcp_servers (the gateway a web session reaches —
                         web is a gateway-only consumer), else plugins.mcp_servers.
                         For studentunion this is the scoped 8781 gateway, whose
                         library-policy.json (generated from library.scope) enforces
                         scope=knowledge/institutions/ustc + personal-data denial —
                         the SAME data-door the QQ session hits.
  - ``render``         = channels.web.render, else output.render, else "markdown".

Override for ad-hoc instances without the profiles tree (or a rebuild):
``RTIME_WEB_CHAT_PROFILES`` = an inline JSON array or a path to a JSON file, using
the same public+behavior shape. Fails fast (ValueError) on a malformed override —
a half-configured public instance must not come up silently.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .config import (
    DEFAULT_PROFILES_ROOT,
    PROFILES_ROOT_ENV,
    read_only_env_forces,
)

# Fallbacks steering the model toward direct chat answers when a profile / override
# omits a system prompt (mirrors the QQ/Feishu chat prompts; the web page renders
# markdown + KaTeX, so unlike QQ no plaintext-math constraint).
_OWNER_PROMPT = (
    "你是用户在网页端的个人助手。原则：① 直接、简洁地回答，像聊天，别啰嗦；"
    "② 闲聊就正常聊，不要为简单问题做多步任务或读库规范；"
    "③ 需要查资料时优先用可用的检索工具，拿到原文再答，简要标出处；"
    "④ 网页端支持Markdown与LaTeX公式渲染（行内$...$、独立$$...$$），可以正常使用。"
)

#: Keys exposed by GET /api/profiles (public view — never leak system_prompt/mcp_config).
PUBLIC_KEYS = ("id", "name", "description", "read_only")

#: The full behavior shape every consumer (server.py) depends on.
_BEHAVIOR_KEYS = (
    "id",
    "name",
    "description",
    "system_prompt",
    "read_only",
    "mcp_config",
    "render",
)


def _normalize(raw: dict) -> dict:
    """Normalize an override entry (or a compiled record) to the behavior shape."""
    if not isinstance(raw, dict) or not str(raw.get("id", "")).strip():
        raise ValueError(f"profile entry needs an 'id': {raw!r}")
    pid = str(raw["id"]).strip()
    # Fail-closed union even on the override path: a truthy env door can ADD
    # read_only, but the override's own read_only:true is never pulled down.
    read_only = bool(raw.get("read_only", False)) or read_only_env_forces()
    mcp_config = raw.get("mcp_config", None)
    return {
        "id": pid,
        "name": str(raw.get("name") or pid),
        "description": str(raw.get("description") or ""),
        "system_prompt": str(raw.get("system_prompt") or _OWNER_PROMPT),
        "read_only": read_only,
        "mcp_config": (str(mcp_config) if mcp_config is not None else None),
        "render": str(raw.get("render") or "markdown"),
    }


# --- override path (ad-hoc instances) -------------------------------------------


def _load_from_override(raw: str) -> list[dict]:
    text = (
        raw if raw.lstrip().startswith("[") else Path(raw).read_text(encoding="utf-8")
    )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"RTIME_WEB_CHAT_PROFILES is not valid JSON: {exc}") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("RTIME_WEB_CHAT_PROFILES must be a non-empty JSON array")
    profiles = [_normalize(item) for item in data]
    _reject_duplicate_ids(profiles)
    return profiles


# --- real loader path -----------------------------------------------------------


def _profiles_root() -> Path:
    return Path(os.getenv(PROFILES_ROOT_ENV, "").strip() or DEFAULT_PROFILES_ROOT)


def _project_compiled(compiled) -> dict:
    """Project a CompiledProfile (web-enabled) to the behavior shape.

    Reads the channel-NEUTRAL nested config (``compiled.config``) so a web session
    consumes the SAME permissions/library core as the QQ session, with the web
    channel's prompt/MCP/render overrides layered on top.
    """
    cfg = compiled.config
    web = cfg.channels.web  # guaranteed present (filter keeps only web-enabled)

    # system prompt: web override file content (already resolved into files by the
    # loader) else the identity prompt content. The loader records resolved file
    # CONTENT under compiled.files keyed by the projected path; for the web channel
    # we resolve the prompt file directly against the profile dir (see below).
    system_prompt = _resolve_web_prompt(compiled)

    # read_only: fail-closed union (profile OR env door). Env can only strengthen.
    read_only = bool(cfg.permissions.read_only) or read_only_env_forces()

    # mcp_config: web channel gateway override, else plugins.mcp_servers.
    mcp_servers = (
        web.mcp_servers if web.mcp_servers is not None else cfg.plugins.mcp_servers
    )
    mcp_config = _mcp_servers_to_config(mcp_servers)

    render = web.render or cfg.output.render or "markdown"
    name = web.name or cfg.identity.name or compiled.profile_id
    description = web.description or ""

    return {
        "id": compiled.profile_id,
        "name": str(name),
        "description": str(description),
        "system_prompt": system_prompt,
        "read_only": read_only,
        "mcp_config": mcp_config,
        "render": str(render),
    }


def _resolve_web_prompt(compiled) -> str:
    """The web session's system prompt: web override file, else identity file, else default."""
    cfg = compiled.config
    web = cfg.channels.web
    profile_dir = Path(compiled.source).parent
    ref = web.system_prompt_file or cfg.identity.system_prompt_file
    if not ref:
        return _OWNER_PROMPT
    # Reuse the loader's traversal-safe file reader (content, missing => hard error).
    from rtime_config.profile.loader import _read_ref_file

    return _read_ref_file(profile_dir, ref, want_content=True)


def _mcp_servers_to_config(mcp_servers) -> str | None:
    """Serialize channels.web.mcp_servers (or plugins.mcp_servers) to the mcp_config JSON.

    Reuses the loader's credential-scanning serializer (drops enabled=false, rejects
    inlined credentials, all-empty => the no-MCP sentinel). None => None (use the
    process default mcp_config).
    """
    if mcp_servers is None:
        return None
    from rtime_config.profile.loader import _mcp_config_json

    return _mcp_config_json(mcp_servers)


def _load_from_profiles_tree() -> list[dict]:
    """Compile every profile under the profiles root; keep the web-enabled ones."""
    # Lazy imports: the profile-consumption stack is only needed on this path.
    from rtime_admin_core import default_registry, validate_state
    from rtime_config.profile import ProfileError, load_profile

    root = _profiles_root()
    if not root.is_dir():
        raise ValueError(
            f"profiles root not found: {root} (set {PROFILES_ROOT_ENV} or "
            f"RTIME_WEB_CHAT_PROFILES for an ad-hoc instance)"
        )
    # The compiled layer carries qq.* keys (the projection table's targets), so the
    # registry must know the qq module for the loader's secret/validation doors; the
    # web-chat module is registered too (T5b coverage — same registry the panel uses).
    registry = default_registry(include_qq=True, include_web_chat=True)

    profiles: list[dict] = []
    for child in sorted(root.iterdir()):
        if not child.is_dir() or child.name.startswith(("_", ".")):
            continue  # skip _base and hidden dirs
        if not (child / "profile.yaml").is_file():
            continue
        compiled = load_profile(
            child, registry=registry, profiles_root=root, validate=validate_state
        )
        if compiled.config.channels.web is None:
            continue  # not web-enabled
        try:
            profiles.append(_project_compiled(compiled))
        except ProfileError as exc:
            raise ValueError(
                f"web profile {child.name!r} failed to project: {exc}"
            ) from exc

    if not profiles:
        raise ValueError(
            f"no web-enabled profiles found under {root} (a profile must declare a "
            "channels.web block to appear in the web dropdown)"
        )
    _reject_duplicate_ids(profiles)
    return profiles


def _reject_duplicate_ids(profiles: list[dict]) -> None:
    ids = [p["id"] for p in profiles]
    if len(set(ids)) != len(ids):
        raise ValueError(f"duplicate profile ids: {ids}")


# --- public API -----------------------------------------------------------------


def load_profiles() -> list[dict]:
    """Return the web-enabled profiles (behavior shape).

    Order matters: the first entry is the page's default selection. Precedence:
      1. ``RTIME_WEB_CHAT_PROFILES`` (inline JSON / path) — ad-hoc override;
      2. the git ``profiles/`` tree (``RTIME_PROFILES_ROOT``), compiled + filtered
         to ``channels.web``-declaring profiles.
    Fails fast (ValueError) on a malformed override or an empty/missing tree — a
    half-configured public instance must not come up silently.
    """
    raw = os.getenv("RTIME_WEB_CHAT_PROFILES", "").strip()
    if raw:
        return _load_from_override(raw)
    return _load_from_profiles_tree()


def get_profile(profiles: list[dict], profile_id: str) -> dict | None:
    for profile in profiles:
        if profile["id"] == profile_id:
            return profile
    return None


def public_view(profiles: list[dict]) -> list[dict]:
    return [{key: p[key] for key in PUBLIC_KEYS} for p in profiles]


__all__ = [
    "PUBLIC_KEYS",
    "load_profiles",
    "get_profile",
    "public_view",
]
