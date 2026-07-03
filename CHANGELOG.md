# Changelog

本文件记录 rtime-assistant 面向实例部署的可见变更，格式遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，版本号遵循语义化版本（发布通道 = main 上的附注 tag `vX.Y.Z`）。

条目约定（`deploy/update.sh check` 依赖以下前缀做风险判定）：

- `BREAKING: ` 开头的行 = 破坏性变更（配置格式/接口/数据布局不兼容）。目标区间任一版本节含此前缀时，`check` 退出码为 20，`apply` 默认拒绝、需 `--yes`。
- `MIGRATION: NNN_描述.sh` 开头的行 = 该版本携带 deploy/migrations/ 迁移脚本，升级时会在实例上执行一次。

## [Unreleased]

## [0.1.0] - 2026-07-04

首个开源版本(AGPL-3.0)。自托管个人/组织 AI 助手运行时:可检索知识库(brain)+ 唯一入口
网关(读写审核/权限/审计/脱敏)+ 可插拔渠道(QQ / 飞书 / 网页 / Obsidian,均可不装)+ 管理
面板(配置树/校验/快照回滚/RBAC/模块总览)。充分模块化:一切可选部件声明在
`deploy/modules.json`,装机向导 `deploy/setup-wizard.py` 选装。

上手见 [README](README.md) / [CONTRIBUTING](CONTRIBUTING.md) /
[架构总览](docs/architecture-overview.zh-CN.md);各渠道"接自己的X"教程见
[docs/connect-your-own.zh-CN.md](docs/connect-your-own.zh-CN.md)。

数据与代码分离:代码开源;知识库数据、凭据、运行状态永不进仓库,默认值均为占位,
真实值由部署方 env 覆盖。以下 [Unreleased] 明细记录了 0.1.0 之前的开发历程。

### Added

- 科大DS/Ollama 全工具等价（claude-kimi 同范式 wrapper，PoC→生产）：
  - `deploy/bin/claude-ustc`：USTC 模型经 LiteLLM 协议翻译拿 Claude Code 全套工具；compose 新增 `litellm` 服务（`deploy/litellm/config.yaml`，密钥走 docker.env 的 `USTC_LLM_API_KEY`/`LITELLM_MASTER_KEY` 两个新键，镜像部署时应 pin digest——PyPI litellm 1.82.7/1.82.8 曾遭投毒）。
  - `deploy/bin/claude-ollama`：srv03 Ollama 原生 Anthropic 端点零代理全工具；token 固定 "ollama"，三档钉同一模型避免 Jetson 换载；Jetson prefill 分钟级（硬件特性）。
  - `claude-rtime` 分流：USTC 模型（ds/deepseek/qwen 别名家族）默认走 claude-ustc 全工具（`RTIME_USTC_AGENT=0` 退回旧内联纯聊天路）；新增 ollama/qwen-local 别名家族 → claude-ollama。
  - registry：ustc 模型 `agent_tools` 翻 true（经 LiteLLM）；新增 `ollama` provider（qwen3.5:9b 别名 ollama/qwen-local，qwen2.5:3b chat-only）。
  - 文档 `docs/model-providers.zh-CN.md`（两条线接法、速度预期、故障排查、orangepi 部署清单）。
- 一键更新机制（发布通道 + 实例配置分离 + 更新执行器）：
  - `deploy/update.sh`：`check`（探测新版本/BREAKING/待执行迁移，JSON 输出）、`apply`（备份 → checkout tag → 迁移记账 → build → up → 健康检查，失败自动回滚代码层）、`rollback`（回上一版本）、`status`（当前版本 + 容器健康）。
  - 实例目录约定 `~/rtime-instances/<name>/`（`.env` + `compose.override.yml` + `data/` + `state/`），多实例互相隔离，升级流程只写 `state/`。
  - `deploy/migrations/`：实例级一次性迁移脚本目录（`NNN_描述.sh`，幂等，按实例记账）。
  - `deploy/compose.override.example.yml`：实例覆盖模板。
  - 文档 `docs/instance-deploy.zh-CN.md`；owner 主实例现有 `scripts/docker-prod-check.sh` 流程不变。
