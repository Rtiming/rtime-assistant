# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Generate the brain-library gateway policy JSON from ``profile.library`` (§2.7).

``library.scope`` is the SINGLE source of truth for a read-only consumer's library
reach. This module compiles it into the ``library-policy.json`` the scoped gateway
process (deploy/systemd/user/rtime-library-gateway-public.service, HTTP 8781) loads
— matching the semantics of the existing hand-written
``packages/rtime-library-gateway/policy/studentunion-policy.json``:

  - ``allowed_path_prefixes`` = ``library.scope`` (every read is confined to these
    brain subtrees; gate-enforced);
  - ``excluded_top_dirs`` = ``["personal-data", "profile"]`` (personal data is never
    reachable by a public consumer);
  - ``redact_sensitive`` / ``hide_excluded_in_results`` from the profile (default
    True for a scoped consumer);
  - ``default_read=allow`` / ``default_write=deny`` and the read-only method
    allow/deny lists (writes denied by three independent locks — §2.7 double-lock).

``methods`` is intentionally omitted so tiers come from METHOD_TIERS in the gateway
(the policy can never drift behind newly added methods — a new method is denied by
the non-empty allow list until explicitly added). The generated JSON is byte-stable
(sorted keys) so a doctor cross-check / golden test can diff it deterministically.

This is the single-source -> policy generation the design calls out; a doctor check
(part G) asserts ``profile.library.scope == generated allowed_path_prefixes ==
(documented) live mount subtree``.
"""

from __future__ import annotations

import json
from typing import Any

# Policy file format version, mirrors studentunion-policy.json.
POLICY_SCHEMA_VERSION = 1

# A public/scoped consumer never sees personal data (design §2.7).
DEFAULT_EXCLUDED_TOP_DIRS = ("personal-data", "profile")

# Read-only method allow/deny lists — copied verbatim from the semantics of
# studentunion-policy.json (the gate resolves tiers from METHOD_TIERS regardless).
_READ_ONLY_ALLOW = (
    "lib.doctor",
    "lib.policy",
    "lib.search",
    "lib.read",
    "lib.tree",
    "lib.stat",
    "lib.recent",
    "lib.list",
)
_WRITE_DENY = (
    "lib.settings.*",
    "lib.contribute",
    "lib.finalize",
    "lib.course-intake",
    "lib.jobs.*",
)


def build_library_policy(
    scope: list[str],
    *,
    redact_sensitive: bool = True,
    hide_excluded_in_results: bool = True,
    excluded_top_dirs: tuple[str, ...] = DEFAULT_EXCLUDED_TOP_DIRS,
    audit_log: str = "{STATE}/rtime-library-gateway/audit-public.jsonl",
) -> dict[str, Any]:
    """Compile ``library.scope`` into the gateway policy dict (§2.7).

    ``scope`` becomes ``allowed_path_prefixes`` (the single source that also drives
    the compose read-only sub-mount list). Raises ``ValueError`` on an empty scope —
    a scoped read-only gateway with no allowed prefix would deny every read, which is
    a configuration error, not a valid "open everything" state.
    """
    if not scope:
        raise ValueError(
            "library.scope is empty: a scoped gateway policy needs at least one "
            "allowed_path_prefix (an empty scope would deny every read)"
        )
    return {
        "schema_version": POLICY_SCHEMA_VERSION,
        "default_read": "allow",
        "default_write": "deny",
        "excluded_top_dirs": list(excluded_top_dirs),
        "redact_sensitive": bool(redact_sensitive),
        "hide_excluded_in_results": bool(hide_excluded_in_results),
        "allowed_path_prefixes": list(scope),
        "audit_log": audit_log,
        "clients": {
            "default": {
                "allow": list(_READ_ONLY_ALLOW),
                "deny": list(_WRITE_DENY),
            }
        },
    }


def render_library_policy_json(policy: dict[str, Any]) -> str:
    """Serialize a policy dict to a byte-stable JSON string (sorted keys, trailing \\n).

    Deterministic so a doctor cross-check / golden test can diff the generated file
    against the profile without spurious churn.
    """
    return json.dumps(policy, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


__all__ = [
    "POLICY_SCHEMA_VERSION",
    "DEFAULT_EXCLUDED_TOP_DIRS",
    "build_library_policy",
    "render_library_policy_json",
]
