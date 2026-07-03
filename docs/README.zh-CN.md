# 文档索引(docs/)

rtime-assistant 全部文档的单一入口。想找任何一份文档,先从这里定位。分四组:
架构与设计(design/)、使用参考(reference/)、运维(deploy/)、审计与计划(audit/、
development-plan)。文档层次约定:design=为什么这么做(设计稿),reference=怎么用
(已建功能的使用/参考),tasks=执行清单,development-log=当时的开发记录。

状态列:已建=合 main 且接线活代码;在建=骨架合 main、消费链/功能未完;规划=仅设计稿。

## 一、P3 管理面 + Profile 机制(本轮consolidate重点)

一份设计稿配一份 reference,后续工作照此归位(reference 目录约定见
reference/README.zh-CN.md)。

| 子系统 | 设计文档 | 使用/参考文档 | 代码位置 | 状态 |
|---|---|---|---|---|
| L1 管理核心库(ConfigStore/Registry/元数据) | design/mainline-profiles §六 | reference/modules.zh-CN.md | 模块清单manifest(deploy/modules.json):字段/doctor校验/三层配置定位/加模块步骤(K1) |
| reference/rbac.zh-CN.md | RBAC两层正交(平台超管is_platform_super/项目角色owner·admin·user·readonly-guest)+超管独占能力+只增权限位+委托护栏(J3) |
| reference/admin-core.zh-CN.md | packages/rtime-admin-core/、packages/rtime-config/ | 已建 |
| L2 HTTP 控制 API + 面板骨架 | design/mainline-profiles §六 | reference/admin-api.zh-CN.md | packages/rtime-admin-api/ | 已建(schemathesis契约与全功能面板在建) |
| profile 机制(编写/编译/四层优先级/消费) | design/mainline-profiles §二 | reference/profile-authoring.zh-CN.md | packages/rtime-config/.../profile/、profiles/、apps/qq-bridge/qq_bridge/config.py、apps/web-chat/web_chat/profiles.py | 已建(T1机制+T2消费;QQ与web已接线,飞书默认未启用) |
| 配置全覆盖 + 覆盖率守卫 | design/config-full-coverage-plan | reference/config-coverage.zh-CN.md | packages/rtime-admin-core/.../coverage_doctor.py、coverage_allowlist.py | 已建(批0守卫;各模块收编批次在建) |
| 库共享 / 多租户 | design/library-sharing-multitenant | (随实现补) | (现网学生会子集共享已跑) | 规划(基线已在产) |
| P5 brain 静态加密 + 网关唯一入口 | design/p5-brain-encryption-gateway-only、design/p5-impl-scoped-encryption | (随实现补) | lib 网关(gate.py/dispatch.py/mcp_server.py) | 规划(owner已决方案,施工未启) |

## 二、架构与设计(design/ 及总纲)

