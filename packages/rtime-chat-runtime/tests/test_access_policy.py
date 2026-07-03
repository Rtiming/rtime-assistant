# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from rtime_chat_runtime.access_policy import is_allowed_actor


def test_access_policy_allows_dev_default_when_unconfigured():
    assert is_allowed_actor("user_001", "group_001", True, set(), set())


def test_access_policy_requires_allowed_group_when_users_configured():
    assert is_allowed_actor("allowed", "allowed", False, {"allowed"}, set())
    assert not is_allowed_actor("other", "other", False, {"allowed"}, set())
    assert not is_allowed_actor("allowed", "group_001", True, {"allowed"}, set())
    assert is_allowed_actor("allowed", "group_001", True, {"allowed"}, {"group_001"})
