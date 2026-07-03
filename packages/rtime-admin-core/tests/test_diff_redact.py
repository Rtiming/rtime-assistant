# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Diff computation + secret redaction."""

from __future__ import annotations

from rtime_admin_core import (
    REDACTED_PLACEHOLDER,
    compute_diff,
    default_registry,
    hash_secret,
    redact_all,
    redact_diff,
)


def test_compute_diff_only_changed_paths():
    d = compute_diff({"a": 1, "b": 2}, {"a": 1, "b": 3})
    assert d == {"b": {"before": 2, "after": 3}}


def test_compute_diff_added_removed():
    d = compute_diff({"a": 1}, {"b": 2})
    assert d["a"]["after"] == "<unset>"
    assert d["b"]["before"] == "<unset>"


def test_hash_secret_stable_and_masking():
    assert hash_secret(None) is None
    h1 = hash_secret("sk-abc")
    h2 = hash_secret("sk-abc")
    h3 = hash_secret("sk-xyz")
    assert h1 == h2 != h3
    assert h1.startswith("sha256:")
    assert "sk-abc" not in h1


def test_redact_diff_hashes_secret_sides():
    reg = default_registry()
    raw = {
        "models.ustc_api_key": {"before": "old-key", "after": "new-key"},
        "models.default_model": {"before": "a", "after": "b"},
    }
    red = redact_diff(reg, raw)
    # secret hashed both sides, non-secret untouched
    assert red["models.ustc_api_key"]["before"].startswith("sha256:")
    assert red["models.ustc_api_key"]["after"].startswith("sha256:")
    assert "old-key" not in str(red) and "new-key" not in str(red)
    assert red["models.default_model"] == {"before": "a", "after": "b"}


def test_redact_diff_keeps_unset_and_none_legible():
    reg = default_registry()
    raw = {"models.ustc_api_key": {"before": None, "after": "sk-1"}}
    red = redact_diff(reg, raw)
    assert red["models.ustc_api_key"]["before"] is None
    assert red["models.ustc_api_key"]["after"].startswith("sha256:")


def test_redact_all_masks_set_secret_keeps_unset():
    reg = default_registry()
    out = redact_all(
        reg,
        {
            "models.ustc_api_key": "sk-live",
            "models.litellm_master_key": None,
            "models.default_model": "ds",
        },
    )
    assert out["models.ustc_api_key"] == REDACTED_PLACEHOLDER
    assert out["models.litellm_master_key"] is None  # unset stays legible
    assert out["models.default_model"] == "ds"


def test_store_diff_redacts_secret(store):
    d = store.diff({"models.ustc_api_key": "sk-new"})
    # store diffs are salted (keyed) — hmac: prefix, not a bare sha256 (defect #9)
    assert d["models.ustc_api_key"]["after"].startswith("hmac:")
    assert "sk-new" not in str(d)


def test_store_diff_unredacted_shows_value(store):
    d = store.diff({"models.default_model": "kimi"}, redact=False)
    assert d["models.default_model"] == {"before": "claude", "after": "kimi"}
