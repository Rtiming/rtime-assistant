# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Wire the file-backed store + app from environment variables.

Environment contract (``python -m rtime_admin_api``):

  RTIME_ADMIN_STORE_DIR   required. One deployment's admin state lives here:
                            config.json     (non-secret values, FileBackend)
                            secrets.json    (0600, FileBackend)
                            history/        (FileHistory snapshots, 0600 each)
                            audit.jsonl     (JsonlAuditSink, append-only)
                            salt            (0600; the per-store secret_salt,
                                             persisted so audit-diff hashes AND
                                             config ETags stay stable across
                                             process restarts)
  RTIME_ADMIN_API_KEYS    required. Path to the bearer-keys JSON (never in
                          git; see keys.example.json for the shape).
  RTIME_ADMIN_API_HOST    default 127.0.0.1 — loopback only unless explicitly
                          opted in (see below).
  RTIME_ADMIN_API_PORT    default 8790.
  RTIME_ADMIN_API_ALLOW_NONLOOPBACK
                          must be truthy (1/true/yes/on) to bind a non-loopback
                          host; otherwise a non-loopback HOST is REFUSED at
                          startup, not merely warned (defect #13). Deliberate
                          friction: this API manages credentials and must never
                          be reachable from the public internet by accident.
  RTIME_PROFILES_ROOT     optional. The git ``profiles/`` tree (read-only bind in
                          prod, ``/etc/rtime/profiles`` by convention). When set,
                          ``POST /v1/profiles/{id}:reload`` is enabled — it compiles
                          ``<root>/<id>/profile.yaml`` through rtime-config's loader
                          and hot-swaps the store's profile layer (T8, design §6.2).
                          Unset => the reload endpoint returns 501 (not wired).

The store dir is created 0700 and every file within (config/secrets/history/
audit/salt/lock) is owner-only. Refusing to start without a keys file is
deliberate: an unauthenticated admin API is not a mode this service has.
"""

from __future__ import annotations

import os
import secrets
from pathlib import Path

from fastapi import FastAPI
from rtime_admin_core import (
    ConfigStore,
    FileBackend,
    FileHistory,
    JsonlAuditSink,
    Registry,
    default_registry,
)

from .app import create_app
from .auth import load_api_keys

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8790

# NOTE: filename intentionally avoids the "secret"/"token" substrings so ad-hoc
# repo-wide gitignore patterns (`*secret*`) can never be what protects it — the
# 0600 mode and the out-of-repo store dir are the protection.
SALT_FILENAME = "salt"


def _ensure_store_dir(directory: Path) -> None:
    """Create ``directory`` 0700 and tighten it if it already exists (defect #11).

    The whole store dir holds config/secrets/history/audit/salt; owner-only is
    the correct posture. ``chmod`` after ``mkdir`` fixes a pre-existing 0755.
    """
    directory.mkdir(parents=True, exist_ok=True)
    try:
        directory.chmod(0o700)
    except OSError:  # pragma: no cover - platform without chmod
        pass


def load_or_create_salt(store_dir: str | Path) -> str:
    """Read the per-store salt, creating it (0600) on first boot.

    Persisting the salt in the store dir keeps two things stable across
    restarts: the keyed audit-diff secret digests and the config ETag.
    """
    directory = Path(store_dir)
    path = directory / SALT_FILENAME
    if path.exists():
        salt = path.read_text(encoding="utf-8").strip()
        if salt:
            return salt
    salt = secrets.token_hex(16)
    _ensure_store_dir(directory)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        os.fchmod(fd, 0o600)  # O_CREAT does not re-chmod a pre-existing file
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(salt + "\n")
    except BaseException:
        try:
            os.close(fd)
        except OSError:
            pass
        raise
    return salt


def build_store(
    store_dir: str | Path,
    *,
    registry: Registry | None = None,
    env: dict[str, str] | None = None,
) -> tuple[ConfigStore, JsonlAuditSink]:
    """A file-backed ConfigStore rooted at ``store_dir`` + its audit sink.

    The default registry includes the REAL ``qq`` module when it is importable: a
    profile layer compiles to ``qq.*`` paths, so the store that must validate a
    ``:reload`` has to know that module or every reload would 422 on an unknown path.
    ``QQBridgeConfig`` depends only on rtime-config/rtime-admin-core (NOT the chat
    runtime), so an admin-api image that ships it can validate profiles; if it is not
    importable, admin-api still boots — only the ``:reload`` of ``qq.*`` paths is then
    unavailable (best-effort, never a startup failure).
    """
    directory = Path(store_dir)
    _ensure_store_dir(directory)
    backend = FileBackend(directory / "config.json", directory / "secrets.json")
    history = FileHistory(directory / "history")
    sink = JsonlAuditSink(directory / "audit.jsonl")
    return (
        ConfigStore(
            registry if registry is not None else _default_registry_with_qq(),
            backend,
            history,
            audit_hook=sink,
            secret_salt=load_or_create_salt(directory),
            env=env,
        ),
        sink,
    )


def _default_registry_with_qq() -> Registry:
    """``default_registry`` with the qq module IF importable (best-effort, §6.1).

    The qq module lets the reload endpoint validate/project ``qq.*`` profile paths.
    admin-api must still boot when qq-bridge is not installed in its image, so a
    missing module downgrades to the sample-only registry rather than crashing.
    """
    try:
        return default_registry(include_qq=True)
    except ModuleNotFoundError:
        return default_registry()


def make_profile_loader(profiles_root: str | Path, store: ConfigStore):
    """A ``profile_id -> compiled flat layer`` callable for the reload endpoint.

    Compiles ``<profiles_root>/<profile_id>/profile.yaml`` through rtime-config's
    ``load_profile`` (single-level extends + file refs + x-secret door + projection),
    validated against the store's registry (env-independent). Returns the compiled
    ``.layer`` (a flat ``{module.field: value}`` map) — exactly what
    ``ConfigStore.reload_profile`` expects. Raises ``FileNotFoundError`` for an
    unknown profile id (mapped to 404 by the endpoint); any other loader failure
    (bad YAML, inlined secret, projection/validation error) propagates for the
    endpoint to map to 422. The rtime-config import is LOCAL so admin-api keeps no
    hard dependency on the loader when the reload feature is unused.
    """
    root = Path(profiles_root)

    def _load(profile_id: str) -> dict:
        from rtime_admin_core import validate_state
        from rtime_config.profile import load_profile

        profile_dir = root / profile_id
        if not (profile_dir / "profile.yaml").is_file():
            raise FileNotFoundError(f"no profile.yaml under {profile_dir}")
        compiled = load_profile(
            profile_dir,
            registry=store.registry,
            profiles_root=root,
            validate=validate_state,
        )
        return compiled.layer

    return _load


def make_models_hooks():
    """K2:models 目录/探测的注入件 ``(catalog, probe)``;rtime_models 不可导入时
    ``(None, None)``(端点回 501)——best-effort,同 build_store 的 qq 模块纹理,
    绝不因缺一个可选包拒绝启动。catalog 每次 force_reload:registry 文件被 CLI
    (add-provider/remove-provider/set-default)原子改写后,面板下一次读就是新的。"""
    try:
        import rtime_models
        from rtime_models import manage as models_manage
    except ImportError:
        return None, None

    def _catalog() -> dict:
        return rtime_models.load_registry(force_reload=True)

    def _probe(*, provider_id=None, timeout=3.0, check_url=True) -> list[dict]:
        return models_manage.probe_registry(
            rtime_models.load_registry(force_reload=True),
            provider_id=provider_id,
            timeout=timeout,
            check_url=check_url,
        )

    return _catalog, _probe


def make_modules_report(manifest_path: str | Path, *, environ: dict[str, str] | None = None):
    """K5:``GET /v1/modules`` 的注入件——modules.json 的 doctor 报告 callable。

    每次调用重读 manifest(改了 modules.json 面板刷新即见);enabled profiles 取
    ``COMPOSE_PROFILES``(逗号分隔,compose 的原生开关);compose 文件默认取 manifest
    同仓库的 compose.prod.yml(``RTIME_COMPOSE_FILE`` 覆盖),docs 存在性按 manifest
    所在仓库根(deploy/..)判。"""
    from rtime_admin_core.modules import load_manifest, manifest_report, validate_manifest
    from rtime_admin_core.modules_cli import _compose_profiles
    from rtime_admin_core.registry import KNOWN_MODULE_NAMES

    env = os.environ if environ is None else environ
    manifest = Path(manifest_path)
    repo_root = manifest.resolve().parent.parent  # deploy/modules.json -> repo root
    compose = Path(env.get("RTIME_COMPOSE_FILE") or (repo_root / "compose.prod.yml"))

    def _report() -> dict:
        modules = load_manifest(manifest.read_text(encoding="utf-8"))
        enabled = {
            s.strip() for s in (env.get("COMPOSE_PROFILES") or "").split(",") if s.strip()
        }
        issues = validate_manifest(
            modules,
            known_config_modules=set(KNOWN_MODULE_NAMES),
            known_profiles=_compose_profiles(compose),
            docs_exists=lambda rel: (repo_root / rel).exists(),
        )
        return manifest_report(modules, issues, enabled_profiles=enabled)

    return _report


def app_from_env(environ: dict[str, str] | None = None) -> FastAPI:
    """Build the FastAPI app from the environment contract above.

    Raises ``ValueError`` with an actionable message on any missing/invalid
    piece (``__main__`` turns that into a clean exit-2, not a traceback).
    """
    from . import __version__  # local import: avoid a circular module import

    env = os.environ if environ is None else environ
    store_dir = (env.get("RTIME_ADMIN_STORE_DIR") or "").strip()
    if not store_dir:
        raise ValueError(
            "RTIME_ADMIN_STORE_DIR is not set (the directory that holds "
            "config.json/secrets.json/history/audit.jsonl for this deployment)"
        )
    keys_path = (env.get("RTIME_ADMIN_API_KEYS") or "").strip()
    if not keys_path:
        raise ValueError(
            "RTIME_ADMIN_API_KEYS is not set (path to the bearer-keys JSON; "
            "an admin API without auth is not a supported mode)"
        )
    api_keys = load_api_keys(keys_path)
    store, sink = build_store(store_dir)
    # T8: enable POST /v1/profiles/{id}:reload only when a profiles tree is pointed at
    # (RTIME_PROFILES_ROOT). Unset => profile_loader=None => the endpoint returns 501.
    profiles_root = (env.get("RTIME_PROFILES_ROOT") or "").strip()
    profile_loader = (
        make_profile_loader(profiles_root, store) if profiles_root else None
    )
    models_catalog, models_probe = make_models_hooks()
    # K5: enable GET /v1/modules only when the manifest is pointed at
    # (RTIME_MODULES_MANIFEST, 常规=<repo>/deploy/modules.json)。Unset => 501。
    manifest_path = (env.get("RTIME_MODULES_MANIFEST") or "").strip()
    modules_report = (
        make_modules_report(manifest_path, environ=env) if manifest_path else None
    )
    # lock_dir = the store dir so the cross-process mutation flock lives with the
    # data it guards (defect #9); the HTTP server and the future L3 CLI share it.
    return create_app(
        store,
        api_keys=api_keys,
        audit_reader=sink.read_all,
        version=__version__,
        lock_dir=store_dir,
        profile_loader=profile_loader,
        models_catalog=models_catalog,
        models_probe=models_probe,
        modules_report=modules_report,
    )


LOOPBACK_HOSTS = {"127.0.0.1", "::1", "localhost"}
_TRUTHY = {"1", "true", "yes", "on"}


def host_port_from_env(environ: dict[str, str] | None = None) -> tuple[str, int]:
    """Resolve + validate the bind host/port.

    A non-loopback HOST is REFUSED (ValueError) unless
    ``RTIME_ADMIN_API_ALLOW_NONLOOPBACK`` is truthy — an accidental public bind
    of a credential-管理 API should fail startup, not merely warn (defect #13).
    """
    env = os.environ if environ is None else environ
    host = (env.get("RTIME_ADMIN_API_HOST") or DEFAULT_HOST).strip() or DEFAULT_HOST
    raw_port = (env.get("RTIME_ADMIN_API_PORT") or str(DEFAULT_PORT)).strip()
    try:
        port = int(raw_port)
    except ValueError as exc:
        raise ValueError(
            f"RTIME_ADMIN_API_PORT is not an integer: {raw_port!r}"
        ) from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"RTIME_ADMIN_API_PORT out of range 1..65535: {port}")
    allow_nonloopback = (
        env.get("RTIME_ADMIN_API_ALLOW_NONLOOPBACK") or ""
    ).strip().lower() in _TRUTHY
    if host not in LOOPBACK_HOSTS and not allow_nonloopback:
        raise ValueError(
            f"RTIME_ADMIN_API_HOST={host!r} is not a loopback address; refusing "
            "to bind a non-loopback host. This API manages credentials and must "
            "never be reachable from the public internet. If you really intend a "
            "Tailscale-interface bind, set RTIME_ADMIN_API_ALLOW_NONLOOPBACK=1."
        )
    return host, port
