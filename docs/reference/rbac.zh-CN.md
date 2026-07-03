# RBAC 权限模型(rtime_admin_core.rbac)使用参考

设计: [../design/config-and-access-architecture-2026-07.zh-CN.md](../design/config-and-access-architecture-2026-07.zh-CN.md) §一(J3)。
代码: `packages/rtime-admin-core/src/rtime_admin_core/rbac.py`(纯逻辑,无 IO,无依赖)。

## 两层正交(核心)

```python
from rtime_admin_core import Principal, Role, Capability, can

# owner + 开发助手 = 平台超级管理员
owner = Principal("owner", is_platform_super=True)
# 学生会负责人:只在 studentunion project 内是 admin,对平台全局默认无权
stu = Principal("stu-lead", project_roles={"studentunion": Role.ADMIN})

can(owner, Capability.PLATFORM_PURGE_DATA)                       # True(超管独占)
can(stu, Capability.PLATFORM_PURGE_DATA, project="studentunion") # False(角色再高也够不到)
can(stu, Capability.WRITE_DIRECT, project="studentunion")        # True(项目内 admin)
can(stu, Capability.WRITE_DIRECT, project="consumption")         # False(作用域外)
```

- **平台层** `is_platform_super`:后台/全量审计/签发token/改RBAC/删租户/改compose·profile·密钥。
- **项目层** `project_roles[project]=Role`:预定义 `OWNER/ADMIN/USER/READONLY_GUEST`,只在该
  project 内有效。同一主体可在不同 project 持不同角色。外部被授权方默认 `READONLY_GUEST`。

## 角色能力(project 内,单调分层)

| 角色 | 能力 |
|---|---|
| READONLY_GUEST | READ |
| USER | READ, CONTRIBUTE |
| ADMIN | READ, CONTRIBUTE, WRITE_CONFIG, WRITE_DIRECT, MANAGE_MEMBERS |
| OWNER | 同 ADMIN(平台级"删 project"仍是超管独占) |

## 超管独占能力(`SUPER_ONLY`,不可逆/全局)

`platform:` 前缀:`delete_project / purge_data / issue_token / change_rbac / read_all_audit /
change_config`。**仅 is_platform_super 拥有**,项目角色再高、额外位都给不了(委托护栏)。

## 只增权限位 + 委托护栏

```python
from rtime_admin_core import grant_extra
# 给外部 guest 在其 project 提权到可投稿(只增不减,原 principal 不变)
p2 = grant_extra(guest, "studentunion", {Capability.CONTRIBUTE})
```

- 额外位与角色能力取并集,单调只增(GitLab custom roles"只能加不能减")。
- **额外位不能含 SUPER_ONLY**:构造 Principal 或 grant_extra 时若含超管独占能力,抛
  `RbacError`——防"委托了管理即自我提权"(Nextcloud 官方警告的经典坑)。

## API

- `can(principal, capability, *, project=None) -> bool`:授权判定。SUPER_ONLY 只看
  is_platform_super(project 无关);项目能力超管恒 True、否则看该 project 角色∪额外位;
  项目能力未给 project 且非超管=拒。
- `require(...)`:can 的抛 `PermissionError` 版(fail-closed 调用点用)。
- `role_capabilities(role)`:角色授予的能力集(恒不含 SUPER_ONLY)。

## 与其它泳道

J3 是 J4(token 按能力 scoped)、J5(不可逆操作 plan→approve,超管独占门)、J6(库共享
grant:外部 guest + grant_extra 提权到 CONTRIBUTE)的授权地基。测试:
`packages/rtime-admin-core/tests/test_rbac.py`(两层正交/超管独占/作用域隔离/只增位/委托护栏/require)。
