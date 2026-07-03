# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J5 两段式 confirm-token 协议(config-and-access §3.3)。"""

from __future__ import annotations

import pytest

from rtime_admin_core.two_phase import (
    TOKEN_LEN,
    StaleTokenError,
    plan_token,
    require_token,
    verify_token,
)


def test_token_deterministic_and_length():
    t1 = plan_token("unset_secret", {"path": "models.ustc_api_key"}, "etag-abc")
    t2 = plan_token("unset_secret", {"path": "models.ustc_api_key"}, "etag-abc")
    assert t1 == t2 and len(t1) == TOKEN_LEN


def test_token_changes_with_op_payload_or_fingerprint():
    base = plan_token("op", {"p": 1}, "fp")
    assert base != plan_token("other", {"p": 1}, "fp")      # op 变
    assert base != plan_token("op", {"p": 2}, "fp")          # payload 变
    assert base != plan_token("op", {"p": 1}, "fp2")         # fingerprint 变(state 变)


def test_verify_matches_only_current_state():
    fp = "etag-1"
    tok = plan_token("unset", {"path": "x"}, fp)
    assert verify_token("unset", {"path": "x"}, fp, tok) is True
    # state 变了(fingerprint 变)=> 旧 token 失效
    assert verify_token("unset", {"path": "x"}, "etag-2", tok) is False
    # 伪造/空 token
    assert verify_token("unset", {"path": "x"}, fp, "bogus") is False
    assert verify_token("unset", {"path": "x"}, fp, "") is False


def test_require_token_raises_on_stale_or_missing():
    fp = "fp"
    tok = plan_token("op", {"a": 1}, fp)
    require_token("op", {"a": 1}, fp, tok)  # 不抛
    with pytest.raises(StaleTokenError):
        require_token("op", {"a": 1}, "fp-new", tok)  # 陈旧
    with pytest.raises(StaleTokenError):
        require_token("op", {"a": 1}, fp, None)  # 缺失
