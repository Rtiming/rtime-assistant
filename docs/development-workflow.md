# 开发流程规范 / Development Workflow

本仓库由用户本人和多个 AI 助手（codex、claude、kimi 等）协作开发，代码在
Mac 上编写、在 orangepi 上运行。本文是**唯一的流程真相源**：分支、提交、校验、
部署都按这里执行。改动流程本身请改本文件并在提交说明里指出。

适用范围：本仓库 `rtime-assistant` 全部代码、脚本、文档、技能与 MCP 包。

## 1. 单一主干

- **唯一主干是 `main`。** 运行时 `<运行时主机>:~/rtime-assistant` 只从 `main`
  部署。`origin` 是运行时主机上的裸库 `<你的服务器>:~/rtime-assistant.git`，
  `origin/HEAD` 指向 `main`。
- **`master` 已退役**，不再提交、不再部署；保留只为历史，新工作一律不基于它。
- 任何功能分支最终都要合回 `main`，合并后删除，不长期堆叠。

## 2. 分支规范

- **一个分支只做一件事**，命名 `<type>/<topic>`：
  - `feat/<topic>`：新功能（如 `feat/obsidian-client-parity`）
  - `fix/<topic>`：缺陷修复（如 `fix/gateway-turn-cap`）
  - `docs/<topic>`：仅文档
  - `chore/<topic>`：工具/构建/治理
  - `refactor/<topic>`：不改行为的重构
- 分支短命：从最新 `main` 切出 → 小步提交 → 过校验门 → 合回 `main` → 删除分支。
- **不要把多件不相关的事压在同一条分支或同一个脏工作树里。** 作者身份写在提交
  尾注（见 §3），不通过分支名表达。
- 从 `main` 切分支：

  ```bash
  git switch main && git pull --ff-only
  git switch -c fix/<topic>
  ```

## 3. 提交规范

- 提交标题：`<type>: 简述`，与分支同 type（feat/fix/docs/chore/refactor）。
- 正文说清**为什么**这么改、影响面、如何验证；不只写"改了什么"。
- AI 助手提交在正文末尾加作者尾注，例如：

  ```text
  Co-Authored-By: <assistant> <noreply@...>
  ```

- **非平凡改动**配一份开发日志 `docs/development-log-YYYY-MM-DD-<topic>.md`，
  记录背景、改动、验证、后续规则（沿用已有约定）。
- 不提交：secrets、运行日志、`brain`/personal-data 资料、临时产物、`__pycache__`。

## 4. 提交前校验门（绿了才提交）

每次提交前在仓库根目录跑：

```bash
scripts/maintenance.sh quick          # audit-env + git 空白检查 + 变更模块 dry-run
python -m pytest <相关测试文件> -q     # 改到哪个模块就跑哪个模块的测试
git diff --check                       # 行尾/空白
python tools/rtime-project-check.py . --strict --no-git   # 跨设备可移植性:硬编码主目录路径/CRLF/断链/超长 Windows 路径
```

跨设备可移植性校验已挂成 **pre-commit 闸门**(`.pre-commit-config.yaml` +
vendored `tools/rtime-project-check.py`,纯标准库、Windows/Mac/Linux 都能跑)。
每台开发机执行一次 `pip install pre-commit && pre-commit install`,之后每次
`git commit` 自动拦可移植性问题(硬编码主目录路径、CRLF/编码漂移、断链、超长 Windows 路径)。
升级校验器:从 `~/.ai-skills/rtime-project/scripts/rtime-project-check.py` 覆盖 vendored 副本。

要知道"改了这些文件该跑哪些模块检查"，用只读的 agent-control MCP / CLI 先规划：

```bash
PYTHONPATH=packages/rtime-agent-control/src \
  python -m rtime_agent_control validation-plan --changed --repo-root "$PWD"
```

涉及某个登记模块时，跑该模块的完整门：

```bash
scripts/module-submit-check.py --module <module> --dry-run
```

新增/修改包、技能、插件、MCP 时，按 `docs/module-submit-workflow.md` 在
`module-submit.json` 登记，并让 `scripts/module-submit-check.py --changed` 能覆盖。

本地门之外还有一道**服务端咨询门**:orangepi 裸仓库的 `post-receive`(真相源
`deploy/git-hooks/post-receive`)对每次 push 同步跑可移植性快门并把结果回显给推送者，
对 main 后台跑 pytest 慢门。它**非阻塞、永不拒 push**，是所有客户端 push 的汇聚反馈点，
不替代本地门。说明与部署见 `docs/ci-server-gate.zh-CN.md`。

