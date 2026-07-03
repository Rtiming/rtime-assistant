<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# rtime-assistant

[English](README.md) · 中文

自托管的个人/组织 AI 助手运行时。围绕一个可检索的知识库(`brain`)提供检索、问答与维护
能力,用户从多个渠道提问,助手经**唯一入口网关**读取知识库、调用模型、答复。

## 它是什么

- **知识库(brain)**:资料/笔记的可检索仓库(BM25 + 向量混合检索)。数据在仓库外,不开源。
- **唯一入口网关**(`rtime-library-gateway`):所有对库的读写都过它——权限门、审计、脱敏、
  两段式写(plan→确认→落盘)。被共享方(如学生会)只拿只读子集 + 投稿,不能直接改库。
- **可插拔渠道**:QQ 机器人、飞书机器人、网页问答、Obsidian 侧边助手——每个都能**不装**。
- **管理面板 + API**(`rtime-admin-api`):配置树、校验、快照/回滚、审计、RBAC、模块总览,
  只绑 `127.0.0.1`。
- **充分模块化**:一切可选部件声明在 `deploy/modules.json`(单一真相),装机向导按需选装。

**数据与代码分离**:代码在本仓库开源(AGPL-3.0);知识库数据、凭据、运行状态**永不进仓库**,
代码里的默认值都是占位,真实值由部署方 env 覆盖。

## 快速开始

前提:Python ≥ 3.10、[uv](https://docs.astral.sh/uv/)、git(可选 Docker、Node)。

```bash
git clone https://github.com/Rtiming/rtime-assistant && cd rtime-assistant
uv sync --all-packages --extra test                       # 装依赖
uv run --all-packages --extra test python -m pytest tests/ packages/ -q   # 跑测试

# 选装哪些渠道/集成(交互式向导)
python3 deploy/setup-wizard.py list
python3 deploy/setup-wizard.py init --instance ./my-inst --modules channel-qq,channel-web
```

各渠道"接自己的 QQ 号 / 飞书应用 / OB vault / 公众号"教程见
[docs/connect-your-own.zh-CN.md](docs/connect-your-own.zh-CN.md)。

## 文档

- **架构总览**(先读):[docs/architecture-overview.zh-CN.md](docs/architecture-overview.zh-CN.md)
- **贡献指南**:[CONTRIBUTING.md](CONTRIBUTING.md)
- **模块化**:[docs/reference/modules.zh-CN.md](docs/reference/modules.zh-CN.md)
- **安装向导**:[docs/reference/setup-wizard.zh-CN.md](docs/reference/setup-wizard.zh-CN.md)
- **文档索引**:[docs/README.zh-CN.md](docs/README.zh-CN.md)

## 三条不可违背的原则

1. **网关是库的唯一入口** —— 绝不绕过网关直接读写 brain 文件系统。
2. **数据与代码分离** —— 代码开源;数据/凭据/运行状态永不进仓库。
3. **模块化 opt-out** —— 不想要的部件完全不装。

## 许可

[AGPL-3.0-only](LICENSE)。第三方/上游代码许可见 [NOTICE](NOTICE)。贡献即同意以 AGPL-3.0 授权。
