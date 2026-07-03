# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Config diffing and secret redaction.

Two jobs, both pure:

  - :func:`compute_diff` — a before/after map ``path -> {"before", "after"}`` for
    exactly the paths whose value changed. Used by ``ConfigStore.diff`` and by the
    apply/rollback audit entries.

  - :func:`redact_value` / :func:`redact_diff` — replace secret values with a
    stable hashed placeholder so audit and ``get_all(redact=True)`` never leak a
    credential yet still let an operator see *that* a secret changed (the hash of
    before != hash of after).

Redaction produces a keyed (salted) digest so equal secrets hash equal (visible
"unchanged") and different secrets differ WITHIN one deployment, yet an attacker
holding a shipped audit log cannot brute-force / confirm a low-entropy secret
without the store's salt. ``None`` (unset secret) stays ``None`` so "was it set?"
is still legible.

The salt is a per-store secret keyed into an HMAC-SHA256 (see
``ConfigStore.secret_salt``). ``hash_secret`` accepts an explicit ``salt``; the
zero-arg legacy call (no salt) keeps a bare, clearly-labelled ``sha256:`` prefix
for callers that only need "did it change" within a single process and do not
persist the digest — but all audit paths supply the store salt.
"""

from __future__ import annotations

import hashlib
import hmac
from typing import Any

from .metadata import REDACTED_PLACEHOLDER
from .registry import Registry

_MISSING = object()


def hash_secret(value: Any, *, salt: str | None = None) -> str | None:
    """Stable, non-reversible, salt-keyed placeholder for a secret value.

    ``None`` -> ``None`` (keeps "unset" legible). With a ``salt`` -> ``hmac:<12hex>``
    HMAC-SHA256(salt, str(value)); the digest is keyed so it is not enumerable
    from a shipped audit log without the salt (defect #9). Without a salt it falls
    back to an unkeyed ``sha256:<12hex>`` (legacy in-process use only). Equal
    (value, salt) pairs produce equal hashes; different salts diverge.
    """
    if value is None:
        return None
    payload = str(value).encode("utf-8")
    if salt:
        digest = hmac.new(salt.encode("utf-8"), payload, hashlib.sha256).hexdigest()
        return f"hmac:{digest[:12]}"
    digest = hashlib.sha256(payload).hexdigest()[:12]
    return f"sha256:{digest}"


def redact_value(
    registry: Registry, path: str, value: Any, *, salt: str | None = None
) -> Any:
    """Return ``value`` unchanged, or its hashed placeholder if ``path`` is secret."""
    from .metadata import is_secret

    if is_secret(registry, path):
        return hash_secret(value, salt=salt)
    return value


def compute_diff(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    """A ``path -> {"before", "after"}`` map for every changed path.

    Inputs are flat ``{path: value}`` dicts. A path present in only one side is
    reported with the missing side as the sentinel string ``"<unset>"`` so the
    audit line is self-describing.
    """
    diff: dict[str, Any] = {}
    for path in sorted(set(before) | set(after)):
        b = before.get(path, _MISSING)
        a = after.get(path, _MISSING)
        if b == a:
            continue
        diff[path] = {
            "before": "<unset>" if b is _MISSING else b,
            "after": "<unset>" if a is _MISSING else a,
        }
    return diff


def redact_diff(
    registry: Registry, diff: dict[str, Any], *, salt: str | None = None
) -> dict[str, Any]:
    """Redact both sides of every secret path in a ``compute_diff`` result.

    Fails CLOSED: if a path's secret-metadata lookup raises (unknown / renamed
    path), the path is treated as secret and masked rather than passed through raw
    (defect #8) — over-redacting an unknown path is safer than leaking one.
    """
    out: dict[str, Any] = {}
    for path, change in diff.items():
        try:
            secret = _path_is_secret(registry, path)
        except Exception:
            secret = True  # fail closed: unknown path -> assume secret, mask it
        if secret:
            out[path] = {
                "before": _redact_side(change.get("before"), salt=salt),
                "after": _redact_side(change.get("after"), salt=salt),
            }
        else:
            out[path] = dict(change)
    return out


def _redact_side(value: Any, *, salt: str | None = None) -> Any:
    if value == "<unset>" or value is None:
        return value
    return hash_secret(value, salt=salt)


def _path_is_secret(registry: Registry, path: str) -> bool:
    from .metadata import is_secret

    return is_secret(registry, path)


def redact_all(registry: Registry, values: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of a flat ``{path: value}`` map with secret values masked.

    Unlike :func:`hash_secret`, ``get_all(redact=True)`` uses the opaque
    :data:`~rtime_admin_core.metadata.REDACTED_PLACEHOLDER` for *set* secrets and
    keeps ``None`` for unset ones — a panel wants "•••" not a hash.
    """
    from .metadata import is_secret

    out: dict[str, Any] = {}
    for path, value in values.items():
        if is_secret(registry, path) and value is not None:
            out[path] = REDACTED_PLACEHOLDER
        else:
            out[path] = value
    return out
