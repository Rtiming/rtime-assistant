# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Profile doctor — the 3-way library-scope cross-check (design §2.7, part G).

Asserts the studentunion profile's ``library.scope`` agrees with (a) its committed
``library-policy.json`` allowed_path_prefixes and (b) the compose read-only
sub-mount subtree under /mnt/brain. A drift in any one turns this red.
"""

from __future__ import annotations

import json
from pathlib import Path

import yaml
from rtime_config.profile import (
    check_profile_policy_file,
    cross_check_scope,
    mount_target_to_scope,
)

_REPO = Path(__file__).resolve().parents[3]
_SU = _REPO / "profiles" / "studentunion"

# The documented live read-only sub-mount for studentunion — see compose.prod.yml
# / the instance override: source ${BRAIN_ROOT}/knowledge/institutions/ustc mounted
# read-only at /mnt/brain/knowledge/institutions/ustc.
_STUDENTUNION_MOUNT_TARGETS = ["/mnt/brain/knowledge/institutions/ustc"]


def _profile_scope(profile_dir: Path) -> list[str]:
    doc = yaml.safe_load((profile_dir / "profile.yaml").read_text(encoding="utf-8"))
    return list((doc.get("library") or {}).get("scope") or [])


def test_mount_target_normalization():
    assert (
        mount_target_to_scope("/mnt/brain/knowledge/institutions/ustc")
        == "knowledge/institutions/ustc"
    )
    assert mount_target_to_scope("/mnt/brain") == ""  # whole-library mount
    assert mount_target_to_scope("/mnt/brain/knowledge/") == "knowledge"


def test_studentunion_three_way_scope_agrees():
    scope = _profile_scope(_SU)
    assert scope == ["knowledge/institutions/ustc"]

    policy = json.loads((_SU / "library-policy.json").read_text(encoding="utf-8"))
    mount_subtrees = [mount_target_to_scope(t) for t in _STUDENTUNION_MOUNT_TARGETS]

    result = cross_check_scope(
        profile_scope=scope,
        policy_allowed_prefixes=policy["allowed_path_prefixes"],
        mount_subtrees=mount_subtrees,
    )
    assert result["ok"], result["risks"]


def test_committed_policy_matches_profile_scope():
    scope = _profile_scope(_SU)
    result = check_profile_policy_file(scope, _SU / "library-policy.json")
    assert result["ok"], result["risks"]


def test_cross_check_flags_drift():
    bad = cross_check_scope(
        profile_scope=["knowledge/institutions/ustc"],
        policy_allowed_prefixes=["knowledge/institutions/ustc", "knowledge/extra"],
        mount_subtrees=["knowledge/institutions/ustc"],
    )
    assert not bad["ok"]
    assert "profile_scope_ne_policy_prefixes" in bad["risks"]


def test_check_policy_file_flags_hand_edit(tmp_path):
    (tmp_path / "library-policy.json").write_text(
        json.dumps({"allowed_path_prefixes": ["knowledge/x"]}), encoding="utf-8"
    )
    result = check_profile_policy_file(
        ["knowledge/institutions/ustc"], tmp_path / "library-policy.json"
    )
    assert not result["ok"]
    assert "policy_file_differs_from_profile_scope" in result["risks"]