| 文档 | 内容 |
|---|---|
| development-plan.zh-CN.md | 开发总纲(单一真相):愿景/主序列 P0-P5/融合点/各阶段方案与状态 |
| design/roadmap-next-phase-2026-07.zh-CN.md | 下一阶段执行规划:八泳道(答疑优化闭环/库共享/开源/加密staging/覆盖收尾/RAG/运维/库维护)+排期+owner拍板清单 |
| design/open-source-architecture-2026-07.zh-CN.md | 开源架构:AGPL-3.0落地、边界白名单、三类使用者用法、保守S0-S6发布流程(每段可停) |
| design/library-lifecycle-maintenance-2026-07.zh-CN.md | 库生命周期与维护:业界参考、内容合同、编辑动词两段式、修订链、doctor六维巡检、蓝绿重索引、M0-M6切分 |
| design/library-query-service-2026-07.zh-CN.md | 库查询服务定式化:四传输壳现状盘点、一核心多壳架构、兼容合同(保开发MCP路径)、Q1-Q4计划 |
| design/a3-studentunion-usage-findings-2026-07.zh-CN.md | A3校会实测分析:101次@bot需求分布+三硬结论(安全门守住/PII输出险情/太啰嗦)+待owner决策+迭代建议 |
| design/two-phase-irreversible-2026-07.zh-CN.md | 不可逆操作两段式协议标准(plan→approve→apply)+可复用two_phase helper+采纳清单(J5,给多开发者定标准) |
| design/module-system-and-open-source-2026-07.zh-CN.md | 模块系统+开源模块化:模块分类/manifest单一真相/三层配置(装机选装+面板细配+CLI-API)/OB·notes·同步通用可选模块/开源打包/K泳道(充分模块化) |
| design/config-and-access-architecture-2026-07.zh-CN.md | 配置与访问架构:RBAC两层正交(平台超管/项目角色)+多管理员+配置入口全覆盖+人和AI双入口+开源setup/边界+库共享grant一等对象(泳道J,5维度行业调研) |
| design/admin-console-ops-2026-07.zh-CN.md | 运营面板三台(Profile运营台/库管理台/接入审核台)+权限模型确认(owner与开发助手同为超管)+外部MCP接入grant审核流 |
| specs/README.zh-CN.md | 实施规格目录:执行契约+当前规格清单(A1.5/A2/H-M1/I-Q2),给任何执行助手零偏离照做 |
| open-source-update-goals.zh-CN.md | 开源准备目的、更新软件目标、owner/学生会/下游实例边界、v0.1 基线清单 |
| design/chat-archive-storage-2026-07.zh-CN.md | 聊天全量归档:raw/normalized/media/index 四层存储合同+分类字典+doctor+验收 |
| design/mainline-profiles-and-entries-2026-07.zh-CN.md | 主线 profile 机制 + 双入口(问答/控制)设计,T0-T8 实施切分 |
| design/config-full-coverage-plan-2026-07.zh-CN.md | 配置全覆盖:逐模块注册计划 + 覆盖率守卫 + 分批顺序 |
| design/library-sharing-multitenant-2026-07.zh-CN.md | 库共享与多租户(OWNER/GRANTEE 权限、多 agent、多人多项目) |
| design/p5-brain-encryption-gateway-only-2026-07.zh-CN.md | P5 威胁模型 + 加密/网关唯一入口选型(owner 定夺基线) |
| design/p5-impl-scoped-encryption-2026-07.zh-CN.md | P5 实现架构(app 层信封加密 + 网关唯一入口,分阶段施工路线) |
| platform-modularization-plan.zh-CN.md、platform-modular-scenarios.zh-CN.md | P2 产品化/模块化方案与场景 |
| channel-unification-plan.zh-CN.md | 渠道并轨(飞书/QQ 共享核心)方案 |
| architecture.md、component-deep-dive.md、overview.md、project-map.zh-CN.md | 总体架构 / 组件深潜 / 概览 / 仓库地图 |
| maintainability-standards.zh-CN.md、refactor-roadmap.md、uv-workspace.zh-CN.md | 可维护性标准 / 重构路线 / uv workspace |
| prompt-layering.md、model-providers.zh-CN.md、memory-loop.zh-CN.md、context-orchestrator.md、context-unlocking.md | 提示词分层 / 模型 provider / 记忆环 / 上下文编排与解锁 |
| vision-and-landscape.zh-CN.md、bridge-requirements.md | 愿景与业界对照 / 桥需求 |

## 三、使用参考(reference/ 及子系统使用文档)

