# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""ConfigStore — the L1 admin core.

One object that reads/writes/validates a single deployment's config, backed by a
:class:`ConfigBackend` (files by default) and guarded by the schema
:class:`Registry`. Every upper layer (L2 HTTP, L2' MCP, L3 CLI, L4 panel) calls
ONLY this, so their behaviour cannot drift (docs/development-plan §五 L1).

Config model
------------
State is a nested ``{module: {field: value}}`` mapping split across two stores:
non-secret config and secrets (kept apart on disk). Read precedence, resolved at
read time (nothing is seeded), is FOUR layers low->high:

    schema default < profile < stored config/secret < env override

The profile layer is an optional, INJECTED, read-only ``{path: value}`` map: the
git-declared profile compiled/projected to flat ``module.field`` keys (see
``rtime_config.profile``). The store never writes it — its only writer is git,
swapped in atomically via :meth:`ConfigStore.reload_profile`. The env overlay is
likewise read-only (the store never writes env back). Omitting the profile layer
(the default) collapses to the original three-layer behaviour, so every existing
caller is unaffected.

Provenance / drift / clear-override
-----------------------------------
:meth:`provenance` answers which layer a path's effective value came from
(``env`` / ``store`` / ``profile`` / ``default``) — K8s server-side-apply's
"declared, not discovered" ownership. :meth:`unset` is the clear-override verb:
delete a store key so its value falls back to the profile layer (SSA ownership
hand-off). :meth:`drift_report` lists shadowed keys (store override differs from
the current profile value). :meth:`reload_profile` is an atomic validate-then-swap
of the whole compiled layer (Caddy ``/load`` semantics): validate the new layer
merged with the live store/env view, keep the old layer on failure, and emit ONE
audit entry classifying changed paths hot vs restart-required.

Addressing
----------
Values are addressed by dotted path ``module.field`` (Caddy style). ``get`` /
``set`` / ``diff`` speak paths; ``get_all`` returns the flat ``{path: value}`` map.

Purity / testability
--------------------
The store NEVER calls ``time.now()`` or generates random *ids* internally.
``apply`` and ``rollback`` take a ``ts`` and ``snapshot_id`` from the caller (the
L2 layer's clock/uuid). This makes transactions deterministic and replayable in
tests. The single exception is the per-store audit-diff salt (``secret_salt``,
defect #9): it is generated ONCE at construction (or injected — tests pass a fixed
salt), never per transaction, so transactions stay deterministic.

Transactions
------------
``apply(changes, ...)`` = validate the change set against the PERSISTED state of
just the referenced modules (env is a read-time overlay only, never gates writes)
-> snapshot the current persisted state to history (capped) -> write atomically
(compensating on failure) -> emit one audit entry -> return which fields are hot
vs restart-required. ``rollback(id, ...)`` restores a snapshot the same way
(itself snapshotted + audited), diffing the persisted layer.
"""

from __future__ import annotations

import os
import secrets as _secrets
from dataclasses import dataclass, field
from typing import Any

from .audit import OUTCOME_ERROR, OUTCOME_OK, AuditEntry, AuditHook
from .backends import ConfigBackend, json_copy
from .diff import compute_diff, redact_all, redact_diff
from .errors import (
    FieldError,
    SnapshotNotFoundError,
    UnknownPathError,
    ValidationError,
)
from .errors import (
    ValidationError as AdminValidationError,
)
from .history import HistoryStore, snapshot_state
from .metadata import REDACTED_PLACEHOLDER, field_meta, split_path
from .registry import Registry
from .validation import validate_state

# Marks "read this from env / stored value at get-time" vs the schema default.
_NO_ENV = object()

# provenance layer labels (which layer an effective value came from), low->high.
PROV_DEFAULT = "default"
PROV_PROFILE = "profile"
PROV_STORE = "store"
PROV_ENV = "env"


def _generate_salt() -> str:
    """One-off random salt for keyed secret digests (defect #9).

    This is the ONLY random/entropy call in the non-test core, and it is
    generate-once (stored on the ConfigStore instance) — tests inject a fixed
    ``secret_salt`` for determinism, so transactions stay replayable.
    """
    return _secrets.token_hex(16)


@dataclass(frozen=True)
class ApplyResult:
    """Outcome of a successful :meth:`ConfigStore.apply` / :meth:`rollback`.

    ``hot`` / ``restart_required`` partition the changed paths by ``x-reload`` so
    the caller knows what took effect immediately and what needs a process restart.
    ``restart_required`` being non-empty is the signal an operator/agent acts on.
    """

    snapshot_id: str
    changed: list[str] = field(default_factory=list)
    hot: list[str] = field(default_factory=list)
    restart_required: list[str] = field(default_factory=list)
    diff: dict[str, Any] = field(default_factory=dict)  # redacted before/after

    @property
    def needs_restart(self) -> bool:
        return bool(self.restart_required)


class ConfigStore:
    def __init__(
        self,
        registry: Registry,
        backend: ConfigBackend,
        history: HistoryStore,
        *,
        audit_hook: AuditHook | None = None,
        max_history: int = 20,
        env: dict[str, str] | None = None,
        secret_salt: str | None = None,
        profile_layer: dict[str, Any] | None = None,
    ) -> None:
        """``env`` overrides the process env source (defaults to ``os.environ``);
        pass ``{}`` to disable env overlay entirely (useful in tests).

        ``max_history`` is clamped to a minimum of 1: apply/rollback return a
        snapshot_id that MUST be rollback-able immediately, so the just-taken
        snapshot can never be pruned away (defect #4). ``secret_salt`` keys the
        audit-diff secret digests (defect #9); if omitted, one is generated once
        via the injected salt provider and persisted on the store instance — tests
        inject a fixed salt for determinism.

        ``profile_layer`` is the optional injected read-only profile layer (a flat
        ``{module.field: value}`` map compiled from the git profile). It sits below
        the store and above schema defaults in read precedence; the store never
        writes it (git is its only writer; hot-swap via :meth:`reload_profile`).
        ``None`` / omitted == no profile layer (three-layer legacy behaviour).
        """
        self.registry = registry
        self.backend = backend
        self.history = history
        self.audit_hook = audit_hook
        if max_history < 0:
            raise ValueError("max_history must be >= 0")
        # Keep at least the most-recent snapshot so a returned snapshot_id is
        # always immediately rollback-able (defect #4).
        self.max_history = max(1, max_history)
        self._env = env
        self._secret_salt = secret_salt if secret_salt is not None else _generate_salt()
        # Read-only profile layer, keyed by validated module.field paths. Copy so a
        # caller mutating their dict cannot silently change our live layer.
        self._profile_layer: dict[str, Any] = dict(profile_layer or {})

    # ------------------------------------------------------------------ helpers
    @property
    def secret_salt(self) -> str:
        """The per-store salt keying audit-diff secret digests (defect #9)."""
        return self._secret_salt

    def _envmap(self) -> dict[str, str]:
        return os.environ if self._env is None else self._env

    def _stored_value(self, module: str, field: str) -> Any:
        """Stored value for a path (secret store wins its own namespace)."""
        meta = field_meta(self.registry, f"{module}.{field}")
        store = (
            self.backend.load_secrets() if meta.secret else self.backend.load_config()
        )
        return store.get(module, {}).get(field, _NO_ENV)

    def _profile_value(self, module: str, field: str) -> Any:
        """Profile-layer value for a path, or ``_NO_ENV`` if the layer omits it."""
        return self._profile_layer.get(f"{module}.{field}", _NO_ENV)

    def _env_value(self, module: str, field: str) -> Any:
        """env override for a path, honouring x-env-aliases resolution order."""
        env = self._envmap()
        prop = self.registry.get_schema(module)["properties"][field]
        env_prefix = (
            self.registry.model(module).model_config.get("env_prefix", "") or ""
        )
        names = prop.get("x-env-aliases") or [f"{env_prefix}{field}".upper()]
        for name in names:
            # case-insensitive: env keys are conventionally UPPER_SNAKE
            for k, v in env.items():
                if k.upper() != name.upper():
                    continue
                # An empty / whitespace-only env value is treated as UNSET, not as
                # an override of "". This is what makes a git profile survive a
                # deployment: compose injects ``${QQ_X:-}`` empties for keys the
                # docker.env no longer sets, and those empties must NOT shadow the
                # profile layer (they would fall the field back to its schema
                # default). Matches the fail-closed-union philosophy for restriction
                # fields (an empty env contributes nothing). An intentional empty
                # value is expressed by the store/profile layer, not by env.
                if str(v).strip() == "":
                    continue
                return self._coerce_env(prop, v)
        return _NO_ENV

    @staticmethod
    def _coerce_env(prop: dict[str, Any], raw: str) -> Any:
        """Coerce an env string to the field's declared JSON type (best effort).

        Full validation still happens in ``validate``; this only makes ``get`` a
        typed read. Unknown/complex types fall through as the raw string.
        """
        t = prop.get("type")
        if t is None and "anyOf" in prop:  # e.g. int | None
            t = next(
                (s.get("type") for s in prop["anyOf"] if s.get("type") != "null"),
                None,
            )
        if t == "boolean":
            return raw.strip().lower() in {"1", "true", "yes", "on"}
        if t == "integer":
            try:
                return int(raw)
            except ValueError:
                return raw
        if t == "number":
            try:
                return float(raw)
            except ValueError:
                return raw
        return raw

    def _default_value(self, module: str, field: str) -> Any:
        prop = self.registry.get_schema(module)["properties"][field]
        if "default" in prop:
            return prop["default"]
        if "x-default" in prop:
            return prop["x-default"]
        return None

    # ---------------------------------------------------------------------- get
    def get(self, path: str) -> Any:
        """Resolve one path: env > store > profile > schema default (read-time)."""
        module, field = split_path(path)
        self._require_path(path)
        env = self._env_value(module, field)
        if env is not _NO_ENV:
            return env
        stored = self._stored_value(module, field)
        if stored is not _NO_ENV:
            return stored
        prof = self._profile_value(module, field)
        if prof is not _NO_ENV:
            return prof
        return self._default_value(module, field)

    def provenance(self, path: str) -> str:
        """Which layer supplies ``path``'s effective value: env|store|profile|default.

        Mirrors :meth:`get`'s precedence exactly so a panel/API can badge every key
        with its winning layer (K8s SSA: ownership is declared, not guessed).
        """
        module, field = split_path(path)
        self._require_path(path)
        if self._env_value(module, field) is not _NO_ENV:
            return PROV_ENV
        if self._stored_value(module, field) is not _NO_ENV:
            return PROV_STORE
        if self._profile_value(module, field) is not _NO_ENV:
            return PROV_PROFILE
        return PROV_DEFAULT

    def get_all(
        self, *, redact: bool = True, provenance: bool = False
    ) -> dict[str, Any]:
        """Every resolved ``{path: value}``; secrets masked when ``redact=True``.

        With ``provenance=True`` each value becomes ``{"value": v, "provenance":
        <layer>}`` (layer = env|store|profile|default), the shape a panel badges.
        Redaction still applies to the ``value``.
        """
        values: dict[str, Any] = {}
        provs: dict[str, str] = {}
        for module in self.registry.list_modules():
            props = self.registry.get_schema(module).get("properties", {})
            for fld in props:
                path = f"{module}.{fld}"
                values[path] = self.get(path)
                if provenance:
                    provs[path] = self.provenance(path)
        if redact:
            values = redact_all(self.registry, values)
        if not provenance:
            return values
        return {
            path: {"value": values[path], "provenance": provs[path]} for path in values
        }

    def _require_path(self, path: str) -> None:
        module, fld = split_path(path)
        if not self.registry.has(module):
            raise UnknownPathError(
                f"unknown config module: {module!r} "
                f"(known: {', '.join(self.registry.list_modules()) or '<none>'})"
            )
        if fld not in self.registry.get_schema(module).get("properties", {}):
            raise UnknownPathError(f"unknown field: {path!r}")

    # -------------------------------------------------------------- full state
    def _persisted_state(self) -> dict[str, dict[str, Any]]:
        """Raw persisted config+secrets (NOT env-merged), for faithful snapshots."""
        return {
            "config": json_copy(self.backend.load_config()),
            "secrets": json_copy(self.backend.load_secrets()),
        }

    def _flatten_persisted(
        self, persisted: dict[str, dict[str, Any]]
    ) -> dict[str, Any]:
        """Flatten a ``{config, secrets}`` state to a flat ``{module.field: value}``.

        Only registered paths are emitted (so reload classification / redaction —
        which look up field metadata — never see an unknown path). Used to diff the
        PERSISTED layer for rollback (defect #2).
        """
        flat: dict[str, Any] = {}
        for bucket in (persisted.get("config", {}), persisted.get("secrets", {})):
            for module, fields in bucket.items():
                if not self.registry.has(module):
                    continue
                props = self.registry.get_schema(module).get("properties", {})
                for fld, value in fields.items():
                    if fld in props:
                        flat[f"{module}.{fld}"] = value
        return flat

    def persisted_flat(self) -> dict[str, Any]:
        """The flat ``{module.field: value}`` map of the PERSISTED layer only.

        This is the config+secrets a write can actually change — env overrides
        and unset schema defaults are excluded. It is the correct basis for an
        upper layer's concurrency ETag: an env-pinned field's persisted value
        still shows here, so any write mutates the map (and thus the tag) even
        when the env-merged ``get_all`` view would not move (the L1 defect #2
        class — the resolved view hides persisted changes). Public seam over
        the private ``_flatten_persisted``/``_persisted_state`` so L2 need not
        reach into internals.
        """
        return self._flatten_persisted(self._persisted_state())

    def _persisted_module_state(self, module: str) -> dict[str, Any]:
        """The validate/diff merge-base slice for ``module``: store > profile > default.

        Validation/diff/rollback classification operate on the layers the store can
        control or that are already active WITHOUT env. Env is a read-time overlay
        only: gating writes on an env-sourced value the store cannot change would
        let a bad/unrelated env var brick every write (defect #10). The profile
        layer, by contrast, IS a live read layer, so the merge-base must include it
        (store > profile > default) or ``validate`` would disagree with ``get`` for
        a key supplied only by the profile — the design calls this out explicitly.
        Missing fields fall back to schema default so the slice is a complete,
        validatable module state.
        """
        config = self.backend.load_config().get(module, {})
        secrets = self.backend.load_secrets().get(module, {})
        props = self.registry.get_schema(module).get("properties", {})
        state: dict[str, Any] = {}
        for fld in props:
            if fld in secrets:
                state[fld] = secrets[fld]
            elif fld in config:
                state[fld] = config[fld]
            else:
                prof = self._profile_value(module, fld)
                state[fld] = (
                    prof if prof is not _NO_ENV else self._default_value(module, fld)
                )
        return state

    # ---------------------------------------------------------------- validate
    def validate(self, partial: dict[str, Any]) -> list:
        """Validate ``partial`` (flat ``{path: value}``) merged onto persisted state.

        Only the modules referenced in ``partial`` are validated, and their base
        state is the PERSISTED layer (not env-merged, not all modules): a malformed
        env var on an untouched module can never make a valid edit fail (defect
        #10/#12). Returns a list of :class:`~rtime_admin_core.errors.FieldError`
        (empty = ok). Does NOT write.
        """
        merged = self._merge_partial(partial)
        return validate_state(self.registry, merged)

    def _merge_partial(self, partial: dict[str, Any]) -> dict[str, dict[str, Any]]:
        """Apply a flat ``{path: value}`` onto the PERSISTED state of ONLY the
        referenced modules.

        Building the base from ``_persisted_module_state`` for exactly the touched
        modules keeps validation env-independent and scoped to the change set.
        """
        for path in partial:
            self._require_path(path)
        touched: dict[str, dict[str, Any]] = {}
        for path, value in partial.items():
            module, fld = split_path(path)
            if module not in touched:
                touched[module] = self._persisted_module_state(module)
            touched[module][fld] = value
        return touched

    # -------------------------------------------------------------------- diff
    def diff(self, new: dict[str, Any], *, redact: bool = True) -> dict[str, Any]:
        """before/after for a proposed flat ``{path: value}`` change set.

        Compares the current resolved values against ``new`` on exactly the given
        paths; secret values are hashed when ``redact=True``.
        """
        before: dict[str, Any] = {}
        after: dict[str, Any] = {}
        for path, value in new.items():
            self._require_path(path)
            before[path] = self.get(path)
            after[path] = value
        d = compute_diff(before, after)
        if redact:
            return redact_diff(self.registry, d, salt=self._secret_salt)
        return d

    # --------------------------------------------------------------------- set
    def set(self, path: str, value: Any, **apply_kwargs: Any) -> ApplyResult:
        """Convenience single-path :meth:`apply`. Requires the same ts/actor kwargs."""
        return self.apply({path: value}, **apply_kwargs)

    # ------------------------------------------------------------------- apply
    def apply(
        self,
        changes: dict[str, Any],
        *,
        ts: str,
        snapshot_id: str,
        actor: str = "system",
        source: str = "core",
        note: str | None = None,
    ) -> ApplyResult:
        """Transactionally apply a flat ``{path: value}`` change set.

        Steps (docs/development-plan §五 L1):
          1. validate the change set against the PERSISTED state of the referenced
             modules only; on failure -> audit(error) + raise.
          2. snapshot the current persisted state to history (capped). A
             snapshot-step failure is audited (error) then re-raised.
          3. write the changes atomically (secrets to the secret store, rest to
             config); on ANY write failure, restore the pre-write persisted state,
             audit(error), and re-raise — never leave a half-applied store.
          4. emit ONE audit entry with a redacted before/after diff.
          5. return the affected paths partitioned into hot / restart-required.

        ``ts`` and ``snapshot_id`` are injected by the caller (never generated
        here) so the transaction is deterministic.
        """
        if not changes:
            raise ValueError("apply requires a non-empty change set")
        for path in changes:
            self._require_path(path)

        # diff (redacted) computed up front so it is in the audit even on failure
        redacted_diff = self.diff(changes, redact=True)
        paths = sorted(changes)

        # 1. validate
        errors = self.validate(changes)
        if errors:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="apply",
                    outcome=OUTCOME_ERROR,
                    paths=paths,
                    diff=redacted_diff,
                    snapshot_id=None,
                    detail="; ".join(f"{e.path}: {e.message}" for e in errors),
                )
            )
            raise AdminValidationError(errors)

        # capture the pre-write persisted state so we can (a) snapshot it and
        # (b) roll back to it if any write fails (defect #1 — atomicity across the
        # separate config + secrets stores).
        pre_write = self._persisted_state()

        # 2. snapshot BEFORE writing. A snapshot-step failure (e.g. duplicate
        #    snapshot_id) must be audited like any other apply failure (defect #5),
        #    not silently propagated with no audit line.
        try:
            self._take_snapshot(snapshot_id, ts, note=note)
        except Exception as exc:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="apply",
                    outcome=OUTCOME_ERROR,
                    paths=paths,
                    diff=redacted_diff,
                    snapshot_id=None,
                    detail=f"snapshot failed: {exc}",
                )
            )
            raise

        # 3. write — compensating: on ANY write failure restore the pre-write
        #    persisted state so a partial (config-committed, secrets-failed) apply
        #    cannot leave the store half-applied (defect #1).
        try:
            self._write_changes(changes)
        except Exception as exc:
            self._restore_persisted(pre_write)
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="apply",
                    outcome=OUTCOME_ERROR,
                    paths=paths,
                    diff=redacted_diff,
                    snapshot_id=snapshot_id,
                    detail=f"write failed, rolled back: {exc}",
                )
            )
            raise

        # 5. classify reload semantics
        hot, restart = self._classify_reload(changes)

        # 4. audit success
        self._audit(
            AuditEntry(
                ts=ts,
                actor=actor,
                source=source,
                action="apply",
                outcome=OUTCOME_OK,
                paths=paths,
                diff=redacted_diff,
                snapshot_id=snapshot_id,
                detail=note,
            )
        )
        return ApplyResult(
            snapshot_id=snapshot_id,
            changed=paths,
            hot=hot,
            restart_required=restart,
            diff=redacted_diff,
        )

    def _classify_reload(self, changes: dict[str, Any]) -> tuple[list[str], list[str]]:
        hot: list[str] = []
        restart: list[str] = []
        for path in sorted(changes):
            meta = field_meta(self.registry, path)
            (hot if meta.reload == "hot" else restart).append(path)
        return hot, restart

    def classify_reload(self, changes: dict[str, Any]) -> tuple[list[str], list[str]]:
        """Public dry-run helper (J1): partition change paths into (hot, restart)
        by x-reload metadata WITHOUT applying anything. Unknown paths are skipped
        (the caller validates separately). Lets the admin-api /validate endpoint
        answer "which of these would need a restart" before an operator/agent commits."""
        known = {p: v for p, v in changes.items() if self._is_known(p)}
        return self._classify_reload(known)

    def _is_known(self, path: str) -> bool:
        try:
            field_meta(self.registry, path)
            return True
        except (KeyError, ValueError):
            return False

    def _write_changes(self, changes: dict[str, Any]) -> None:
        config = self.backend.load_config()
        secrets = self.backend.load_secrets()
        cfg_dirty = sec_dirty = False
        for path, value in changes.items():
            module, fld = split_path(path)
            meta = field_meta(self.registry, path)
            target = secrets if meta.secret else config
            target.setdefault(module, {})[fld] = value
            if meta.secret:
                sec_dirty = True
            else:
                cfg_dirty = True
        if cfg_dirty:
            self.backend.save_config(config)
        if sec_dirty:
            self.backend.save_secrets(secrets)

    def _restore_persisted(self, persisted: dict[str, dict[str, Any]]) -> None:
        """Restore config+secrets to a captured ``_persisted_state`` snapshot.

        Used to compensate a failed :meth:`apply` write so the persisted state
        ends equal to the pre-apply state (defect #1). Best-effort per store: if a
        restore write itself raises it is swallowed so the ORIGINAL failure (which
        the caller re-raises) is not masked.
        """
        try:
            self.backend.save_config(json_copy(persisted["config"]))
        except Exception:  # pragma: no cover - restore-of-restore is best effort
            pass
        try:
            self.backend.save_secrets(json_copy(persisted["secrets"]))
        except Exception:  # pragma: no cover
            pass

    # ---------------------------------------------------------------- snapshot
    def snapshot(self, snapshot_id: str, ts: str, *, note: str | None = None) -> str:
        """Explicitly snapshot the current persisted state; returns the id."""
        return self._take_snapshot(snapshot_id, ts, note=note)

    def _take_snapshot(self, snapshot_id: str, ts: str, *, note: str | None) -> str:
        persisted = self._persisted_state()
        snap = snapshot_state(
            snapshot_id, ts, persisted["config"], persisted["secrets"], note=note
        )
        self.history.add(snap)
        self.history.prune(self.max_history)
        return snapshot_id

    def list_history(self) -> list[dict[str, Any]]:
        """Snapshot descriptors, newest first (id/ts/note; no payload)."""
        return [s.to_meta() for s in reversed(self.history.list())]

    # ---------------------------------------------------------------- rollback
    def rollback_changed_paths(self, snapshot_id: str) -> list[str]:
        """The persisted paths a rollback to ``snapshot_id`` WOULD change.

        Pure preview (no write, no snapshot, no audit): diffs the current
        persisted layer against the snapshot's, on the persisted layer (same
        basis ``rollback`` itself uses — env-pinned changes included). Raises
        :class:`SnapshotNotFoundError` for an unknown id. An upper layer uses
        this to enforce per-field write scope BEFORE performing the rollback,
        so a write verb can never bypass field-level x-scope by going through
        rollback (every write verb enforces field scope, not just PATCH).
        """
        target = self.history.get(snapshot_id)
        if target is None:
            raise SnapshotNotFoundError(f"unknown snapshot id: {snapshot_id!r}")
        before_persisted = self._flatten_persisted(self._persisted_state())
        target_persisted = self._flatten_persisted(
            {"config": target.config, "secrets": target.secrets}
        )
        return sorted(compute_diff(before_persisted, target_persisted))

    def rollback(
        self,
        snapshot_id: str,
        *,
        ts: str,
        new_snapshot_id: str,
        actor: str = "system",
        source: str = "core",
    ) -> ApplyResult:
        """Restore the persisted state captured in ``snapshot_id``.

        Snapshots the CURRENT state first (under ``new_snapshot_id``) so a rollback
        is itself reversible, then restores, then audits (rollback is one audit
        line, per §五). Returns the changed paths with reload classification.
        """
        target = self.history.get(snapshot_id)
        if target is None:
            raise SnapshotNotFoundError(f"unknown snapshot id: {snapshot_id!r}")

        # Diff/classify from the PERSISTED layer, not the env-merged view: a path
        # that is env-pinned would show no diff under get_all even though the
        # persisted layer really changed, yielding an empty audit diff and no
        # restart signal (defect #2). Flattening config+secrets to module.field
        # records the real persisted change.
        before_persisted = self._flatten_persisted(self._persisted_state())
        target_persisted = self._flatten_persisted(
            {"config": target.config, "secrets": target.secrets}
        )

        # snapshot current, then restore target's persisted state wholesale
        self._take_snapshot(new_snapshot_id, ts, note=f"pre-rollback to {snapshot_id}")
        self.backend.save_config(json_copy(target.config))
        self.backend.save_secrets(json_copy(target.secrets))

        raw_diff = compute_diff(before_persisted, target_persisted)
        changed = sorted(raw_diff)
        redacted = redact_diff(self.registry, raw_diff, salt=self._secret_salt)
        hot, restart = self._classify_reload({p: None for p in changed})

        self._audit(
            AuditEntry(
                ts=ts,
                actor=actor,
                source=source,
                action="rollback",
                outcome=OUTCOME_OK,
                paths=changed,
                diff=redacted,
                snapshot_id=new_snapshot_id,
                detail=f"restored snapshot {snapshot_id}",
            )
        )
        return ApplyResult(
            snapshot_id=new_snapshot_id,
            changed=changed,
            hot=hot,
            restart_required=restart,
            diff=redacted,
        )

    # --------------------------------------------------------------- clear-override
    def unset(
        self,
        path: str,
        *,
        ts: str,
        snapshot_id: str,
        actor: str = "system",
        source: str = "core",
        note: str | None = None,
    ) -> ApplyResult:
        """Clear a store override for ``path`` — the value falls back to the layer
        below (profile, else schema default). K8s SSA's ownership hand-off, NOT a
        global "file wins / UI wins" toggle (that Open WebUI anti-pattern).

        Full transaction like :meth:`apply` (snapshot before + one audit entry,
        ``action="unset"``): validate the post-delete state of the module (so the
        fallback value is itself valid) -> snapshot -> delete the key (compensating
        on failure) -> audit -> classify hot/restart. Deleting a key the store
        never had is a no-op success (empty diff), so ``unset`` is idempotent.

        ``ts`` / ``snapshot_id`` are injected by the caller (deterministic).
        """
        self._require_path(path)
        module, fld = split_path(path)

        before_val = self.get(path)  # env-merged effective value (for the diff)
        # Is there actually a store override to clear?
        stored = self._stored_value(module, fld)
        if stored is _NO_ENV:
            # nothing to clear: idempotent no-op success, still snapshotted+audited
            # so the intent is on the record, but the diff is empty.
            self._take_snapshot(snapshot_id, ts, note=note)
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="unset",
                    outcome=OUTCOME_OK,
                    paths=[path],
                    diff={},
                    snapshot_id=snapshot_id,
                    detail=note or "no store override to clear (no-op)",
                )
            )
            return ApplyResult(
                snapshot_id=snapshot_id, changed=[], hot=[], restart_required=[]
            )

        # 1. validate the module state AFTER the delete (fallback must be valid).
        post = self._persisted_module_state_without(module, fld)
        errors = validate_state(self.registry, {module: post})
        if errors:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="unset",
                    outcome=OUTCOME_ERROR,
                    paths=[path],
                    diff={},
                    snapshot_id=None,
                    detail="; ".join(f"{e.path}: {e.message}" for e in errors),
                )
            )
            raise AdminValidationError(errors)

        pre_write = self._persisted_state()
        try:
            self._take_snapshot(snapshot_id, ts, note=note)
        except Exception as exc:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="unset",
                    outcome=OUTCOME_ERROR,
                    paths=[path],
                    diff={},
                    snapshot_id=None,
                    detail=f"snapshot failed: {exc}",
                )
            )
            raise

        # 2. delete the key (compensating on failure).
        try:
            self._delete_key(module, fld)
        except Exception as exc:
            self._restore_persisted(pre_write)
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="unset",
                    outcome=OUTCOME_ERROR,
                    paths=[path],
                    diff={},
                    snapshot_id=snapshot_id,
                    detail=f"delete failed, rolled back: {exc}",
                )
            )
            raise

        after_val = self.get(path)  # now resolves to profile / default
        raw_diff = compute_diff({path: before_val}, {path: after_val})
        redacted = redact_diff(self.registry, raw_diff, salt=self._secret_salt)
        hot, restart = self._classify_reload({p: None for p in raw_diff})
        self._audit(
            AuditEntry(
                ts=ts,
                actor=actor,
                source=source,
                action="unset",
                outcome=OUTCOME_OK,
                paths=[path],
                diff=redacted,
                snapshot_id=snapshot_id,
                detail=note,
            )
        )
        return ApplyResult(
            snapshot_id=snapshot_id,
            changed=sorted(raw_diff),
            hot=hot,
            restart_required=restart,
            diff=redacted,
        )

    def _persisted_module_state_without(
        self, module: str, drop_field: str
    ) -> dict[str, Any]:
        """Like :meth:`_persisted_module_state` but as if ``drop_field``'s store
        override were already gone (so it resolves to profile/default)."""
        config = self.backend.load_config().get(module, {})
        secrets = self.backend.load_secrets().get(module, {})
        props = self.registry.get_schema(module).get("properties", {})
        state: dict[str, Any] = {}
        for fld in props:
            if fld == drop_field:
                prof = self._profile_value(module, fld)
                state[fld] = (
                    prof if prof is not _NO_ENV else self._default_value(module, fld)
                )
            elif fld in secrets:
                state[fld] = secrets[fld]
            elif fld in config:
                state[fld] = config[fld]
            else:
                prof = self._profile_value(module, fld)
                state[fld] = (
                    prof if prof is not _NO_ENV else self._default_value(module, fld)
                )
        return state

    def _delete_key(self, module: str, fld: str) -> None:
        """Remove a store override from whichever store (config/secret) holds it."""
        meta = field_meta(self.registry, f"{module}.{fld}")
        if meta.secret:
            secrets = self.backend.load_secrets()
            if module in secrets and fld in secrets[module]:
                del secrets[module][fld]
                if not secrets[module]:
                    del secrets[module]
                self.backend.save_secrets(secrets)
        else:
            config = self.backend.load_config()
            if module in config and fld in config[module]:
                del config[module][fld]
                if not config[module]:
                    del config[module]
                self.backend.save_config(config)

    # -------------------------------------------------------------- profile layer
    @property
    def profile_layer(self) -> dict[str, Any]:
        """A copy of the active profile layer (read-only view)."""
        return dict(self._profile_layer)

    def reload_profile(
        self,
        new_layer: dict[str, Any],
        *,
        ts: str,
        snapshot_id: str,
        actor: str = "system",
        source: str = "core",
        note: str | None = None,
    ) -> ApplyResult:
        """Atomic validate-then-swap of the whole compiled profile layer.

        Caddy ``/load`` semantics: validate the ENTIRE new layer merged with the
        live store view; on ANY failure keep the old layer active and raise — never
        a partial swap. On success: snapshot BEFORE the swap (provenance of the
        pre-swap state), swap the layer atomically, then emit ONE audit entry
        (``action="profile_reload"``) with the before/after diff and the changed
        paths partitioned hot vs restart-required. The diff/classification are
        computed from the NON-env view (store > profile > default), so an env-pinned
        path's real profile change is still recorded (defect #4).

        ``new_layer`` keys must be registered ``module.field`` paths; an unknown
        path or a key whose field is ``x-secret`` is a hard failure (a profile must
        never carry a secret — the loader also rejects this, this is defence in
        depth). ``ts`` / ``snapshot_id`` injected by the caller.
        """
        # 0. keys must be known paths, and none may be a secret field.
        errs: list = []
        for path in new_layer:
            try:
                self._require_path(path)
            except UnknownPathError as exc:
                errs.append(FieldError(path=path, message=str(exc)))
                continue
            if field_meta(self.registry, path).secret:
                errs.append(
                    FieldError(
                        path=path,
                        message="secret field must not appear in a profile layer",
                    )
                )
        if errs:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="profile_reload",
                    outcome=OUTCOME_ERROR,
                    paths=sorted(new_layer),
                    diff={},
                    snapshot_id=None,
                    detail="; ".join(f"{e.path}: {e.message}" for e in errs),
                )
            )
            raise AdminValidationError(errs)

        # capture the NON-env view (store > profile > default) BEFORE the swap, so
        # the reload diff/classification reflect the real profile change even for a
        # path that env pins: get_all() would show the env value on both sides and
        # report an empty diff / no restart signal (defect #2 / #4 — the same class
        # as rollback's persisted-layer diff). Env is a read-time overlay the reload
        # does not touch, so it must not participate in the reload diff.
        before_effective = self._flatten_store_profile()

        # 1. validate the WHOLE new layer merged with live store/env: build each
        #    touched module's full state as store > NEW-profile > default, then run
        #    the env-independent validator. On failure keep the old layer (no swap).
        touched_modules = {split_path(p)[0] for p in new_layer}
        # also validate modules the OLD layer touched but the new one drops, so a
        # removed profile key that leaves an invalid fallback is caught too.
        touched_modules |= {split_path(p)[0] for p in self._profile_layer}
        merged: dict[str, dict[str, Any]] = {}
        for module in touched_modules:
            merged[module] = self._module_state_with_profile(module, new_layer)
        verrors = validate_state(self.registry, merged)
        if verrors:
            self._audit(
                AuditEntry(
                    ts=ts,
                    actor=actor,
                    source=source,
                    action="profile_reload",
                    outcome=OUTCOME_ERROR,
                    paths=sorted(new_layer),
                    diff={},
                    snapshot_id=None,
                    detail="; ".join(f"{e.path}: {e.message}" for e in verrors),
                )
            )
            raise AdminValidationError(verrors)

        # 2. snapshot the store BEFORE swapping (profile isn't in the store, but a
        #    reload is a config-affecting event; the snapshot records the store
        #    state at reload time for provenance / rollback of concurrent edits).
        self._take_snapshot(snapshot_id, ts, note=note or "pre-profile-reload")

        # 3. atomic swap (a single attribute assignment — no half state possible).
        self._profile_layer = dict(new_layer)

        # 4. classify + audit ONE entry. Diff the NON-env view (store > profile >
        #    default) on both sides so an env-pinned path's real profile change is
        #    still recorded (defect #4); env never participates in the reload diff.
        after_effective = self._flatten_store_profile()
        raw_diff = compute_diff(before_effective, after_effective)
        changed = sorted(raw_diff)
        redacted = redact_diff(self.registry, raw_diff, salt=self._secret_salt)
        hot, restart = self._classify_reload({p: None for p in changed})
        self._audit(
            AuditEntry(
                ts=ts,
                actor=actor,
                source=source,
                action="profile_reload",
                outcome=OUTCOME_OK,
                paths=changed,
                diff=redacted,
                snapshot_id=snapshot_id,
                detail=note,
            )
        )
        return ApplyResult(
            snapshot_id=snapshot_id,
            changed=changed,
            hot=hot,
            restart_required=restart,
            diff=redacted,
        )

    def _flatten_store_profile(self) -> dict[str, Any]:
        """Flat ``{module.field: value}`` of the NON-env effective view.

        Precedence store > profile > default for EVERY registered path (env
        excluded). This is the layer a profile reload actually changes, so diffing
        it on both sides of the swap records the real change even where env pins the
        get()-visible value (defect #4 — mirrors rollback's ``_flatten_persisted``).
        """
        flat: dict[str, Any] = {}
        config = self.backend.load_config()
        secrets = self.backend.load_secrets()
        for module in self.registry.list_modules():
            props = self.registry.get_schema(module).get("properties", {})
            cfg_m = config.get(module, {})
            sec_m = secrets.get(module, {})
            for fld in props:
                path = f"{module}.{fld}"
                if fld in sec_m:
                    flat[path] = sec_m[fld]
                elif fld in cfg_m:
                    flat[path] = cfg_m[fld]
                else:
                    prof = self._profile_value(module, fld)
                    flat[path] = (
                        prof
                        if prof is not _NO_ENV
                        else self._default_value(module, fld)
                    )
        return flat

    def _module_state_with_profile(
        self, module: str, profile_layer: dict[str, Any]
    ) -> dict[str, Any]:
        """Full state for ``module`` as store > (given profile_layer) > default.

        Used by :meth:`reload_profile` to validate a CANDIDATE profile layer
        (not ``self._profile_layer``) without swapping it in first.
        """
        config = self.backend.load_config().get(module, {})
        secrets = self.backend.load_secrets().get(module, {})
        props = self.registry.get_schema(module).get("properties", {})
        state: dict[str, Any] = {}
        for fld in props:
            if fld in secrets:
                state[fld] = secrets[fld]
            elif fld in config:
                state[fld] = config[fld]
            elif f"{module}.{fld}" in profile_layer:
                state[fld] = profile_layer[f"{module}.{fld}"]
            else:
                state[fld] = self._default_value(module, fld)
        return state

    def drift_report(self) -> list[dict[str, Any]]:
        """Shadowed keys: paths where a store override differs from the profile value.

        For each store-overridden path that the profile layer ALSO supplies with a
        different value, report ``{path, store, profile, secret}`` (values redacted
        for secret fields). This is the "declared, not discovered" drift list the
        panel/doctor surfaces (Grafana/ArgoCD selfHeal lesson); the companion verb
        is :meth:`unset` (clear the override so the profile value wins again).
        """
        out: list[dict[str, Any]] = []
        config = self.backend.load_config()
        secrets = self.backend.load_secrets()
        for path, prof_val in sorted(self._profile_layer.items()):
            try:
                module, fld = split_path(path)
            except ValueError:
                continue
            if not self.registry.has(module):
                continue
            meta = field_meta(self.registry, path)
            bucket = secrets if meta.secret else config
            if module in bucket and fld in bucket[module]:
                store_val = bucket[module][fld]
                if store_val != prof_val:
                    if meta.secret:
                        out.append(
                            {
                                "path": path,
                                "store": REDACTED_PLACEHOLDER,
                                "profile": REDACTED_PLACEHOLDER,
                                "secret": True,
                            }
                        )
                    else:
                        out.append(
                            {
                                "path": path,
                                "store": store_val,
                                "profile": prof_val,
                                "secret": False,
                            }
                        )
        return out

    # ------------------------------------------------------------------- audit
    def _audit(self, entry: AuditEntry) -> None:
        if self.audit_hook is not None:
            self.audit_hook(entry)


__all__ = [
    "ConfigStore",
    "ApplyResult",
    "ValidationError",
    "PROV_DEFAULT",
    "PROV_PROFILE",
    "PROV_STORE",
    "PROV_ENV",
]
