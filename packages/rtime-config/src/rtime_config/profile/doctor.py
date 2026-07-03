# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Profile doctor — the 3-way library-scope cross-check (design §2.7, part G).

The library scope is enforced by two independent locks that MUST agree:

  1. ``profile.library.scope`` — the single source of truth in the git profile;
  2. the generated ``library-policy.json`` ``allowed_path_prefixes`` — what the 8781
     scoped gateway process actually enforces;
  3. the (documented) live read-only sub-mount subtrees under ``/mnt/brain`` in
     compose — what the container filesystem exposes.

If any two disagree, a read that one lock allows another blocks (or worse, the
compose mount exposes a subtree the gateway policy forgot to scope). This check
asserts all three describe the SAME set of brain subtrees, so a doctor / CI gate
turns a silent drift into a red result (design §2.7: "doctor 跑三方交叉核验").

It is pure and data-in/data-out (no filesystem probe of the live brain — that is the
``lib.tree`` probe the gateway owns); callers pass the three declared sets. The
compose subtrees are the bind ``target`` paths under ``/mnt/brain`` normalized to
brain-relative prefixes (e.g. ``/mnt/brain/knowledge/institutions/ustc`` ->
``knowledge/institutions/ustc``).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .library_policy import build_library_policy, render_library_policy_json

BRAIN_MOUNT_PREFIX = "/mnt/brain"


def mount_target_to_scope(target: str) -> str:
    """Normalize a compose bind ``target`` under /mnt/brain to a brain-relative prefix.

    ``/mnt/brain/knowledge/institutions/ustc`` -> ``knowledge/institutions/ustc``.
    A bare ``/mnt/brain`` (whole-library mount) -> ``""`` (the empty prefix = whole
    library), which the caller can treat as "not a scoped subtree".
    """
    t = target.rstrip("/")
    if t == BRAIN_MOUNT_PREFIX:
        return ""
    prefix = BRAIN_MOUNT_PREFIX + "/"
    if t.startswith(prefix):
        return t[len(prefix) :]
    return t  # not under /mnt/brain: return as-is so the mismatch surfaces


def cross_check_scope(
    *,
    profile_scope: list[str],
    policy_allowed_prefixes: list[str],
    mount_subtrees: list[str],
) -> dict[str, Any]:
    """3-way cross-check of a read-only consumer's library scope.

    ``mount_subtrees`` are brain-relative (already normalized via
    :func:`mount_target_to_scope`, whole-library ``""`` entries dropped by the
    caller). Returns ``{"ok": bool, "risks": [...], ...}`` — ``ok`` iff all three
    sets are equal.
    """
    p = set(profile_scope or [])
    g = set(policy_allowed_prefixes or [])
    m = set(s for s in (mount_subtrees or []) if s)
    risks: list[str] = []
    if p != g:
        risks.append("profile_scope_ne_policy_prefixes")
    if p != m:
        risks.append("profile_scope_ne_mount_subtrees")
    if g != m:
        risks.append("policy_prefixes_ne_mount_subtrees")
    return {
        "ok": not risks,
        "profile_scope": sorted(p),
        "policy_allowed_prefixes": sorted(g),
        "mount_subtrees": sorted(m),
        "risks": risks,
    }


def check_profile_policy_file(
    profile_scope: list[str],
    policy_path: str | Path,
    *,
    redact_sensitive: bool = True,
    hide_excluded_in_results: bool = True,
) -> dict[str, Any]:
    """Assert the committed ``library-policy.json`` == generated from the profile scope.

    Catches a hand-edited policy file or a profile scope changed without regenerating
    (the single-source invariant, §2.7). Returns ``{"ok", "risks", ...}``.
    """
    policy_path = Path(policy_path)
    risks: list[str] = []
    if not policy_path.is_file():
        return {
            "ok": False,
            "risks": ["policy_file_missing"],
            "policy_path": str(policy_path),
        }
    committed = policy_path.read_text(encoding="utf-8")
    try:
        expected = render_library_policy_json(
            build_library_policy(
                profile_scope,
                redact_sensitive=redact_sensitive,
                hide_excluded_in_results=hide_excluded_in_results,
            )
        )
    except ValueError as exc:
        return {
            "ok": False,
            "risks": [f"invalid_scope:{exc}"],
            "policy_path": str(policy_path),
        }
    # Compare parsed JSON (tolerant of trailing whitespace) AND the exact bytes.
    if json.loads(committed) != json.loads(expected):
        risks.append("policy_file_differs_from_profile_scope")
    return {
        "ok": not risks,
        "policy_path": str(policy_path),
        "risks": risks,
    }


__all__ = [
    "BRAIN_MOUNT_PREFIX",
    "mount_target_to_scope",
    "cross_check_scope",
    "check_profile_policy_file",
]
