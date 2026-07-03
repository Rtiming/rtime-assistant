# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Validation: reject illegal values, structured errors, dry-run purity."""

from __future__ import annotations

import pytest
from rtime_admin_core import ValidationError


def test_valid_change_passes(store):
    assert store.validate({"library-gateway.http_port": 9000}) == []


def test_out_of_range_int_rejected(store):
    errs = store.validate({"library-gateway.http_port": 999999})
    assert len(errs) == 1
    assert errs[0].path == "library-gateway.http_port"
    assert errs[0].type == "less_than_equal"


def test_too_small_int_rejected(store):
    errs = store.validate({"library-gateway.http_port": 0})
    assert errs and errs[0].path == "library-gateway.http_port"


def test_wrong_type_rejected(store):
    errs = store.validate({"library-gateway.http_port": "not-an-int"})
    assert errs and errs[0].path == "library-gateway.http_port"


def test_negative_timeout_rejected(store):
    errs = store.validate({"channel-common.reply_timeout_seconds": -1})
    assert errs and errs[0].path == "channel-common.reply_timeout_seconds"


def test_apply_rejects_invalid_and_leaves_store_unchanged(store):
    before = store.get("library-gateway.http_port")
    with pytest.raises(ValidationError) as ei:
        store.apply({"library-gateway.http_port": 999999}, ts="t", snapshot_id="s")
    assert len(ei.value.errors) == 1
    # nothing written
    assert store.get("library-gateway.http_port") == before
    # no snapshot created for a failed apply
    assert store.list_history() == []


def test_validate_is_dry_run(store):
    store.validate({"models.default_model": "would-be"})
    # dry-run must not mutate
    assert store.get("models.default_model") == "claude"


def test_validate_unknown_field_raises(store):
    # unknown *path* is a programming error surfaced as UnknownPathError
    with pytest.raises(Exception):
        store.validate({"models.ghost": 1})


def test_secret_value_not_echoed_in_error(store, registry):
    # force a validation error on a module that also has a secret; ensure any
    # secret input in the same module is masked, never echoed back.
    # ustc_api_key accepts str|None so give a valid secret + an invalid sibling.
    errs = store.validate(
        {
            "library-gateway.http_port": 999999,
            "models.ustc_api_key": "sk-should-not-leak",
        }
    )
    # the port error carries the port input, not the secret
    for e in errs:
        assert e.input != "sk-should-not-leak"
