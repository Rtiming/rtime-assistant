# 管理面板演进计划:从"配置字段编辑器"到"操作员控制台"(2026-07-04)

状态:**规划,未开发**(owner 2026-07-04 反馈:现面板太底层、看不懂在干啥;要的是切QQ号、
给QQ加模块这类**操作员一键动作**)。本文记录方向,交后续开发者或以后落地。

现状代码:`packages/rtime-admin-api`(FastAPI + 静态面板 `static/{index.html,panel.js}`),
只绑 127.0.0.1。现有五页:配置树 / 编辑·diff·提交 / 历史·快照·回滚 / 审计·漂移 / 模块。

## 〇、先厘清:能改 ≠ 好改(2026-07-04 owner 追问后核实)
owner 早有明确需求:**人员在面板上轻轻松松管理提示词、群聊等配置**。核实结论:
- **字段层"能改"已建**:qq 模块 schema 有 44 个可编辑字段,含 `qq.system_prompt`
  (提示词)、`qq.public_groups`/`qq.group_allowlist`/`qq.admin_ids`/`qq.allowed_users`
  (群聊/名单)、`qq.direct_rules_path`(直答规则)——面板配置树/编辑表单本来就能改它们
  (前提:面板进程注册了 qq 模块,即 qq-bridge 可导入 / build_store include_qq)。
- **但"好改"没做**:现在是逐字段改一个叫 `qq.system_prompt` 的框,不是"编辑提示词"
  的友好界面;而且默认面板实例常没接 qq 模块 → 字段根本不显示,更像"没有"。
- **结论**:不是没执行(字段层执行了),也不是没计划(计划有面板),而是**友好体验层
  缺席 + 本 roadmap 之前没把"提示词/群聊/名单友好管理"列成头等项**。本次补上(见 §二.0)。

## 一、问题(owner 原话)
"我都不知道这个面板是干啥的"、"方便人员维护提示词包括群聊等配置轻轻松松管理"——
面板现在是**schema 字段编辑器**(改 store 层配置值),门槛高、不直观。operator 真正想做的
是**高层任务**,不是逐字段改:
- **编辑提示词/群聊/名单**(把 qq.system_prompt 等呈现成"提示词编辑器"而非裸字段)
- QQ **切换登录号码**(换个小号 / 重新扫码登录)
- QQ **加/开关模块**(直答规则、掉线自愈、媒体、群权限等子能力)
- 其它渠道类似(飞书换应用、web 开关等)

## 二、方向:配置面板之上加一层"操作台"
保留现有配置面(底层真相 + 安全写路径不变),在其上加**面向任务的快捷动作**——每个动作
= 一次或多次底层配置写/服务操作的封装,但呈现成 operator 看得懂的按钮。

### 应支持的快捷动作(按渠道/主题分组)
| 分组 | 快捷动作 | 底层映射 |
|---|---|---|
| **提示词/群聊/名单(头等,owner 点名)** | "编辑提示词"友好视图(大文本框+预览+版本对比,不是裸 `qq.system_prompt` 字段) | 改 `qq.system_prompt`(字段已存在,配置写路径已通) |
| | 管群:加/删公开答疑群、白名单群 | 改 `qq.public_groups`/`qq.group_allowlist`(已存在) |
| | 管名单:admin/普通用户/黑名单分级 | 改 `qq.admin_ids`/`qq.allowed_users`/`qq.blocked_users`(已存在) |
| **QQ 号** | 切换登录号码 | 改 `qq.account`(QQ_ACCOUNT)+ 触发 NapCat 重登;经 selfheal 补码链路发新二维码到飞书 |
| | 重新扫码登录 / 看当前二维码 | 触发 selfheal on-demand QR(现有 qq-qr-request 机制) |
| | 看在线状态 | get_friend_list 功能验真(现有 selfheal 判定) |
| **QQ 模块** | 开/关直答规则 | 改 `qq.direct_rules` 指向规则文件 |
| | 开/关掉线自愈 | 启停 qq-selfheal.service(integration-qq-selfheal 模块) |
| | 开/关媒体/群权限/分级准入 | 改 qq.* 对应字段(现有 schema) |
| **模块(通用)** | 装/卸一个可选模块 | 改 COMPOSE_PROFILES + compose up/down(K4 向导的运行时版) |
| | 开/关一个模块的子能力 | 改对应 config_module 字段 |
| **模型** | 切换默认模型 | 改 `models.default_model`(已有,K2);面板选 provider |
| **服务** | 重启/重载某渠道 | profile reload(已有 `POST /v1/profiles/{id}:reload`)或容器重启 |

### 设计原则(与现有架构一致)
1. **快捷动作 = 底层写路径的封装**,不开第二条写口——仍走 PATCH /v1/config(ETag/x-scope/
   审计/两段式),或已有的专用端点(profile reload、modules)。审计/回滚不丢。
2. **危险动作两段式**:切号、卸模块等不可逆/影响大的,plan→确认→apply(复用 two_phase)。
3. **需要 host 侧操作的**(启停 systemd、compose up/down、docker restart NapCat)要一个
   **受信 executor**——面板 API 不直接跑 docker/systemctl(它只绑 127.0.0.1 但仍应最小权限);
   参考 selfheal 的"面板写触发文件 → host 守护执行"解耦纹理(qq-qr-request 就是这模式)。
4. **operator 视角优先**:先给"我要干的事"的按钮,底层字段作为"高级"折叠区。

## 三、落地分期(建议)
- **P1(优先)友好编辑视图**:提示词/群聊/名单——把已存在的 qq.* 字段包装成 operator
  看得懂的编辑器(提示词大文本框+预览、群/名单增删列表)。**底层写路径已通,只差 UI 封装**,
  投入最小、owner 最想要,应先做。同时确保部署的面板实例注册 qq 模块(否则字段不显示)。
- P1 只读状态台:一页汇总各渠道在线/离线、装了哪些模块、当前模型、最近审计。
- P2 低危动作:切默认模型、开关子能力(直答/媒体等,纯配置写,已有端点)。
- P3 host 动作:切 QQ 号 / 重扫码 / 启停自愈 / 装卸模块——需受信 executor + 两段式。
- P4 多渠道对齐 + 权限(哪些 operator 能做哪些动作,复用 RBAC)。

## 四、非目标
- 不替代配置树(底层真相保留);操作台是它的**友好封装层**。
- 不在面板进程里直接跑特权命令(host 动作走受信 executor 解耦)。

## 相关
- 现有模块总览:K5(`GET /v1/modules` + 面板"模块"页),docs/reference/admin-api.zh-CN.md。
- QQ 切号/重登/补码链路:apps/qq-bridge/ops/qq_selfheal.py(qq-qr-request 触发文件机制)。
- 装机向导(装卸模块的批处理版):deploy/setup-wizard.py(K4)。
- 两段式写:packages/rtime-admin-core/two_phase.py。
