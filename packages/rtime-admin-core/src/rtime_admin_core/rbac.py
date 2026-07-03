# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J3 RBAC:两层正交权限模型(平台超管 / 项目角色),纯逻辑无 IO。

设计: docs/design/config-and-access-architecture-2026-07.zh-CN.md §一。行业依据(调研):
Sentry is_superuser vs org Owner、GitLab instance admin vs group Owner、Keycloak master
realm、Grafana Server vs Org Admin。

**两层正交(别混成一个 admin 字段)**:
- 平台层 ``is_platform_super``:管服务器/全实例/全量审计/签发 token/改 RBAC/删租户/改
  compose·profile·密钥。owner + 开发助手(AI)= 平台超级管理员。
- 项目层 ``project_roles[project] = role``:只在某 project 作用域内有效。预定义角色
  owner/admin/user/readonly-guest;外部被授权方默认 readonly-guest。

**超管独占能力白名单**:不可逆的破坏性能力(删数据/删 project/签发·吊销 token/改 RBAC
策略本身/读全量审计/改平台配置)硬留给 is_platform_super,普通 admin 再高也够不到
(Sentry Manager 挡在删组织之外、Home Assistant owner 独占的做法)。

**只增不减权限位**:提权=在目标 project 叠加单调、只增不减的额外能力位(GitLab custom
roles 只能加不能减,避免语义自相矛盾)。额外位**不能含超管独占能力**(委托护栏:防
"委托了管理即可自我提权"——Nextcloud 官方警告的经典坑)。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class Role(str, Enum):
    """项目层预定义角色(作用域=单个 project)。"""

    OWNER = "owner"
    ADMIN = "admin"
    USER = "user"
    READONLY_GUEST = "readonly-guest"


class Capability(str, Enum):
    """能力枚举。前缀 ``platform:`` 的是平台超管独占(全局,project 无关)。"""

    # --- 项目作用域能力 ---
    READ = "read"                       # 读该 project 的配置/库子集
    CONTRIBUTE = "contribute"           # 投稿(写=提议,走审核),外部方的"写"
    WRITE_CONFIG = "write_config"       # 改该 project 的热可调配置
    WRITE_DIRECT = "write_direct"       # 直接写库(annotate/edit),admin+
    MANAGE_MEMBERS = "manage_members"   # 管该 project 成员与其项目角色
    # --- 平台超管独占(不可逆/全局)---
    PLATFORM_DELETE_PROJECT = "platform:delete_project"
    PLATFORM_PURGE_DATA = "platform:purge_data"          # 物理删库内容
    PLATFORM_ISSUE_TOKEN = "platform:issue_token"        # 签发/吊销 token
    PLATFORM_CHANGE_RBAC = "platform:change_rbac"        # 改角色/权限策略本身
    PLATFORM_READ_ALL_AUDIT = "platform:read_all_audit"  # 读跨 project 全量审计
    PLATFORM_CHANGE_CONFIG = "platform:change_config"    # 改 compose/profile/密钥


#: 平台超管独占能力集合(仅 is_platform_super 拥有;不可经项目角色或额外位获得)。
SUPER_ONLY: frozenset[Capability] = frozenset(
    c for c in Capability if c.value.startswith("platform:")
)

#: 每个预定义角色在其 project 内授予的能力(单调分层:owner ⊇ admin ⊇ user ⊇ guest)。
_ROLE_CAPS: dict[Role, frozenset[Capability]] = {
    Role.READONLY_GUEST: frozenset({Capability.READ}),
    Role.USER: frozenset({Capability.READ, Capability.CONTRIBUTE}),
    Role.ADMIN: frozenset(
        {
            Capability.READ,
            Capability.CONTRIBUTE,
            Capability.WRITE_CONFIG,
            Capability.WRITE_DIRECT,
            Capability.MANAGE_MEMBERS,
        }
    ),
    Role.OWNER: frozenset(
        {
            Capability.READ,
            Capability.CONTRIBUTE,
            Capability.WRITE_CONFIG,
            Capability.WRITE_DIRECT,
            Capability.MANAGE_MEMBERS,
        }
    ),
}