## 5. 部署流程（只从 main）

源在 Mac，运行时在 orangepi。流程固定为：**改源 → 过校验门 → 合并 main →
push → orangepi 拉取 → 重启受影响的 service → 复测**。

Mac：

```bash
git switch main && git pull --ff-only
git merge --ff-only <type>/<topic>     # 或 PR 合并后拉取
git push                               # 推到 orangepi 裸库
git branch -d <type>/<topic>           # 合并后删分支
```

orangepi：

```bash
scripts/deploy-on-orangepi.sh          # git pull --ff-only + audit-env + daemon-reload
systemctl --user restart <受影响的 service>   # 只重启受影响的，不要全量重启
```

- env 改动（如 `deploy/env/assistant-gateway.env.example` 新增/删除变量）要同步
  更新 orangepi 上的实际 env 文件，再重启 service；删除的变量若被代码无视则可留作
  inert，但建议清掉以免误导。
- 部署后复测：`healthz` + 一问回归；网关类改动顺带核对 `requests.jsonl` 的
  `budget_profile` 与是否有残留 claude 子进程。

## 6. 工具速查

| 工具 | 用途 |
|---|---|
| `scripts/maintenance.sh quick\|changed\|governance\|env` | 提交前/治理校验的统一入口 |
| `scripts/module-submit-check.py --changed\|--module <m>` | 模块级提交门 |
| `scripts/audit-env.sh [--profile mac\|orangepi]` | 环境/路径审计（profile-aware） |
| `scripts/deploy-on-orangepi.sh` | orangepi 拉取部署 |
| `rtime-agent-control`（MCP/CLI） | 只读控制面：盘点工具、规划校验、渲染 MCP 配置、上下文分流（见 `docs/agent-control-mcp.md`） |

## 7. 多助手协作约定

- 同一仓库多个助手协作：作者身份写在提交尾注，分支按 §2 的 `<type>/<topic>`。
- 接手别人未提交的工作树前，先盘点"保留/修正/存疑"，再按主题切成聚焦提交，不要
  整坨一次性提交。
- 已确认错误的设计（例如曾经的网关 `--max-turns` 工具轮次上限）**不要重新引入**；
  此类决定记录在相关 `fix/` 提交、本文件与 `docs/development-log-*.md` 中。

## 8. 多客户端与 Windows 开发机

开发客户端现在有两台：**Mac** 和 **Windows**；运行时仍是 **orangepi** 角色。三者都克隆
运行时主机裸库 `<你的服务器>:~/rtime-assistant.git`，`main` 是唯一主干和部署源。

### 8.1 本地工作目录放什么

本地 clone 只放**仓库本身**（代码、脚本、文档、skills、plugins、MCP 源）。下列一律
**不在**本地工作目录、也不进 git：

- `brain` 知识/记忆/资料 —— 在 `brain` 库（运行时 `<brain-root>`，
  Mac `<local-brain-mount>`，Windows `C:\Users\<用户名>\OrangePi-Store\sync\brain`；
  **三机均已挂载/可访问**，2026-06-23 实测）。
- `rtime-hub` 项目/设备/同步状态 —— 在 `rtime-hub` 仓。
- secrets / `.env` / token / session —— 各机器本地，`.gitignore` 已排除；`.env` 从
  `.env.example` 拷，按机器填。
- 运行时 state/logs（`.local/state`、`logs/`）、`.venv/`、`node_modules/`、缓存、
  构建产物 —— 各机器自建，不提交。

### 8.2 多客户端收敛（同一主干，别并行写）

`.git` 由 git 自己写，**绝不**用 Syncthing/网盘等实时文件同步去同步代码工作树或
`.git`（会损坏仓库）。代码的“同步”只走 git：

- **开工前**：`git switch main && git pull --ff-only`（或下面的 `git sync`）。
- **收工**：过校验门 → 提交 → `git push`。
- **同一分支不要在两台机器并行改**；一台一个 `<type>/<topic>` 分支，合回 main 后删。
- 便捷收敛别名（各机器本地设一次，只搬**已提交**的工作、绝不自动 commit）：

  ```bash
  git config --global alias.sync '!git fetch origin && git pull --rebase --autostash && git push'
  ```

### 8.3 Windows 客户端约束

Windows 现在是**双重角色:代码开发机 + 数据流水线算力机**(爬取/转写/分析,见
`docs/data-pipeline-norms.zh-CN.md`)。brain 已挂载、ollama/poppler(TeXLive)/tesseract/
python/uv/paddleocr 已装,**爬取与文档转写可在 Windows 上跑**(2026-06-23 实测,§四)。
但**Linux/bash 的那部分**仍不能在原生 PowerShell 跑:

