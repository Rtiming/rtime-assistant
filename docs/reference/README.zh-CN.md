# 使用参考文档(docs/reference/)约定

本目录放"已合 main、可稳定使用的子系统"的使用/参考文档(reference),区别于
docs/design/ 的设计稿(为什么这么做)与 docs/tasks/ 的执行清单。

## 约定(所有 reference 文档必须遵守)

一、每个合入 main 的功能都要有一份 reference 文档,且在文首用一行链回其设计稿:
> 设计依据:docs/design/xxx.zh-CN.md。

这样"设计(为什么)"与"参考(怎么用)"始终成对,后续工作照此归位,不再散落。

二、准确到代码,不写未实现的能力。端点/字段/环境变量/命令一律以真实代码为准
(app.py/auth.py/schema.py/config.py 等),file:line 引用尽量给出。骨架/在建的
部分明确标注"在建",不当已建写。

三、命名与排版随主线规范:中文为主,技术名/命令/字段名按原样(env 名大写下划线、
命令行、JSON 键),不额外加中英文空格;标题编号与文字之间一个空格。

四、状态标注三档:已建(合 main 且接线活代码)、在建(骨架合 main、消费链未完)、
规划(仅设计稿)。文档里逐节可标,总览见 docs/README.zh-CN.md 的状态列。

## 现有 reference 文档

| 文档 | 覆盖子系统 | 设计依据 |
|---|---|---|
| admin-core.zh-CN.md | L1 管理核心库(ConfigStore/Registry/metadata) | design/mainline-profiles §六、config-full-coverage-plan |
| admin-api.zh-CN.md | L2 HTTP 控制 API + 面板骨架 | design/mainline-profiles §六 |
| profile-authoring.zh-CN.md | profile 机制(编写/编译/四层优先级/消费) | design/mainline-profiles §二 |
| config-coverage.zh-CN.md | 配置覆盖率守卫(doctor + 棘轮测试 + allowlist) | design/config-full-coverage-plan §三 |
| feishu-config.zh-CN.md | 飞书桥配置项(FeishuBridgeConfig 字段/密钥/生效/scope) | design/config-full-coverage-plan §二 批 1 |

新增 reference 文档时,在上表登记一行,并在 docs/README.zh-CN.md 的对应子系统行补上链接。
