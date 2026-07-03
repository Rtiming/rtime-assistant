# 库共享 grant(rtime_library_gateway.grants)使用参考

设计: [../design/config-and-access-architecture-2026-07.zh-CN.md](../design/config-and-access-architecture-2026-07.zh-CN.md) §五(J6);
[../design/library-sharing-multitenant-2026-07.zh-CN.md](../design/library-sharing-multitenant-2026-07.zh-CN.md)。
代码: `packages/rtime-library-gateway/src/rtime_library_gateway/grants.py`(纯 stdlib,无 IO)。

## 干什么

把"一次库共享"从散在配置里的**静态 policy** 升级为**一等 grant 对象**:可整体吊销、
可审计、有生命周期、读写位正交。网关 policy **由 grant 生成**,替代手写 8781
studentunion-policy.json(阶段0 迁入行为不变——已测等价)。

行业依据:AWS RAM resource share(可命名可整体吊销)、GitHub fine-grained PAT(默认无
访问、显式白名单、强制过期)、Postgres RLS(读 USING vs 写 WITH CHECK 正交)、Google
Drive Commenter(只读+可提建议=只读+投稿)。

## grant 对象

```python
from rtime_library_gateway.grants import Grant, GrantScope, grant_to_policy

g = Grant(
    grant_id="studentunion",
    subject="studentunion",                 # 被授方实例
    scopes=(GrantScope(prefix="knowledge/institutions/ustc", read=True, contribute=False),),
    granted_by="owner",
    granted_at="2026-07-02T00:00:00Z",
    expires_at=None,                         # owner 默认无限;write/contribute 建议按学期设
    # redact_sensitive/redact_student_pii/excluded_top_dirs/hide_excluded_in_results 默认对外收紧
)
policy = grant_to_policy(g)                   # → 网关 policy(allowed_path_prefixes + allow/deny)
```

- **scope = {前缀: 读/投稿位}**(读写正交):`read` 能读该子树;`contribute` 能往该子树
  投稿(写=提议进 _inbox,走 owner 审核;默认 False)。**绝不授予直接写**(annotate/finalize)。
- **生命周期**:`is_active(now_iso)` = status=active 且未过期;吊销=status→revoked;
  网关下次加载即断(`owner_audit_view` 给 owner"谁能碰我的库"清单,= GitHub view granted PATs)。
- **台账**:`load_ledger/dump_ledger`(JSONL,坏行跳过)。

## grant → policy 生成

`grant_to_policy(grant)` 产出与手写 scoped policy 同构:
- `allowed_path_prefixes` = 读前缀;`default_write=deny`;
- `clients.default.allow` = 只读方法(+ lib.contribute 当任一 scope 授予 contribute);
- `deny` = 写/危险方法(settings.*/finalize/course-intake/jobs.*/annotate;contribute 未授予时也 deny);
- redact_sensitive/redact_student_pii/excluded_top_dirs/hide_excluded 从 grant 带出。

**网关唯一入口不变**:grant 只决定 policy 内容,不新增旁路;被授实例仍以受限身份跑
(独立进程+本 policy),运行身份不旁路 scope(Postgres 表 owner 默认 BYPASSRLS 的坑)。

## 验证(严格)

- studentunion grant 生成的 policy 与手写 studentunion-policy.json 安全字段**等价**
  (allow 精确等、live_deny ⊆ gen_deny 至少一样严)。
- 生成的 policy 经 **gate.enforce 真机 PoC**:in-scope 读放行、越界读拒、直接写(annotate)拒。
- 测试:`tests/test_library_grants.py`(等价/读写正交/生命周期/台账/owner审计/gate集成)。

## 与其它泳道

J6 建在 J3 RBAC(外部方 = readonly-guest,grant_extra 提权到 contribute)之上;是
library-sharing-multitenant B 泳道的正式落地。后续:CLI 管理 grant 台账、network 侧
per-grant 实例编排(I-Q3)、admin-api 接入审核台(C3)、到期提醒。


## 台账管理 CLI(owner/平台超管)

```bash
python -m rtime_library_gateway.grants_cli list                 # owner审核视图:谁能碰我的库
python -m rtime_library_gateway.grants_cli show <grant_id>
python -m rtime_library_gateway.grants_cli add --grant-id G --subject S --prefix knowledge/institutions/ustc [--contribute] [--expires ISO]
python -m rtime_library_gateway.grants_cli revoke <grant_id>    # 吊销:下次网关加载即断
python -m rtime_library_gateway.grants_cli gen-policy <grant_id> [--out 8781-policy.json]
```

台账默认 `$STATE/rtime-library-gateway/grants.jsonl`(env `RTIME_LIBRARY_GRANTS_LEDGER` 覆盖),
原子写。CLI 由 owner 在 shell 直接跑(=平台超管);RBAC 语义上 add/revoke 属超管独占
(SUPER_ONLY)。`gen-policy` 产出的 JSON 可作为 scoped 网关(如 8781)的 policy 文件——
把手写 studentunion-policy.json 迁成 grant 生成。web 接入审核台(C3)后续消费同一台账。
测试:tests/test_library_grants_cli.py。
