# 架构总览(新贡献者先读这份)

一页看懂 rtime-assistant:它是什么、有哪些部件、怎么协作、边界在哪。细节文档在
docs/README.zh-CN.md 索引;本文给整体心智模型。

## 一、一句话
一套自托管 AI 助手:**知识库(brain)+ 唯一入口网关 + 可插拔渠道 + 管理面板**。
用户从任意渠道(QQ/飞书/网页)提问,助手经网关检索知识库、调模型、答复。所有对库的
访问都过网关审核;所有可选部件都能不装。

## 二、分层(数据流从上到下)
```
  用户 ──> 渠道入口(apps/)         QQ桥 / 飞书桥 / 网页 / Obsidian插件
            │  每个渠道热插拔、可不装
            ▼
       聊天运行时(rtime-chat-runtime) 渠道无关原语:会话/工具策略/渲染/run-log
            │
            ▼
        模型(rtime-models + 包裹脚本)  kimi/科大DS/ollama/deepseek/qwen/Anthropic
            │   provider 目录=数据文件,选默认/增删走面板
            ▼
   ┌─────────────────────────────────────────┐
   │  库网关 rtime-library-gateway(唯一入口)  │  lib.* 方法,权限门+审计+脱敏
   │   READ:search/read/tree/stat/...          │  scoped 实例只读子集
   │   WRITE(两段式):annotate/edit/move/retire │  超管专用,plan→token→apply
   │   投稿:contribute→_inbox→owner finalize   │  外部只投稿不直写
   └─────────────────────────────────────────┘
            │
            ▼
      知识库 brain(仓库外)             brain-library 检索/索引底座(SQLite+向量)
```
旁挂:**管理面板 + API**(rtime-admin-api/core)——配置树/校验/快照回滚/审计/RBAC/
模块总览,只绑 127.0.0.1。**配置**四层优先级 env > store > profile > default。

## 三、包职责(packages/)
| 包 | 干什么 |
|---|---|
| rtime-config | 配置 schema 地基(pydantic-settings,字段元数据:secret/reload/scope) |
| rtime-admin-core | 配置读写/校验/快照/回滚/审计/RBAC/模块清单(纯 Python) |
| rtime-admin-api | 管理面 HTTP API(FastAPI 薄层,ETag 并发/scoped token/默认脱敏) |
| rtime-library-gateway | **库唯一入口**:方法分发 + 权限门(gate)+ grant 生成 policy |
| brain-library | 检索/索引底座 + 库写动词(annotate/edit/maintain,两段式+修订链) |
| rtime-models | 模型目录单一真相(model-registry.json)+ 路由 |
| rtime-chat-runtime | 渠道无关桥运行时原语(会话/工具策略/输出渲染/校园URL) |
| brain-citation / docpack / visualmd | 引文诊断 / DocPack 工具链 / 视觉转写(可选扩展) |
| rtime-jobs / automation / context / profile / review | 任务队列 / 诊断只读工具组 |

## 四、渠道入口(apps/,全部可选)
qq-bridge(NapCat OneBot)、feishu-bridge、web-chat、assistant-gateway(Obsidian 用)、
obsidian-rtime-assistant(插件)、reminder-sender。**每个都是"薄入口适配器"**——不是
检索引擎也不是记忆库,只把用户消息接进共享运行时。都能不装(compose profile 门控)。

## 五、三条不可违背的原则
1. **网关是唯一入口**:绝不绕过网关直接读/写 brain 文件系统。权限、审计、脱敏都在网关。
2. **数据与代码分离**:代码开源;brain 数据/密钥/运行状态永不进仓库。默认值用占位,
   真实值由部署方 env 覆盖。
3. **模块化 opt-out**:一切可选部件声明在 deploy/modules.json,不想要的完全不装。

## 六、权限模型(谁能碰库)
- **超管**(owner/开发助手实例):库全权,含两段式写动词(annotate/edit/move/retire)。
- **被共享方**(如学生会实例):独立进程 + scoped policy,只读授权子树 + 投稿(不直写);
  写动词恒被拒(fail-closed)。共享=一个可整体吊销的 grant 对象(有效期+审计)。
详见 docs/design/config-and-access-architecture、reference/library-grants。

## 七、新人第一天(quickstart)
1. `uv sync --all-packages --extra test` → `uv run --all-packages --extra test python -m pytest tests/ packages/ -q`(先跑通全绿)。
2. 读 CONTRIBUTING.md(流程)+ 本文(架构)+ docs/reference/modules.zh-CN.md(模块化)。
3. 挑个小口切入:改一个配置字段(admin-core schema)、加一条直答规则、或补一个模块教程。
4. 想跑起来:setup-wizard 选模块 → 补 .env → docker compose up(见 CONTRIBUTING §二)。

## 八、部署形态(生产怎么跑)
运行时 = 一台常在线机器(如树莓派/小主机):网关服务 + 选装的渠道容器(compose profile)。
配置在实例目录(.env + compose.override.yml + data/ + state/,setup-wizard 产出)。
升级/回滚走 deploy/update.sh(tag 发布 + 迁移 + 健康检查 + 自动回滚)。
