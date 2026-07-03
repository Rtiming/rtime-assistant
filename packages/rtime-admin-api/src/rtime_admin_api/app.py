# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The FastAPI application — every endpoint is a thin shim over ``ConfigStore``.

Endpoints (all JSON, all authenticated, prefix ``/v1``)
------------------------------------------------------
  GET    /v1/schema             registry JSON Schemas (panel forms / agent introspection)
  GET    /v1/config             all resolved values, REDACTED by default;
                                ``?reveal=1`` needs the ``read:sensitive`` scope
  GET    /v1/config/{path}      one value (same redaction rules)
  POST   /v1/config/validate    dry-run; ALWAYS 200 ``{ok, errors}`` (the
                                validation ran fine — its verdict is the body);
                                never writes, never audits
  POST   /v1/config/diff        redacted before/after for a proposed change set
  PATCH  /v1/config             apply; REQUIRES ``If-Match`` (428 missing /
                                412 stale); 422 + field errors when invalid
  GET    /v1/history            snapshot descriptors (id/ts/note, no payload)
  POST   /v1/rollback           restore a snapshot; same ``If-Match`` rules
  GET    /v1/audit?limit=N      tail of the (already-redacted) audit log
  GET    /v1/health             {ok, version, needs_restart: [paths]}

Concurrency (Caddy-style ETag)
------------------------------
The config ETag is an HMAC-SHA256 (keyed with the store's persisted
``secret_salt``) over the canonical JSON of the store's PERSISTED layer
(``ConfigStore.persisted_flat`` — config+secrets, NOT the env-merged resolved
view). Hashing the persisted layer, not ``get_all``, is deliberate: an
env-pinned field's persisted write does not move the resolved view, so an ETag
over ``get_all`` would stay constant across a real write and silently drop the
concurrent update (defect #6, same class as L1 defect #2). Keying the digest
means the tag is server-side only and cannot offline-confirm a guessed secret.
Mutations run under one process-wide lock AND a cross-process file lock (flock
on ``<store>/.lock``): check ``If-Match`` against the current ETag, apply,
recompute — so neither two threads nor two processes (e.g. HTTP + the L3 CLI)
can interleave a read-modify-write. ``If-Match: *`` is rejected (400): opting
out of concurrency control is exactly the footgun this exists to stop; a
comma-separated tag LIST matches if ANY member equals the current tag (RFC 7232).

Secret non-disclosure (the invariant, enforced on EVERY surface)
----------------------------------------------------------------
A caller without ``read:sensitive`` must learn NOTHING about a secret's value
from any response — success or error. Concretely:
  - GET config redacts set secrets to ``***`` (unset stays ``None``).
  - diff/validate/apply responses run through :func:`_scope_redact_diff` /
    field-error redaction so a secret path a caller submits comes back as a
    constant ``***`` before/after (NOT the salted hmac, NOT dropped on a
    value match) — presence and content are independent of the submitted
    value, closing the equality-oracle (defect #1; the whole class of
    reflecting a secret-derived state back to the submitter).
  - pydantic error ``input``/``ctx`` AND ``message`` are all dropped/redacted
    for secret fields — every error face is covered, not just ``input``
    (defect #5, same class as L1 #6/#7).

L2 responsibilities kept here (and nowhere lower)
-------------------------------------------------
  - clock + ids: ``ts`` = UTC ISO-8601, ``snapshot_id`` = uuid4 hex; the core
    never self-generates either.
  - actor/source: audit ``actor`` is the bearer key's name, ``source="http"``.
  - field-scope on EVERY write verb: PATCH and rollback both compute the union
    of ``x-scope`` over their changed paths and require each (defect #2 — a
    write verb must never bypass field scope).
  - pending-restart tracking: every apply/rollback with a non-empty
    ``restart_required`` adds those paths to an in-process set surfaced by
    ``/v1/health``; the set clears on process restart — which is exactly the
    semantics (a restart is what was needed).

Error envelope: EVERY non-2xx body is ``{"error": {code, message[, errors]}}``,
including otherwise-uncaught 500s (a catch-all handler wraps them so the
contract never breaks and no traceback leaks).

Auth precedes body parsing: authentication runs as a router dependency BEFORE
the request body is read/validated, so a malformed/oversized body from an
unauthenticated caller is a 401, not a 422 (defect #3).

The OpenAPI documents (``/openapi.json``, ``/docs``, ``/redoc``) are DISABLED:
even field NAMES (e.g. ``ustc_api_key``) are information an unauthenticated
caller should not get (defect #4). Schema is served by the authenticated
``GET /v1/schema``; contract tests build the app in-process and can re-enable
docs there if needed.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import threading
import uuid
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from fastapi import Depends, FastAPI, Query, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from rtime_admin_core import (
    ApplyResult,
    ConfigStore,
    SnapshotNotFoundError,
    field_meta,
    is_secret,
    redact_all,
)
from rtime_admin_core import (
    ValidationError as AdminValidationError,
)
from rtime_admin_core.metadata import REDACTED_PLACEHOLDER
from rtime_admin_core.two_phase import plan_token as two_phase_token
from rtime_admin_core.two_phase import verify_token as verify_two_phase
from starlette.exceptions import HTTPException as StarletteHTTPException

from .auth import (
    SCOPE_READ,
    SCOPE_READ_SENSITIVE,
    SCOPE_WRITE,
    ApiKey,
    authenticate,
    require_scope,
)
from .bodies import ChangesBody, PatchBody, RollbackBody
from .errors import ApiError
from .locking import FileLock
from .panel import is_public_panel_path, register_panel

AuditReader = Callable[[], list[dict[str, Any]]]
# A profile loader: profile_id -> the compiled flat ``{module.field: value}`` layer
# (design §6.2 ``POST /v1/profiles/{id}:reload``). Injected by ``wiring`` so admin-api
# stays decoupled from rtime-config's loader (same seam pattern the loader itself uses
# to take an injected registry/validate). ``None`` disables the reload endpoint.
ProfileLoader = Callable[[str], dict[str, Any]]
# K2 models 目录/探测的注入座(同 profile_loader 的解耦纹理:admin-api 不硬 import
# rtime_models,wiring 注入;None => 对应端点 501)。catalog 返回解析后的
# model-registry.json(设计上无密钥);probe 接 (provider_id, timeout, check_url)。
ModelsCatalog = Callable[[], dict[str, Any]]
ModelsProbe = Callable[..., list[dict[str, Any]]]
# K5 模块总览的注入座:返回 manifest_report(deploy/modules.json 的 doctor 报告+
# installed 状态)。wiring 从 RTIME_MODULES_MANIFEST 接;None => 501。
ModulesReport = Callable[[], dict[str, Any]]


# ------------------------------------------------------------------ etag helpers
def compute_etag(store: ConfigStore) -> str:
    """Strong ETag over the PERSISTED layer (config+secrets, unredacted).

    HMAC-SHA256 keyed with the store's ``secret_salt`` (persisted in the store
    dir, so the tag is stable across restarts) over canonical JSON — sorted
    keys, tight separators — of ``store.persisted_flat()``. Hashing the
    persisted layer rather than the env-merged ``get_all`` is what makes the
    tag move on EVERY write, including writes to env-pinned fields whose
    resolved value would not change (defect #6). Server-side only; the keyed
    digest never lets the tag stand in for the values it covers.
    """
    persisted = store.persisted_flat()
    canonical = json.dumps(
        persisted, sort_keys=True, separators=(",", ":"), ensure_ascii=False
    )
    return hmac.new(
        store.secret_salt.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256
    ).hexdigest()


def _scope_redact_diff(
    store: ConfigStore, diff: dict[str, Any], *, reveal: bool
) -> dict[str, Any]:
    """Make a core diff safe to return to the caller's scope.

    The core's redacted diff hashes secret values with the store salt and DROPS
    a path whose value is unchanged — together an equality oracle: a caller
    submitting the true secret gets the path dropped (= "you guessed it"), and
    a caller submitting a wrong value gets ``hmac(salt, guess)`` back (defect
    #1). For a caller WITHOUT ``read:sensitive`` we therefore recompute every
    secret path in the SUBMITTED change set to a constant ``{***, ***}`` and
    keep it present regardless of value — so nothing about the secret's value
    (including whether the guess matched) is learnable. With ``read:sensitive``
    the caller gets the core's (hashed) diff unchanged.
    """
    if reveal:
        return diff
    out = dict(diff)
    for path in diff:
        if _safe_is_secret(store, path):
            out[path] = {
                "before": REDACTED_PLACEHOLDER,
                "after": REDACTED_PLACEHOLDER,
            }
    return out


def _secret_paths_in(store: ConfigStore, changes: dict[str, Any]) -> set[str]:
    return {p for p in changes if _safe_is_secret(store, p)}


def _safe_is_secret(store: ConfigStore, path: str) -> bool:
    """``is_secret`` that fails CLOSED: an unknown/malformed path is treated as
    secret (so a mistyped secret-field name can never leak by falling through)."""
    try:
        return is_secret(store.registry, path)
    except Exception:
        return True


def _quote(etag: str) -> str:
    return f'"{etag}"'


def _required_if_match(request: Request) -> list[str]:
    """Parse ``If-Match`` into a list of candidate (unquoted) tags.

    428 when absent. ``*`` (concurrency opt-out) and weak (``W/``) tags are
    rejected 400. RFC 7232 allows a comma-separated LIST of tags and the
    precondition passes if ANY listed tag matches the current one — so a
    single-element list here is the common case, but a client may send several
    (defect #8: a multi-tag list previously always 412'd because the whole
    string was compared as one opaque tag). Returns the unquoted candidates.
    """
    raw = request.headers.get("If-Match")
    if raw is None or not raw.strip():
        raise ApiError(
            428,
            "precondition_required",
            "mutations require If-Match with the current config ETag "
            "(GET /v1/config returns it)",
        )
    candidates: list[str] = []
    for part in raw.split(","):
        value = part.strip()
        if not value:
            continue
        if value == "*":
            raise ApiError(
                400,
                "wildcard_if_match_rejected",
                "If-Match: * (concurrency opt-out) is not supported here; "
                "send the actual ETag",
            )
        if value.startswith("W/"):
            raise ApiError(
                400,
                "weak_etag_rejected",
                "weak ETags are not accepted; send the strong ETag",
            )
        if len(value) >= 2 and value[0] == '"' and value[-1] == '"':
            value = value[1:-1]
        candidates.append(value)
    if not candidates:
        raise ApiError(
            428,
            "precondition_required",
            "mutations require a non-empty If-Match with the current config ETag",
        )
    return candidates


# ------------------------------------------------------------------- time / ids
def _now_iso() -> str:
    """UTC ISO-8601 — L2 is the single clock source (the core never calls now())."""
    return datetime.now(timezone.utc).isoformat()


def _new_snapshot_id() -> str:
    """uuid4 hex — L2 is the single id source."""
    return uuid.uuid4().hex


# ----------------------------------------------------------------------- factory
def create_app(
    store: ConfigStore,
    *,
    api_keys: list[ApiKey],
    audit_reader: AuditReader,
    version: str,
    lock_dir: str | Path | None = None,
    profile_loader: ProfileLoader | None = None,
    models_catalog: ModelsCatalog | None = None,
    models_probe: ModelsProbe | None = None,
    modules_report: ModulesReport | None = None,
) -> FastAPI:
    """Build the app around ONE ConfigStore (one deployment instance).

    ``audit_reader`` returns the full ordered audit log as dicts (e.g.
    ``JsonlAuditSink.read_all``); ``/v1/audit`` serves its tail. Entries are
    already redacted by the core before they ever reach a sink.

    ``lock_dir`` is where the cross-process mutation flock file lives (the
    store dir in production; omitted for pure in-memory tests, where the
    in-process lock alone is sufficient and no directory exists).

    ``profile_loader`` (T8, design §6.2) maps a profile id to its compiled flat
    ``{module.field: value}`` layer; when given, ``POST /v1/profiles/{id}:reload``
    is enabled — it loads the profile through this callable and hot-swaps the
    store's profile layer atomically (validate-then-swap via
    ``ConfigStore.reload_profile``). ``None`` (the default) leaves the endpoint
    returning 501 (feature not wired) — so admin-api never hard-depends on the
    rtime-config loader; ``wiring`` injects it in production.
    """
    if not api_keys:
        raise ValueError("refusing to build an admin API with no API keys configured")

    def _auth(request: Request) -> ApiKey | None:
        # The static operator-panel SHELL is public (a browser must load the
        # HTML/JS before a token can be pasted; see panel.py). Those exact paths
        # — and ONLY those — skip auth here; every /v1/* path still authenticates.
        # The shell is inert and carries no config; the whole service is
        # 127.0.0.1-only regardless.
        if is_public_panel_path(request.url.path):
            return None
        # J4: pass current time so expired tokens are rejected at auth time.
        return authenticate(
            request.headers.get("Authorization"), api_keys, now_iso=_now_iso()
        )

    # docs disabled: even field names are info an unauthenticated caller should
    # not get (defect #4). GET /v1/schema serves schema behind auth.
    # _auth as an APP-LEVEL dependency runs BEFORE request-body parsing, so an
    # unauthenticated caller sending a malformed/huge body gets 401, not 422
    # (defect #3). Endpoints still declare _reader/_writer for scope; FastAPI
    # caches Depends(_auth) per request so it executes exactly once.
    app = FastAPI(
        title="rtime-admin-api",
        version=version,
        openapi_url=None,
        docs_url=None,
        redoc_url=None,
        dependencies=[Depends(_auth)],
    )
    pending_restart: set[str] = set()
    mutation_lock = threading.Lock()
    file_lock = FileLock(lock_dir) if lock_dir is not None else None
    # test/introspection handle; the closure set is the single source of truth
    app.state.pending_restart = pending_restart

    @contextmanager
    def _mutation_guard():
        """Serialise a read-modify-write across BOTH threads (in-process lock)
        and processes (advisory flock on the store dir), so HTTP + the future
        L3 CLI cannot interleave a check-ETag/apply (defect #9)."""
        with mutation_lock:
            if file_lock is not None:
                with file_lock:
                    yield
            else:
                yield

    # ------------------------------------------------------------ error handlers
    @app.exception_handler(ApiError)
    async def _on_api_error(request: Request, exc: ApiError) -> JSONResponse:
        headers = {"WWW-Authenticate": "Bearer"} if exc.www_authenticate else None
        return JSONResponse(status_code=exc.status, content=exc.body(), headers=headers)

    @app.exception_handler(RequestValidationError)
    async def _on_request_validation(
        request: Request, exc: RequestValidationError
    ) -> JSONResponse:
        # NEVER echo pydantic's `input`/`ctx`: a malformed body can carry a
        # plaintext secret and reflecting it back would bypass read:sensitive.
        errors = [
            {
                "loc": [str(part) for part in err.get("loc", ())],
                "message": err.get("msg", "invalid"),
                "type": err.get("type"),
            }
            for err in exc.errors()
        ]
        body = {
            "error": {
                "code": "invalid_request",
                "message": "request failed schema validation",
                "errors": errors,
            }
        }
        return JSONResponse(status_code=422, content=body)

    @app.exception_handler(StarletteHTTPException)
    async def _on_http_exception(
        request: Request, exc: StarletteHTTPException
    ) -> JSONResponse:
        # keep the {"error": ...} envelope for framework-raised 404/405 etc.
        body = {"error": {"code": "http_error", "message": str(exc.detail)}}
        return JSONResponse(
            status_code=exc.status_code, content=body, headers=exc.headers
        )

    @app.exception_handler(Exception)
    async def _on_unhandled(request: Request, exc: Exception) -> JSONResponse:
        # Catch-all so an unexpected bug NEVER breaks the {"error": ...} contract
        # or leaks a traceback/str(exc) (which could carry a secret) to a client
        # (defect #12). Detail is generic; the real exception is left to server
        # logs. ApiError/RequestValidationError/HTTPException have their own
        # handlers above and never reach here.
        body = {
            "error": {
                "code": "internal_error",
                "message": "internal server error",
            }
        }
        return JSONResponse(status_code=500, content=body)

    # ------------------------------------------------------------------ auth deps
    # _auth is defined above (used as the app-level dependency); reuse it here.
    def _reader(key: ApiKey = Depends(_auth)) -> ApiKey:
        require_scope(key, SCOPE_READ)
        return key

    def _writer(key: ApiKey = Depends(_auth)) -> ApiKey:
        require_scope(key, SCOPE_WRITE)
        return key

    # -------------------------------------------------------------- path helpers
    def _meta_or_http(path: str):
        """field_meta with API error mapping: 400 malformed / 404 unknown."""
        try:
            return field_meta(store.registry, path)
        except ValueError as exc:
            raise ApiError(400, "invalid_path", str(exc)) from exc
        except KeyError as exc:
            message = exc.args[0] if exc.args else str(exc)
            raise ApiError(404, "unknown_path", str(message)) from exc

    def _check_etag(candidates: list[str], current: str) -> None:
        """412 unless SOME candidate tag equals ``current`` (the fresh ETag).

        Byte-wise constant-time compare over every candidate (``compare_digest``
        on str raises ``TypeError`` for non-ASCII client input; encoding first
        makes any client-supplied garbage a clean 412 instead of a 500). All
        candidates are scanned (no early break) so timing does not reveal which
        matched. ``current`` is passed in so the caller computes the ETag ONCE
        under the lock and reuses it for both the check and the response (no
        second read that could tear, defect #7).
        """
        current_bytes = current.encode("utf-8")
        matched = False
        for cand in candidates:
            if hmac.compare_digest(cand.encode("utf-8"), current_bytes):
                matched = True
        if not matched:
            raise ApiError(
                412,
                "etag_mismatch",
                "If-Match does not match the current config "
                "(re-GET /v1/config for the fresh ETag)",
            )

    def _redact_field_errors(
        errors: list[dict[str, Any]], *, reveal: bool = False
    ) -> list[dict[str, Any]]:
        """Scrub EVERY error face for a secret field, not just ``input``.

        The core already masks a secret field's ``input``, but a pydantic
        ``message``/``ctx`` (and any leftover ``ctx`` key) can still echo the
        rejected value (defect #5, same class as L1 #6/#7). For a secret path
        (fail-closed on unknown) we blank the message to a fixed string and drop
        ``input``/``ctx`` entirely, unless the caller holds ``read:sensitive``.
        """
        if reveal:
            return errors
        out: list[dict[str, Any]] = []
        for err in errors:
            path = err.get("path", "")
            if isinstance(path, str) and path and _safe_is_secret(store, path):
                scrubbed = dict(err)
                scrubbed["message"] = "invalid value for secret field (redacted)"
                if "input" in scrubbed:
                    scrubbed["input"] = REDACTED_PLACEHOLDER
                scrubbed.pop("ctx", None)
                out.append(scrubbed)
            else:
                out.append(err)
        return out

    def _apply_response(
        response: Response,
        result: ApplyResult,
        ts: str,
        *,
        new_etag: str,
        reveal: bool,
    ) -> dict[str, Any]:
        pending_restart.update(result.restart_required)
        response.headers["ETag"] = _quote(new_etag)
        return {
            "ok": True,
            "ts": ts,
            "snapshot_id": result.snapshot_id,
            "changed": result.changed,
            "hot": result.hot,
            "restart_required": result.restart_required,
            # core redacts secrets to a salted hmac; downgrade to constant ***
            # for non-sensitive callers so an apply response is not an oracle
            # either (defect #1 class extends to the apply diff).
            "diff": _scope_redact_diff(store, result.diff, reveal=reveal),
            "etag": new_etag,
        }

    # --------------------------------------------------------------------- reads
    @app.get("/v1/health")
    def health(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        return {
            "ok": True,
            "version": version,
            "needs_restart": sorted(pending_restart),
        }

    @app.get("/v1/schema")
    def get_schema(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        registry = store.registry
        return {"modules": {m: registry.get_schema(m) for m in registry.list_modules()}}

    @app.get("/v1/config")
    def get_config(
        response: Response,
        reveal: int = Query(0, ge=0, le=1),
        provenance: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        if reveal:
            require_scope(key, SCOPE_READ_SENSITIVE)
        # Read values and ETag under the SAME mutation guard so no concurrent
        # write can slip between them: a client that GETs {values, etag} and
        # then PATCHes with that etag must be guaranteed the etag describes the
        # exact state it saw (defect #7 — the two reads were previously
        # unsynchronised and could return a torn (values, etag) pair).
        #
        # ``?provenance=1`` makes each value ``{"value": v, "provenance": layer}``
        # (layer = env|store|profile|default) so the panel can badge every key
        # with its winning layer (design §6.3). Redaction still applies to the
        # inner ``value`` — provenance is a plain read, not a reveal, so it stays
        # under the ``read`` scope; a secret still shows ``***`` without
        # ``read:sensitive``.
        with _mutation_guard():
            values = store.get_all(redact=not reveal, provenance=bool(provenance))
            etag = compute_etag(store)
        response.headers["ETag"] = _quote(etag)
        return {"values": values, "etag": etag}

    @app.get("/v1/config/drift")
    def get_drift(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        """J1 drift:store 覆盖被 profile 层遮蔽(值不同)的 path 清单
        ``[{path, store, profile, secret}]``(secret 值恒 ***)。面板据此把这些字段标
        "由 profile 管理"、给 unset(DELETE)按钮把 override 交还上层——消灭"改了 UI 不
        生效"的头号困惑(GitLab env-override banner / Grafana provisioned 提示的对味做法)。
        注册在 /v1/config/{path} 之前,否则会被 path 捕获。config-and-access §2.2。"""
        return {"drift": store.drift_report()}

    @app.get("/v1/config/{path}")
    def get_config_value(
        path: str,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        _meta_or_http(path)
        if reveal:
            require_scope(key, SCOPE_READ_SENSITIVE)
        value = store.get(path)
        if not reveal:
            value = redact_all(store.registry, {path: value})[path]
        return {"path": path, "value": value}

    @app.get("/v1/history")
    def get_history(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        return {"snapshots": store.list_history()}

    @app.get("/v1/audit")
    def get_audit(
        limit: int = Query(50, ge=1, le=1000),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        entries = audit_reader()
        return {"entries": entries[-limit:]}

    def _reveal_ok(key: ApiKey, reveal: int) -> bool:
        """Resolve a ``?reveal=1`` query into a bool, enforcing the scope."""
        if reveal:
            require_scope(key, SCOPE_READ_SENSITIVE)
            return True
        return False

    # ------------------------------------------------------------------ dry runs
    @app.post("/v1/config/validate")
    def validate_config(
        body: ChangesBody,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        """Dry-run / preview-impact. ALWAYS 200 with ``{ok, errors, diff, hot,
        restart_required}`` — including unknown paths (reported as error entries,
        not 404), so one call vets a whole change set. Never writes, never audits.

        J1: beyond validation it PREVIEWS the impact of the change set without
        applying — the redacted before→after ``diff`` (same redaction discipline
        as /v1/config/diff) plus the change paths partitioned into ``hot`` (would
        take effect live) vs ``restart_required`` (would need a process restart),
        derived from x-reload. This is the "预览影响" the panel/agent shows before
        committing (Netdata TEST-command semantics).

        Secret-field errors are scrubbed on every face (message/input/ctx)
        unless the caller holds ``read:sensitive`` and passes ``?reveal=1``
        (defect #5 class)."""
        reveal_ok = _reveal_ok(key, reveal)
        errors: list[dict[str, Any]] = []
        known: dict[str, Any] = {}
        for path, value in body.changes.items():
            try:
                field_meta(store.registry, path)
            except (KeyError, ValueError) as exc:
                message = exc.args[0] if exc.args else str(exc)
                errors.append(
                    {
                        "path": path,
                        "message": str(message),
                        "type": "unknown_path",
                        "input": None,  # never echo: the path may be a mistyped secret field
                    }
                )
                continue
            known[path] = value
        if known:
            errors.extend(e.to_dict() for e in store.validate(known))
        # Impact preview over the KNOWN paths only (unknown paths error out above).
        # Redacted diff mirrors /v1/config/diff exactly (scope + secret constant ***).
        raw_diff = store.diff(known, redact=True) if known else {}
        if not reveal_ok:
            for path in _secret_paths_in(store, known):
                raw_diff[path] = {
                    "before": REDACTED_PLACEHOLDER,
                    "after": REDACTED_PLACEHOLDER,
                }
        hot, restart_required = store.classify_reload(known)
        return {
            "ok": not errors,
            "errors": _redact_field_errors(errors, reveal=reveal_ok),
            "diff": _scope_redact_diff(store, raw_diff, reveal=reveal_ok),
            "hot": hot,
            "restart_required": restart_required,
        }

    @app.post("/v1/config/diff")
    def diff_config(
        body: ChangesBody,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        """Redacted before/after. For a caller WITHOUT ``read:sensitive`` every
        secret path in the change set comes back as a constant ``{***, ***}``
        and is never dropped on a value match — so the endpoint reveals nothing
        about a secret's value, closing the equality oracle (defect #1)."""
        reveal_ok = _reveal_ok(key, reveal)
        for path in body.changes:
            _meta_or_http(path)
        raw = store.diff(body.changes, redact=True)
        # the core drops secret paths whose submitted value equals the stored
        # one; re-add them as constant *** so presence is value-independent.
        if not reveal_ok:
            for path in _secret_paths_in(store, body.changes):
                raw[path] = {
                    "before": REDACTED_PLACEHOLDER,
                    "after": REDACTED_PLACEHOLDER,
                }
        return {"diff": _scope_redact_diff(store, raw, reveal=reveal_ok)}

    def _require_field_scopes(key: ApiKey, paths) -> None:
        """Require the ``x-scope`` union over ``paths`` — EVERY write verb runs
        this, so no write (PATCH or rollback) can bypass field-level scope
        (defect #2). A field with no ``x-scope`` is open to plain ``write``
        (already checked by the ``_writer`` dependency)."""
        needed: set[str] = set()
        for path in paths:
            meta = _meta_or_http(path)
            if meta.scope:
                needed.add(meta.scope)
        for scope in sorted(needed):
            require_scope(key, scope)

    # ----------------------------------------------------------------- mutations
    @app.patch("/v1/config")
    def patch_config(
        body: PatchBody,
        request: Request,
        response: Response,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_writer),
    ) -> dict[str, Any]:
        if not body.changes:
            raise ApiError(400, "empty_changes", "changes must be a non-empty object")
        reveal_ok = _reveal_ok(key, reveal)
        _require_field_scopes(key, body.changes)
        candidates = _required_if_match(request)
        with _mutation_guard():
            current = compute_etag(store)
            _check_etag(candidates, current)
            ts = _now_iso()
            try:
                result = store.apply(
                    body.changes,
                    ts=ts,
                    snapshot_id=_new_snapshot_id(),
                    actor=key.name,
                    source="http",
                    note=body.note,
                )
            except AdminValidationError as exc:
                raise ApiError(
                    422,
                    "validation_failed",
                    "config validation failed",
                    errors=_redact_field_errors(
                        [e.to_dict() for e in exc.errors], reveal=reveal_ok
                    ),
                ) from exc
            new_etag = compute_etag(store)
            return _apply_response(
                response, result, ts, new_etag=new_etag, reveal=reveal_ok
            )

    @app.post("/v1/rollback")
    def rollback_config(
        body: RollbackBody,
        request: Request,
        response: Response,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_writer),
    ) -> dict[str, Any]:
        """Restore a snapshot (same If-Match rules as PATCH).

        Field-level x-scope IS enforced (defect #2): the paths a rollback would
        change are computed up front (``store.rollback_changed_paths``) and the
        caller must hold each path's ``x-scope`` — a write verb must never let a
        caller change a scoped field it could not change via PATCH. Gated by
        ``write`` + ETag and fully audited (actor = key name).
        """
        reveal_ok = _reveal_ok(key, reveal)
        candidates = _required_if_match(request)
        with _mutation_guard():
            current = compute_etag(store)
            _check_etag(candidates, current)
            try:
                changed = store.rollback_changed_paths(body.snapshot_id)
            except SnapshotNotFoundError as exc:
                message = exc.args[0] if exc.args else str(exc)
                raise ApiError(404, "unknown_snapshot", str(message)) from exc
            _require_field_scopes(key, changed)
            ts = _now_iso()
            result = store.rollback(
                body.snapshot_id,
                ts=ts,
                new_snapshot_id=_new_snapshot_id(),
                actor=key.name,
                source="http",
            )
            new_etag = compute_etag(store)
            return _apply_response(
                response, result, ts, new_etag=new_etag, reveal=reveal_ok
            )

    @app.delete("/v1/config/{path}")
    def unset_config_value(
        path: str,
        request: Request,
        response: Response,
        reveal: int = Query(0, ge=0, le=1),
        confirm: str | None = Query(None),
        key: ApiKey = Depends(_writer),
    ) -> dict[str, Any]:
        """J1 unset:清掉 ``path`` 的 store override,值落回下层(profile,否则 schema
        默认)——K8s SSA 的 ownership 交还,不是"文件赢/UI 赢"的全局开关。面板 drift 列表
        的配套写动词。与 PATCH/rollback 同样的写门:write scope + 字段级 x-scope + If-Match
        + mutation guard,全审计(action=unset)。清一个本就没 override 的 path=幂等 no-op
        成功(空 diff)。config-and-access §2.2。

        J5 两段式(不可逆保护):unset 一个 **secret** 字段会丢失其明文值(不可逆)。所以对
        secret 字段要求两段式确认——不带有效 ``?confirm=<token>`` 时返回 409
        ``{needs_confirm, confirm_token, warning}``(plan 阶段,不删);带正确 token 才执行
        (apply 阶段)。token 绑 path+当前 ETag,secret 或状态变了即失效。非 secret 字段
        (值落回下层、可再 set)不受影响。config-and-access §3.3。"""
        reveal_ok = _reveal_ok(key, reveal)
        _meta_or_http(path)
        _require_field_scopes(key, [path])
        candidates = _required_if_match(request)
        with _mutation_guard():
            current = compute_etag(store)
            _check_etag(candidates, current)
            # J5: secret unset 需两段式确认(丢明文值不可逆)。token 绑 path+当前 ETag。
            if _safe_is_secret(store, path):
                if not verify_two_phase("unset_secret", {"path": path}, current, confirm):
                    response.status_code = 409
                    return {
                        "needs_confirm": True,
                        "confirm_token": two_phase_token("unset_secret", {"path": path}, current),
                        "warning": f"unset 会丢失 secret 字段 {path!r} 的明文值(不可逆);"
                        "带 ?confirm=<confirm_token> 再次调用以确认执行。",
                    }
            ts = _now_iso()
            try:
                result = store.unset(
                    path,
                    ts=ts,
                    snapshot_id=_new_snapshot_id(),
                    actor=key.name,
                    source="http",
                )
            except AdminValidationError as exc:
                raise ApiError(
                    422,
                    "validation_failed",
                    "unset would leave an invalid state",
                    errors=_redact_field_errors(
                        [e.to_dict() for e in exc.errors], reveal=reveal_ok
                    ),
                ) from exc
            new_etag = compute_etag(store)
            return _apply_response(
                response, result, ts, new_etag=new_etag, reveal=reveal_ok
            )

    # ----------------------------------------------------------- profile reload
    @app.post("/v1/profiles/{profile_id}:reload")
    def reload_profile(
        profile_id: str,
        response: Response,
        reveal: int = Query(0, ge=0, le=1),
        key: ApiKey = Depends(_writer),
    ) -> dict[str, Any]:
        """Atomic validate-then-swap of the git profile ``profile_id`` (design §6.2/§2.10).

        Loads the profile through the injected ``profile_loader`` (rtime-config's
        loader — parse + single-level extends + file-ref resolution + x-secret door +
        projection), then calls :meth:`ConfigStore.reload_profile` (Caddy ``/load``
        semantics: the WHOLE new layer is validated against the live store/env view
        and, on ANY failure, the OLD layer stays active — never a partial swap). One
        audit entry (``action=profile_reload``, actor = key name) is emitted by the
        core. The response is the :class:`ApplyResult` partitioned into ``hot`` (took
        effect immediately — a running bridge re-reads on its next session build) and
        ``restart_required`` (the operator/panel must restart to apply — reload does
        NOT hot-apply read_only / library.scope / channels / mcp_servers; those are
        surfaced here and added to the pending-restart set on ``GET /v1/health``).

        Requires ``write`` scope (git is the profile's only writer; this endpoint just
        tells the running store to re-read what git produced — it is not a per-field
        config write, so it takes the plain write gate, not field ``x-scope``). No
        ``If-Match``: a reload is not a read-modify-write of the store's persisted
        layer (it swaps the read-only profile layer), so it does not race PATCH's ETag;
        it still runs under the mutation guard so a concurrent PATCH cannot interleave.

        501 when no ``profile_loader`` was wired (feature not enabled in this
        deployment); 404 / 422 when the profile cannot be loaded / fails validation.
        """
        if profile_loader is None:
            raise ApiError(
                501,
                "profile_reload_unavailable",
                "profile reload is not enabled on this deployment "
                "(no profile loader configured)",
            )
        reveal_ok = _reveal_ok(key, reveal)
        # Load OUTSIDE the mutation guard (parse/IO can be slow); the swap itself is
        # what must be serialised. A load failure (missing dir, bad YAML, inlined
        # secret, projection error) is a 4xx, not a 500 — never leak a traceback.
        try:
            new_layer = profile_loader(profile_id)
        except FileNotFoundError as exc:
            raise ApiError(
                404, "unknown_profile", f"profile {profile_id!r} not found"
            ) from exc
        except Exception as exc:  # noqa: BLE001 — loader raises rtime-config errors
            # ProfileError / ProfileSecretError / any loader failure -> 422 with a
            # safe message (the loader's message names paths/keys, not secret values).
            raise ApiError(
                422,
                "profile_load_failed",
                f"profile {profile_id!r} failed to load: {exc}",
            ) from exc
        with _mutation_guard():
            ts = _now_iso()
            try:
                result = store.reload_profile(
                    new_layer,
                    ts=ts,
                    snapshot_id=_new_snapshot_id(),
                    actor=key.name,
                    source="http",
                    note=f"reload profile {profile_id}",
                )
            except AdminValidationError as exc:
                # validate-then-swap failed: the OLD layer is still active (the core
                # never half-swaps). Surface the field errors (secret faces scrubbed).
                raise ApiError(
                    422,
                    "profile_validation_failed",
                    f"profile {profile_id!r} did not validate; the previous profile "
                    "stays active (no partial swap)",
                    errors=_redact_field_errors(
                        [e.to_dict() for e in exc.errors], reveal=reveal_ok
                    ),
                ) from exc
            new_etag = compute_etag(store)
            body = _apply_response(
                response, result, ts, new_etag=new_etag, reveal=reveal_ok
            )
            body["profile_id"] = profile_id
            return body

    # ------------------------------------------------------------ models (K2)
    @app.get("/v1/models/catalog")
    def get_models_catalog(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        """模型目录(面板"选默认/看provider"的数据源;K2)。

        返回解析后的 model-registry.json(设计上不含任何密钥——provider 只列
        ``secret_env_names`` 名字)+ 当前生效的路由默认。**选默认不在这里写**:
        面板选好后走 ``PATCH /v1/config {"models.default_model": ...}``——与所有
        配置同一条写路径(ETag/x-scope/审计/两段式一致),不开第二写口。
        registry 文件本身的增删 provider 走 ``python -m rtime_models add-provider/
        remove-provider``(CLI 校验+原子写;面板层 K5 再包装)。501=本部署未接
        rtime_models(与 profile reload 同纹理)。
        """
        if models_catalog is None:
            raise ApiError(
                501,
                "models_catalog_unavailable",
                "models catalog is not enabled on this deployment "
                "(rtime_models not wired)",
            )
        try:
            effective_default = store.get("models.default_model")
        except Exception:  # models 模块未注册进本 store 的 registry
            effective_default = None
        return {
            "registry": models_catalog(),
            "effective_default_model": effective_default,
            "set_default_via": 'PATCH /v1/config {"models.default_model": "<id-or-alias>"}',
        }

    @app.get("/v1/models/probe")
    def get_models_probe(
        provider: str | None = Query(None),
        timeout: float = Query(3.0, gt=0, le=10),
        check_url: int = Query(1, ge=0, le=1),
        key: ApiKey = Depends(_reader),
    ) -> dict[str, Any]:
        """provider 就绪探测(K2):密钥 env 设了吗、endpoint 活着吗。

        只报"是否设置"(布尔+env 名),从不读密钥值、从不把密钥发给 provider——
        探测发的是裸 GET,任何 HTTP 状态(401/404 也算)都证明 endpoint 活着。
        URL 全部来自 registry 数据文件而非请求参数,无 SSRF 面。read scope 即可
        (面板亮"就绪灯"用)。``?check_url=0`` 跳过网络只查密钥。"""
        if models_probe is None:
            raise ApiError(
                501,
                "models_probe_unavailable",
                "models probe is not enabled on this deployment "
                "(rtime_models not wired)",
            )
        results = models_probe(
            provider_id=provider, timeout=timeout, check_url=bool(check_url)
        )
        if provider is not None and not results:
            raise ApiError(404, "unknown_provider", f"no provider with id {provider!r}")
        return {"results": results}

    # ------------------------------------------------------------ modules (K5)
    @app.get("/v1/modules")
    def get_modules(key: ApiKey = Depends(_reader)) -> dict[str, Any]:
        """模块总览(K5):deploy/modules.json 的 doctor 报告——22 个模块、每个装没装
        (按 COMPOSE_PROFILES)、hot_pluggable、config_module(面板据此跳细配表单)、
        docs、校验 issues。read scope 即可(纯清单,无密钥面)。501=本部署未接
        manifest(wiring 的 RTIME_MODULES_MANIFEST 未设)。"""
        if modules_report is None:
            raise ApiError(
                501,
                "modules_report_unavailable",
                "module manifest is not enabled on this deployment "
                "(RTIME_MODULES_MANIFEST not set)",
            )
        return modules_report()

    # Static operator panel (T7): registered LAST so a panel route can never
    # shadow a /v1 route. Its shell paths are auth-exempt (see _auth above);
    # every /v1/* endpoint stays gated.
    register_panel(app)

    return app