| 文档 | 内容 |
|---|---|
| reference/README.zh-CN.md | reference 目录约定(每功能一份 reference + 链回设计) |
| reference/modules.zh-CN.md | 模块清单manifest(deploy/modules.json):字段/doctor校验/三层配置定位/加模块步骤(K1) |
| reference/rbac.zh-CN.md | RBAC两层正交(平台超管is_platform_super/项目角色owner·admin·user·readonly-guest)+超管独占能力+只增权限位+委托护栏(J3) |
| reference/admin-core.zh-CN.md | ConfigStore API + Registry + x-secret/x-reload/x-scope(开发者加模块) |
| reference/admin-api.zh-CN.md | /v1 全端点 + scope 模型 + ETag/If-Match + 运行服务 + 安全姿态 |
| reference/profile-authoring.zh-CN.md | profile.yaml schema + extends + 编译投影表 + 四层优先级 + 消费 |
| reference/config-coverage.zh-CN.md | 覆盖率 doctor + 棘轮测试 + allowlist 增减 |
| reference/models-config.zh-CN.md、config/models.md | models 配置模块(路由/选型 env 字段 + provider 密钥 + 哪些是 registry 数据) |
| reference/feishu-config.zh-CN.md、config/feishu.md | 飞书桥配置项(FeishuBridgeConfig 字段/密钥/生效/scope) |
| reference/assistant-gateway-config.zh-CN.md、config/assistant-gateway.md | assistant-gateway(Obsidian 后端网关)配置项(AssistantGatewayConfig 字段/生效/scope,收编+注册) |
| reference/qq-selfheal-config.zh-CN.md、config/qq-selfheal.md | QQ 掉线自愈守护配置项(QQSelfhealConfig;守护 stdlib-only,schema 独立镜像) |
| reference/ustc-kb-config.zh-CN.md、config/ustc-kb.md | ustc-kb 抓取器配置项(UstcKbConfig:DATA/WORKERS/TODAY) |
| reference/chat-runtime-config.zh-CN.md、config/chat-runtime.md | chat-runtime 配置项(ChatRuntimeConfig:RTIME_CAMPUS_URLS_FILE,归 admin-core) |
| reference/library-gateway.zh-CN.md | 库网关 scope 强制模型(index-reject/纵深结果过滤/lib.get 收敛)+ 收编配置字段 + prewarm 漂移修 |
| reference/admin-notify.zh-CN.md | 管理员上报工具:通道无关分发(飞书webhook/webhook/邮件/QQ扩展点)+模型自主escalation+只读allowlist接线(A3决策3) |
| reference/qa-eval.zh-CN.md | A4答疑评测集+打分器:直答覆盖回归+捕获回答前后对照(改prompt/检索/模型量化改善) |
| reference/chat-transcript.zh-CN.md | normalized transcript归一层:CLI用法+内容寻址幂等+出站捕获+隐私口径(A2) |
| reference/library-grants.zh-CN.md | 库共享grant一等对象:{前缀:读/投稿位}正交+生命周期+吊销+由grant生成网关policy(替代手写8781)+owner审计(J6) |
| reference/library-contract.zh-CN.md | 库内容合同(status/review_after/superseded_by/version/source)+ contract 夜巡 CLI + drift 检查(H M0) |
| qq-bridge-development.zh-CN.md、wechat-bridge-development.zh-CN.md、config/qq-bridge.md | QQ/微信桥开发与配置 |
| rtime-library-gateway.md、brain-library-*.md、brain-*.md | 库网关与 brain 库各模块(检索/索引/docpack/citation/visualmd 等) |
| obsidian-assistant-plugin.md、obsidian-*.md | Obsidian 插件与 vault 布局 |
| entrypoints.zh-CN.md、model-providers.zh-CN.md、latency-and-local-model.zh-CN.md | 入口一览 / 模型 provider / 延迟与本地模型 |
| logging-and-audit.md、review-console.md、automation-and-reminders.md | 日志审计 / 复核台 / 自动化与提醒 |

## 四、运维(deploy/ 及运维文档)

| 文档 | 内容 |
|---|---|
| deploy/PROFILE-CUTOVER.md | profile 切换清单:orangepi docker.env 清理 + 部署验收 + 回滚 |
| deployment.md、docker-production.md、docker-workflow.md | 部署 / 生产 Compose / Docker 工作流 |
| instance-deploy.zh-CN.md、runbook.md、troubleshooting.md | 实例部署 / 运行手册 / 排障 |
| ci-server-gate.zh-CN.md | orangepi 裸仓库 post-receive 咨询式 CI 门(快门 + 慢门 pytest) |
| tooling-installation.md、tooling-packaging.md、runtime-assets.md | 工具安装 / 打包 / 运行时资产 |
| development-workflow.md | 开发流程规范(单一主干 main / type-topic 分支 / 提交前校验门) |

## 五、审计与计划(audit/、research/、tasks/)

| 文档 | 内容 |
|---|---|
| audit/codebase-audit-2026-07.zh-CN.md | 代码库摸底:命名一致性 + ~220 配置点 + MCP 对接现状(P2/P3 输入) |
| research/rag-memory-survey-2026-07.zh-CN.md | RAG/记忆调研(P4 采纳清单依据) |
| tasks/PROGRESS.md、tasks/README.md、tasks/EXECUTE.md | 入库链路任务台账 / 说明 / 执行 |
| tasks/pipeline/M0-M9 及 prompts/ | brain 入库流水线各阶段与提示词 |
| development-log-*.md | 逐日开发记录(历史归档,非当前状态源) |

## 六、找不到时

- 想知道"某功能怎么用" → 先看第三组 reference/;没有就是尚未有 reference,查对应 design。
- 想知道"为什么这么设计" → 第二组 design/。
- 想部署/排障 → 第四组 deploy/。
- 想知道"现在做到哪了" → development-plan.zh-CN.md 的主序列表 + §五(P3 状态)。
