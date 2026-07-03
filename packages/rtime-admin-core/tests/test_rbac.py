# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J3 RBAC 两层正交模型不变量(config-and-access §一)。"""

from __future__ import annotations

import pytest

from rtime_admin_core.rbac import (
    Capability,
    Principal,
    RbacError,
    Role,
    can,
    grant_extra,
    require,
    role_capabilities,
)

SU = Principal("owner", is_platform_super=True)
STU_ADMIN = Principal("stu-lead", project_roles={"studentunion": Role.ADMIN})
STU_GUEST = Principal("outsider", project_roles={"studentunion": Role.READONLY_GUEST})


# --- 平台超管 vs 项目角色 两层正交 -------------------------------------------
def test_platform_super_can_everything_including_super_only():
    for cap in Capability:
        assert can(SU, cap, project="studentunion") is True
    # SUPER_ONLY 也放行,且 project 无关
    assert can(SU, Capability.PLATFORM_PURGE_DATA) is True


def test_non_super_never_gets_super_only_regardless_of_role():
    # 项目 admin(甚至 owner)也够不到平台超管独占能力
    stu_owner = Principal("po", project_roles={"studentunion": Role.OWNER})
    for p in (STU_ADMIN, stu_owner, STU_GUEST):
        assert can(p, Capability.PLATFORM_PURGE_DATA, project="studentunion") is False
        assert can(p, Capability.PLATFORM_ISSUE_TOKEN) is False
        assert can(p, Capability.PLATFORM_DELETE_PROJECT, project="studentunion") is False


def test_project_role_scoped_to_that_project_only():
    # admin 于 studentunion,对别的 project 无任何能力
    assert can(STU_ADMIN, Capability.WRITE_DIRECT, project="studentunion") is True
    assert can(STU_ADMIN, Capability.WRITE_DIRECT, project="consumption") is False
    assert can(STU_ADMIN, Capability.READ, project="consumption") is False


def test_same_principal_different_roles_per_project():
    p = Principal(
        "multi",
        project_roles={"studentunion": Role.ADMIN, "consumption": Role.READONLY_GUEST},
    )
    assert can(p, Capability.WRITE_DIRECT, project="studentunion") is True
    assert can(p, Capability.WRITE_DIRECT, project="consumption") is False
    assert can(p, Capability.READ, project="consumption") is True


def test_readonly_guest_is_read_only():
    caps = role_capabilities(Role.READONLY_GUEST)
    assert caps == frozenset({Capability.READ})
    assert can(STU_GUEST, Capability.READ, project="studentunion") is True
    assert can(STU_GUEST, Capability.CONTRIBUTE, project="studentunion") is False
    assert can(STU_GUEST, Capability.WRITE_DIRECT, project="studentunion") is False


def test_role_hierarchy_monotone():
    guest = role_capabilities(Role.READONLY_GUEST)
    user = role_capabilities(Role.USER)
    admin = role_capabilities(Role.ADMIN)
    assert guest < user < admin  # 严格包含,单调分层


# --- 只增权限位 + 委托护栏 ---------------------------------------------------
def test_grant_extra_is_additive_and_monotone():
    # 给外部 guest 在其 project 叠加 contribute(提权到可投稿)
    p2 = grant_extra(STU_GUEST, "studentunion", {Capability.CONTRIBUTE})
    assert can(p2, Capability.CONTRIBUTE, project="studentunion") is True
    assert can(p2, Capability.READ, project="studentunion") is True  # 原有的仍在
    # 原 principal 不被修改(frozen)
    assert can(STU_GUEST, Capability.CONTRIBUTE, project="studentunion") is False
    # 再叠加不丢已有(并集)
    p3 = grant_extra(p2, "studentunion", {Capability.WRITE_CONFIG})
    assert can(p3, Capability.CONTRIBUTE, project="studentunion") is True
    assert can(p3, Capability.WRITE_CONFIG, project="studentunion") is True


def test_extra_grant_cannot_include_super_only_delegation_guard():
    # 委托护栏:超管独占能力不能当额外位授予(否则=自我提权后门)
    with pytest.raises(RbacError):
        Principal(
            "sneaky",
            project_roles={"studentunion": Role.ADMIN},
            extra_grants={"studentunion": frozenset({Capability.PLATFORM_CHANGE_RBAC})},
        )
    with pytest.raises(RbacError):
        grant_extra(STU_ADMIN, "studentunion", {Capability.PLATFORM_ISSUE_TOKEN})


# --- project=None 语义 + require -----------------------------------------------
def test_project_scoped_capability_needs_project():
    # 不给 project 的项目作用域能力,非超管一律拒
    assert can(STU_ADMIN, Capability.READ, project=None) is False
    # 超管不受影响
    assert can(SU, Capability.READ, project=None) is True


def test_require_raises_on_deny():
    require(SU, Capability.PLATFORM_PURGE_DATA)  # 不抛
    with pytest.raises(PermissionError):
        require(STU_GUEST, Capability.WRITE_DIRECT, project="studentunion")
    with pytest.raises(PermissionError):
        require(STU_ADMIN, Capability.PLATFORM_ISSUE_TOKEN)


def test_super_only_set_matches_prefix():
    from rtime_admin_core.rbac import SUPER_ONLY

    assert all(c.value.startswith("platform:") for c in SUPER_ONLY)
    assert Capability.READ not in SUPER_ONLY and Capability.WRITE_DIRECT not in SUPER_ONLY
