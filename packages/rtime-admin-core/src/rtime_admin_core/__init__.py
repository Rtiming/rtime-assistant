# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""rtime-admin-core — P3 L1 management core (pure Python, no network).

The single source of truth for reading, writing, validating, and auditing one
deployment instance's configuration. Every upper layer of the P3 management face
— L2 HTTP API, L2' MCP adapter, L3 CLI, L4 human panel — is a thin client of the
objects here, so their behaviour cannot drift apart (API-first, panel = just
another client; docs/development-plan.zh-CN.md §五).

What this package is
--------------------
  - :class:`Registry` — module name -> pydantic-settings model (built on the P2
    ``rtime-config`` L0 foundation). ``list_modules`` / ``get_schema`` feed the
    admin API and auto-generated forms. :func:`default_registry` ships 3 sample
    modules (models / library-gateway / channel-common).
  - :class:`ConfigStore` — path-addressed get/set, ``get_all(redact=)``,
    ``validate`` (dry-run), ``diff``, transactional ``apply`` (validate ->
    snapshot -> write -> classify hot/restart -> audit), ``snapshot`` /
    ``list_history`` / ``rollback``. Backed by a :class:`ConfigBackend`
    (:class:`FileBackend` = config file + separate 0600 secrets file) and a
    :class:`HistoryStore`.
  - Append-only audit (:class:`AuditEntry`, :class:`JsonlAuditSink`) with an
    injected timestamp and hashed-placeholder secret diffs.

What this package is NOT (later layers): HTTP, MCP, CLI, panel, token/scope auth.
It also does not migrate the live apps' config (that is P2 stage ①).

Determinism rule: transaction functions never call ``time.now()`` or generate
random ids — the caller injects ``ts`` and ``snapshot_id``.
"""

from __future__ import annotations

from .audit import (
    OUTCOME_ERROR,
    OUTCOME_OK,
    AuditEntry,
    AuditHook,
    InMemoryAuditSink,
    JsonlAuditSink,
)
from .backends import ConfigBackend, FileBackend, MemoryBackend
from .diff import compute_diff, hash_secret, redact_all, redact_diff
from .errors import (
    AdminCoreError,
    FieldError,
    SnapshotNotFoundError,
    UnknownPathError,
    ValidationError,
)
from .history import (
    FileHistory,
    HistoryStore,
    InMemoryHistory,
    Snapshot,
)
from .metadata import (
    REDACTED_PLACEHOLDER,
    FieldMeta,
    all_paths,
    field_meta,
    is_secret,
    secret_paths,
    split_path,
)
from .registry import (
    Registry,
    default_registry,
    register_assistant_gateway_module,
    register_chat_runtime_module,
    register_feishu_module,
    register_qq_module,
    register_qq_selfheal_module,
    register_ustc_kb_module,
    register_web_chat_module,
)
from .store import (
    PROV_DEFAULT,
    PROV_ENV,
    PROV_PROFILE,
    PROV_STORE,
    ApplyResult,
    ConfigStore,
)
from .rbac import (
    Capability,
    Principal,
    RbacError,
    Role,
    can,
    grant_extra,
    require,
    role_capabilities,
)
from .validation import validate_module, validate_state

__all__ = [
    # rbac (J3: two-layer platform-super / project-role authorization)
    "Role",
    "Capability",
    "Principal",
    "RbacError",
    "can",
    "require",
    "grant_extra",
    "role_capabilities",
    # registry + schemas
    "Registry",
    "default_registry",
    "register_assistant_gateway_module",
    "register_chat_runtime_module",
    "register_feishu_module",
    "register_qq_module",
    "register_qq_selfheal_module",
    "register_ustc_kb_module",
    "register_web_chat_module",
    # store
    "ConfigStore",
    "ApplyResult",
    "PROV_DEFAULT",
    "PROV_PROFILE",
    "PROV_STORE",
    "PROV_ENV",
    # backends
    "ConfigBackend",
    "FileBackend",
    "MemoryBackend",
    # history
    "HistoryStore",
    "InMemoryHistory",
    "FileHistory",
    "Snapshot",
    # audit
    "AuditEntry",
    "AuditHook",
    "InMemoryAuditSink",
    "JsonlAuditSink",
    "OUTCOME_OK",
    "OUTCOME_ERROR",
    # metadata
    "FieldMeta",
    "field_meta",
    "is_secret",
    "secret_paths",
    "all_paths",
    "split_path",
    "REDACTED_PLACEHOLDER",
    # diff
    "compute_diff",
    "redact_diff",
    "redact_all",
    "hash_secret",
    # validation
    "validate_module",
    "validate_state",
    # errors
    "AdminCoreError",
    "UnknownPathError",
    "ValidationError",
    "SnapshotNotFoundError",
    "FieldError",
]
