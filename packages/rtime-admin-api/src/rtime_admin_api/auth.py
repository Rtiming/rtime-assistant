# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Bearer-key auth for the admin API — the L2 security gate.

Key file
--------
Operator-managed JSON, OUTSIDE git, pointed at by ``RTIME_ADMIN_API_KEYS``::

    [{"name": "ops-agent", "key": "<random>", "scopes": ["read", "write"]}]

See ``keys.example.json`` (obviously-fake values) for the shape. Key names
become the audit ``actor``, so they must be unique.

Scope model
-----------
  - ``read``            — every GET plus the dry-run POSTs (validate / diff).
  - ``write``           — PATCH /v1/config and POST /v1/rollback.
  - ``read:sensitive``  — required for ``?reveal=1`` (plaintext secrets).
  - field scopes        — a field whose schema declares ``x-scope`` (e.g.
    ``write:models``) ADDITIONALLY requires that exact scope string to be
    changed via PATCH; a field without ``x-scope`` is open to plain ``write``.

Rules
-----
  - Localhost bind does NOT waive auth: every endpoint (health included)
    authenticates — defense in depth for a config/credential surface.
  - Token comparison is constant-time (``hmac.compare_digest``) and scans ALL
    configured keys on every attempt so hit/miss timing stays uniform.
  - 401 = missing/invalid credentials (with ``WWW-Authenticate: Bearer``);
    403 = valid key, insufficient scope. Distinguished deliberately so an agent
    knows whether to re-authenticate or to ask for more scope.