def role_capabilities(role: Role | None) -> frozenset[Capability]:
    """某项目角色授予的能力(None=无角色=空)。恒不含 SUPER_ONLY。"""
    return _ROLE_CAPS.get(role, frozenset()) if role else frozenset()


class RbacError(ValueError):
    """RBAC 配置非法(如把超管独占能力当额外位授予=委托护栏拒绝)。"""


@dataclass(frozen=True)
class Principal:
    """一个主体(人或 AI/agent)的权限身份。

    ``is_platform_super``:平台超级管理员(owner + 开发助手)。
    ``project_roles``:{project: Role} 每 project 的预定义角色。
    ``extra_grants``:{project: {Capability}} 在角色之上叠加的额外能力位(只增;
       构造时校验不含 SUPER_ONLY —— 额外位永远不能是超管独占,否则=自我提权后门)。
    """

    id: str
    is_platform_super: bool = False
    project_roles: dict[str, Role] = field(default_factory=dict)
    extra_grants: dict[str, frozenset[Capability]] = field(default_factory=dict)

    def __post_init__(self) -> None:
        for project, caps in self.extra_grants.items():
            bad = set(caps) & SUPER_ONLY
            if bad:
                raise RbacError(
                    f"extra_grants[{project!r}] 含超管独占能力 {sorted(c.value for c in bad)};"
                    " 委托护栏:超管独占能力只能靠 is_platform_super,不可经额外位授予"
                )

    def project_role(self, project: str) -> Role | None:
        return self.project_roles.get(project)

    def capabilities_in(self, project: str) -> frozenset[Capability]:
        """该主体在某 project 的有效能力(角色 ∪ 额外位;不含 SUPER_ONLY)。"""
        return role_capabilities(self.project_role(project)) | self.extra_grants.get(
            project, frozenset()
        )


def can(principal: Principal, capability: Capability, *, project: str | None = None) -> bool:
    """授权判定:principal 能否在 project 上行使 capability。

    - SUPER_ONLY 能力(平台全局):仅 is_platform_super=True 放行,project 忽略。
    - 项目作用域能力:平台超管在任何 project 都放行;否则看该 project 的角色 ∪ 额外位。
    - project=None 且能力是项目作用域的 => 无作用域可判 => 拒(除非平台超管)。
    """
    if capability in SUPER_ONLY:
        return principal.is_platform_super
    if principal.is_platform_super:
        return True  # 超管在任何 project 都能行使项目作用域能力
    if project is None:
        return False  # 项目作用域能力必须指明 project
    return capability in principal.capabilities_in(project)


def require(principal: Principal, capability: Capability, *, project: str | None = None) -> None:
    """can 的抛异常版(给需要 fail-closed 的调用点)。"""
    if not can(principal, capability, project=project):
        where = f" on project {project!r}" if project else ""
        raise PermissionError(
            f"principal {principal.id!r} lacks capability {capability.value!r}{where}"
        )


def grant_extra(
    principal: Principal, project: str, capabilities: set[Capability]
) -> Principal:
    """在目标 project 叠加额外能力位,返回新 Principal(只增不减、不可含 SUPER_ONLY)。

    单调:结果是原额外位与新增的并集(GitLab custom roles 只能加不能减)。构造时
    __post_init__ 会拒绝 SUPER_ONLY(委托护栏)。原 Principal 不变(frozen)。
    """
    merged = dict(principal.extra_grants)
    merged[project] = frozenset(merged.get(project, frozenset()) | set(capabilities))
    return Principal(
        id=principal.id,
        is_platform_super=principal.is_platform_super,
        project_roles=dict(principal.project_roles),
        extra_grants=merged,
    )