- 行尾由 `.gitattributes` 统一为 LF；Windows 上设 `git config --global core.autocrlf false`
  与 `git config --global core.longpaths true`。`*.sh`/`*.py`/`*.service` 等强制 LF，
  否则 orangepi 上会 `bad interpreter: /bin/bash^M`。
- **Linux-only 的步骤**(`scripts/maintenance.sh`、`scripts/module-submit-check.py`、
  pytest、docker、systemd、部署)到 orangepi 跑（`ssh orangepi '...'`）或本机 WSL2/Docker；
  不要在原生 Windows 上假装能跑通这些。
- 只读的 git 检查（`git diff --check` 等）可在 Git Bash 里跑。
- 仓库已在 `C:\Users\<用户名>\Desktop\rtime-assistant`(`origin=<你的服务器>:~/rtime-assistant.git`,
  `main`,git 同步)。待补齐:`soffice`(LibreOffice,Office/PPT 转换需要)。
- **可移植性闸门在 Windows 上原样跑**：`tools/rtime-project-check.py`（纯标准库）+ pre-commit
  钩子拦硬编码主目录路径 / CRLF / 断链 / 超长 Windows 路径，这一门 Windows/Mac/Linux 通用。
- **pre-commit 用 `language: system`（不联网建 venv）**：entry 是 `python <脚本>`，要求每台克隆
  PATH 上有可执行的 `python`。Windows、orangepi 自带；macOS 默认只有 `python3`，已在 Mac 的
  `/opt/homebrew/bin` 建 `python -> python3` 软链补上（撤销：`rm /opt/homebrew/bin/python`）。
  不用 `language: python`，因为那会在每台机联网建 venv，Mac 的 pip 到 PyPI 不稳时会失败。
  安装(每台一次)：Win/orangepi `pip install pre-commit`、Mac `brew install pre-commit`，再 `pre-commit install`。
- **Windows-only 可移植性 bug 必须在 Windows 上测才抓得到**：涉及文件 IO / 路径 / 符号链接 /
  索引（SQLite）的 Python 改动，在 orangepi 上跑测试**永远测不出** Windows 专属问题（隐藏文件写
  `PermissionError`、`os.readlink` 的 `\\?\` 前缀、未关句柄时 `os.replace` 的 `WinError 32`、
  `\` vs `/`、`import` 顶层强依赖）。改这类代码时，本机跑一遍该模块 `pytest` 是值得的；
  别把"orangepi 绿了"当成 Windows 也对。2026-06-18 已据此修复 5 处，见
  `docs/brain-library-module.md` §6。

### 8.4 给本地 AI agent 的硬约束

agent 在本地（Mac/Windows）改代码时，必须：

- 遵守 §1–§7：基于最新 `main` 开 `<type>/<topic>` 分支，小步提交，标题 `<type>: 简述`
  + 正文讲清为什么/影响/如何验证 + `Co-Authored-By` 尾注；非平凡改动配
  `docs/development-log-YYYY-MM-DD-<topic>.md`。
- **验证不靠本地**：要测 Linux/服务/`brain`/docker 行为，用 `ssh orangepi '...'` 在
  orangepi 上跑校验门和服务复测，或本机 WSL/Docker；**不要把“在 PowerShell 里跳过校验门”
  当成通过**。env-only 失败按 `docs/module-submit-workflow.md` 显式记录。
- 不提交 secrets/`.env`/日志/`brain` 数据/运行时 state；不改 `brain`、`rtime-hub` 的事实。
- 只从 `main` 部署；生产服务切换必须有 rollback（§5、`docs/project-map.zh-CN.md` 原则）。

## 变更记录

- 2026-06-18（标准化闸门）：加入 `.editorconfig` + `.pre-commit-config.yaml` + vendored
  `tools/rtime-project-check.py`；§4 校验门加入跨设备可移植性检查并挂 pre-commit；§8.3 补
  "Windows-only 可移植性 bug 须在 Windows 实测"约束（本轮在 Windows 修复 5 处，见
  `docs/brain-library-module.md` §6）。
- 2026-06-18：新增 §8 多客户端与 Windows 开发机；加入 `.gitattributes` 统一行尾；
  明确本地工作目录边界、多客户端 git 收敛与本地 agent 约束。
- 2026-06-16：建立本规范。确立单一主干 `main`、`<type>/<topic>` 分支、提交前
  校验门、只从 main 部署；退役 `master`。
