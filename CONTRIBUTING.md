<!-- SPDX-License-Identifier: AGPL-3.0-only -->
# 贡献指南 / Contributing

欢迎参与 rtime-assistant。本文是**外部贡献者的单一上手真相源**:怎么装、怎么改、
怎么测、怎么提。仓库内更详细的开发流程见 [docs/development-workflow.md](docs/development-workflow.md)
(那份含 owner 多机协作细节,你只需读本文即可开工)。

架构先读:[docs/architecture-overview.zh-CN.md](docs/architecture-overview.zh-CN.md)。

## 一、这是什么
一套自托管的个人/组织 AI 助手运行时:一个可检索的知识库(`brain`)+ 一个**唯一入口
网关**(所有对库的读写都过它审核)+ 多个**可插拔渠道**(QQ / 飞书 / 网页)+ 一个管理
面板。**充分模块化**——不想要的渠道/集成完全不装(见 `deploy/modules.json`)。

数据与代码分离:代码在本仓库开源;知识库数据、密钥、运行状态**永不进仓库**。

## 二、环境准备
前提:Python ≥ 3.10、[uv](https://docs.astral.sh/uv/)(workspace 包管理)、git。
可选:Docker(跑渠道服务)、Node(obsidian 插件)。

```bash
git clone <仓库地址> && cd rtime-assistant
uv sync --all-packages --extra test   # 装全部 workspace 包 + 测试依赖
```

装机选模块(哪些渠道/集成要装)——交互式向导:
```bash
python3 deploy/setup-wizard.py list                       # 看所有可选模块
python3 deploy/setup-wizard.py init --instance ./my-inst --modules channel-qq,channel-web
```
详见 [docs/reference/setup-wizard.zh-CN.md](docs/reference/setup-wizard.zh-CN.md) 与各模块
"接自己的X"教程 [docs/connect-your-own.zh-CN.md](docs/connect-your-own.zh-CN.md)。

## 三、跑测试(改任何东西前先跑通)
这是 uv workspace,测试要带 `--all-packages`(否则漏跑跨包依赖):
```bash
uv run --all-packages --extra test python -m pytest tests/ packages/ -q
```
单包/单文件:
```bash
uv run --all-packages --extra test python -m pytest tests/test_xxx.py -q
```
app 有自己的 venv(qq-bridge/feishu-bridge 等):`cd apps/qq-bridge && uv run --extra test python -m pytest -q`。
注意:web-chat 的新鲜度测试对"哪个 python 跑"敏感,务必用 `uv run`(venv 的 python)。

## 四、分支与提交
- **单一主干 `main`**,只从 main 部署。一个分支只做一件事,命名 `feat/<topic>` /
  `fix/<topic>` / `docs/<topic>` / `chore/<topic>`。合并后删除,不长期堆叠。
- 提交前本地校验门自动跑(pre-commit):跨设备可移植性(**禁止硬编码个人主目录/内网IP**)、
  入口漂移、SPDX 头。装一次:`pip install pre-commit && pre-commit install`。
- 提交说明:一行主题(中文可)+ 必要正文;说清**改了什么、为什么**。

## 五、硬规矩(必须遵守)
1. **网关是库的唯一入口**:任何对 `brain` 的读写都经 `rtime-library-gateway`,
   不要绕过它直接读文件系统。写动词都是两段式(plan→confirm_token→apply)。
2. **密钥/内网IP/机器路径绝不进代码**:凭据走 env/keyfile;默认值用 `127.0.0.1`/占位,
   真实值由部署方 env 覆盖。可移植性门会拦硬编码个人路径。
3. **每个源文件带 SPDX 头**(`SPDX-License-Identifier: AGPL-3.0-only`);
   新增文件跑 `python3 scripts/add-spdx-headers.py` 盖章。
4. **每个功能配文档**:design(为什么)+ reference(怎么用),归位见 docs/README.zh-CN.md。
5. **改配置字段**要同步:schema(admin-core registry)+ 重生成字段文档 + 过覆盖率守卫。
6. 修**通用问题**,不只修表面那一个实例。

## 六、模块化开发(加渠道/集成)
所有可选能力都是"模块",声明在 `deploy/modules.json`(单一真相)。加一个模块:
1. modules.json 加条目(id/kind/compose_profile/config_module/depends_on/docs)。
2. 有配置:在 admin-core registry 注册 config_module(schema)。
3. 装机可选:compose.prod.yml 给它 `profiles: ["<name>"]`。
4. 写 reference 文档 + "接自己的X"教程。
5. `python -m rtime_admin_core.modules_cli doctor` 必须绿。
详见 [docs/reference/modules.zh-CN.md](docs/reference/modules.zh-CN.md)。

## 七、提 PR
1. 从 main 拉分支 → 改 → 本地测试全绿 + pre-commit 过。
2. 推分支 → 开 PR,说明动机 + 影响面 + 测试结果。
3. 涉及库写路径/权限/密钥的改动,PR 里要说清安全影响。

## 许可
贡献即同意以 [AGPL-3.0-only](LICENSE) 授权。第三方/上游代码的许可见 [NOTICE](NOTICE)。