"""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from rtime_admin_core.rbac import Capability, Principal, Role, can

from .errors import ApiError

SCOPE_READ = "read"
SCOPE_WRITE = "write"
SCOPE_READ_SENSITIVE = "read:sensitive"

# Refuse trivially guessable tokens outright; real keys should be ~32+ random
# chars (e.g. `python -c "import secrets; print(secrets.token_urlsafe(32))"`).
MIN_KEY_LENGTH = 16


@dataclass(frozen=True)
class ApiKey:
    """One configured bearer key. ``name`` is the audit actor.

    J4(token 分级/TTL/吊销,config-and-access §3.2):可选 ``expires_at``(ISO8601,
    到期后拒;None=不过期,向后兼容)+ ``revoked``(即时吊销)。给每个消费方(MCP/QQ/
    飞书/面板)发独立、可吊销、可到期的 token,避免共用一张全权 bearer(Coolify 反模式)。
    """

    name: str
    key: str
    scopes: frozenset[str]
    expires_at: str | None = None  # ISO8601;None=不过期
    revoked: bool = False
    # J3 接线(config-and-access §一):把 token 映射到 RBAC 两层身份。这两个字段是
    # scope 之外的正交维度——scope 管"能调哪些 API 动词",RBAC 管"平台超管 vs 项目角色"。
    # 现有端点仍走 scope 门(不变);capability 门(require_capability)给审核台等新端点用。
    is_platform_super: bool = False
    project_roles: dict[str, str] = field(default_factory=dict)  # {project: role名}

    def has_scope(self, scope: str) -> bool:
        return scope in self.scopes

    def is_expired(self, now_iso: str | None) -> bool:
        """到期判定。now_iso=None(调用方没给时钟)=不判过期(向后兼容)。"""
        if self.expires_at is None or now_iso is None:
            return False
        return now_iso >= self.expires_at  # ISO8601 字典序即时间序

    def principal(self) -> Principal:
        """映射成 RBAC Principal(J3):平台超管 + 每 project 角色。"""
        roles: dict[str, Role] = {}
        for proj, role_name in self.project_roles.items():
            roles[proj] = Role(role_name)
        return Principal(
            id=self.name,
            is_platform_super=self.is_platform_super,
            project_roles=roles,
        )


def load_api_keys(path: str | Path) -> list[ApiKey]:
    """Parse + sanity-check the operator's keys file.

    Raises ``ValueError`` (not ApiError — this is startup, not request time) on:
    missing/invalid file, empty list, non-dict entries, missing/empty ``name``
    or ``key``, keys shorter than :data:`MIN_KEY_LENGTH`, unknown-typed
    ``scopes``, duplicate names (audit actors must be unambiguous), duplicate
    key strings (a shared token defeats per-actor audit).
    """
    p = Path(path)
    if not p.is_file():
        raise ValueError(f"API keys file not found: {p}")
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"API keys file is not valid JSON: {p} ({exc})") from exc
    if not isinstance(data, list) or not data:
        raise ValueError("API keys file must be a non-empty JSON array")

    keys: list[ApiKey] = []
    names: set[str] = set()
    raw_keys: set[str] = set()
    for i, item in enumerate(data):
        where = f"keys[{i}]"
        if not isinstance(item, dict):
            raise ValueError(f"{where}: entry must be an object")
        name = item.get("name")
        key = item.get("key")
        scopes = item.get("scopes")
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"{where}: 'name' must be a non-empty string")
        if not isinstance(key, str) or len(key) < MIN_KEY_LENGTH:
            raise ValueError(
                f"{where}: 'key' must be a string of at least "
                f"{MIN_KEY_LENGTH} characters"
            )
        if not isinstance(scopes, list) or not all(
            isinstance(s, str) and s.strip() for s in scopes
        ):
            raise ValueError(f"{where}: 'scopes' must be a list of non-empty strings")
        name = name.strip()
        if name in names:
            raise ValueError(f"{where}: duplicate key name {name!r}")
        if key in raw_keys:
            raise ValueError(f"{where}: duplicate key value (names {name!r})")
        # J4: optional TTL + revocation (backward compatible — absent = no expiry, live)
        expires_at = item.get("expires_at")
        if expires_at is not None and (not isinstance(expires_at, str) or not expires_at.strip()):
            raise ValueError(f"{where}: 'expires_at' must be an ISO8601 string or absent")
        revoked = item.get("revoked", False)
        if not isinstance(revoked, bool):
            raise ValueError(f"{where}: 'revoked' must be a boolean")
        # J3: optional RBAC identity (backward compatible — absent = non-super, no roles)
        is_super = item.get("is_platform_super", False)
        if not isinstance(is_super, bool):
            raise ValueError(f"{where}: 'is_platform_super' must be a boolean")
        project_roles = item.get("project_roles", {})
        if not isinstance(project_roles, dict) or not all(
            isinstance(k, str) and isinstance(v, str) for k, v in project_roles.items()
        ):
            raise ValueError(f"{where}: 'project_roles' must be an object of project->role strings")
        valid_roles = {r.value for r in Role}
        for proj, role_name in project_roles.items():
            if role_name not in valid_roles:
                raise ValueError(
                    f"{where}: project_roles[{proj!r}]={role_name!r} not a valid role {sorted(valid_roles)}"
                )
        names.add(name)
        raw_keys.add(key)
        keys.append(
            ApiKey(
                name=name,
                key=key,
                scopes=frozenset(s.strip() for s in scopes),
                expires_at=expires_at.strip() if isinstance(expires_at, str) else None,
                revoked=revoked,
                is_platform_super=is_super,
                project_roles=dict(project_roles),
            )
        )
    return keys


def authenticate(
    authorization: str | None, keys: list[ApiKey], *, now_iso: str | None = None
) -> ApiKey:
    """Resolve an ``Authorization`` header to a configured key, or 401.

    Constant-time comparison against EVERY configured key (no early break) so
    the response timing does not reveal which — if any — key matched. J4: a
    matched token that is ``revoked`` or past ``expires_at`` (when ``now_iso``
    given) is rejected 401 — a valid token string is not enough, the identity
    must still be live.
    """
    if not authorization:
        raise ApiError(
            401, "unauthorized", "missing bearer token", www_authenticate=True
        )
    scheme, _, token = authorization.partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise ApiError(
            401,
            "unauthorized",
            "authorization must be 'Bearer <token>'",
            www_authenticate=True,
        )
    token_bytes = token.encode("utf-8")
    matched: ApiKey | None = None
    for candidate in keys:
        if hmac.compare_digest(candidate.key.encode("utf-8"), token_bytes):
            matched = candidate  # keep scanning: uniform timing
    if matched is None:
        raise ApiError(
            401, "unauthorized", "invalid bearer token", www_authenticate=True
        )
    if matched.revoked:
        raise ApiError(401, "unauthorized", "token revoked", www_authenticate=True)
    if matched.is_expired(now_iso):
        raise ApiError(401, "unauthorized", "token expired", www_authenticate=True)
    return matched


def require_scope(key: ApiKey, scope: str) -> None:
    """403 unless ``key`` holds ``scope``. Message names the scope, never the token."""
    if not key.has_scope(scope):
        raise ApiError(
            403, "forbidden", f"key {key.name!r} lacks required scope {scope!r}"
        )


def require_capability(
    key: ApiKey, capability: Capability, *, project: str | None = None
) -> None:
    """403 unless ``key`` 的 RBAC 身份能行使 ``capability``(J3 接线,config-and-access §一)。

    与 require_scope 正交:scope 管"能调哪个 API 动词",capability 管"平台超管 vs 项目
    角色"。SUPER_ONLY 能力仅 is_platform_super 放行;项目能力看该 project 的角色。审核台
    等新端点用它做能力门。消息只提能力/project,不提 token。
    """
    if not can(key.principal(), capability, project=project):
        where = f" on project {project!r}" if project else ""
        raise ApiError(
            403, "forbidden", f"key {key.name!r} lacks capability {capability.value!r}{where}"
        )


def scopes_summary(key: ApiKey) -> dict[str, Any]:
    """A redaction-safe descriptor of a key (never includes the token itself)."""
    return {
        "name": key.name,
        "scopes": sorted(key.scopes),
        "is_platform_super": key.is_platform_super,
        "project_roles": dict(key.project_roles),
    }
