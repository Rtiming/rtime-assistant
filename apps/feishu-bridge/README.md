# feishu-claude-code

在飞书里直接和本机或服务器上的 Claude Code 对话。

WebSocket 长连接，多消息正文输出，手机上随时 code review、debug、问问题。

> 复用 Claude Max/Pro 订阅，不需要 API Key，不需要公网 IP。
> 在 `rtime-assistant` 中，本目录是 Python 候选桥；当前线上服务是否使用它，以 systemd unit 为准。

<p align="center">
  <img src="https://img.shields.io/badge/Python-3.11+-blue" alt="Python" />
  <img src="https://img.shields.io/badge/Claude_Code-CLI-blueviolet" alt="Claude Code" />
  <img src="https://img.shields.io/badge/License-MIT-green" alt="MIT" />
</p>

## 特性

**多消息正文输出**

- 助手正文按自然段落作为多条飞书消息发送
- 工具调用、命令和内部执行细节默认不展示
- 状态卡只承担进度、停止按钮和选项按钮等控制职责

**跨设备 Session 管理**

- 手机上开始的对话，回到电脑前接着聊
- CLI 终端里的会话也能在飞书恢复 (`/resume`)
- 后台自动生成会话摘要，方便找回历史对话
- CLI Handover: 终端会话一键移交到飞书继续

**交互式按钮**

- Claude 给出选项时，自动渲染成可点击按钮
- Y/N 确认、编号选项、Plan 模式审批，一键响应
- 输入 `/` 显示命令菜单，按钮分组一目了然

**群聊支持**

- @机器人 即可对话，不 @ 的消息静默忽略
- 每个群独立 session、模型、工作目录
- `/ws` 为不同群绑定不同项目，多群并发互不阻塞

**图片识别**

- 直接发截图，Claude 自动下载并分析

**健壮运行**

- 同一聊天里的新消息不会自动中断当前任务，会排队到下一轮处理
- 同一聊天短时间连续发送的普通文本可先合并为一次 Claude 调用
- `/stop` 是显式中断当前任务的入口
- 智能空闲超时: 检测子进程存活，编译/下载不会被误杀
- 看门狗默认 4 小时自动重启，Docker 生产可用 `WATCHDOG_MAX_UPTIME_SECONDS=0` 关闭固定周期重启
- API 调用自动重试 (指数退避)
- 分段延迟 trace：webhook、debounce、队列、占位卡、模型 spawn、first stdout、首个卡片/文本更新、done，以及 Feishu API 调用耗时都会写入结构化 run log（不含消息正文、open_id、token 或图片内容）
- 模型长时间无输出时，状态卡按 `STATUS_HEARTBEAT_SECONDS` 发送真实等待状态

## 快速开始

### 前置条件

| 依赖 | 最低版本 | 验证命令 |
|------|---------|---------|
| Python | 3.11+ | `python3 --version` |
| Claude Code CLI | 最新 | `claude --version` |
| Claude Max/Pro 订阅 | - | `claude "hi"` 能正常回复 |

### 安装

```bash
git clone https://github.com/joewongjc/feishu-claude-code.git
cd feishu-claude-code

python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# 编辑 .env，填入飞书应用凭证，或设置 FEISHU_CONFIG_JSON 指向本机凭证 JSON

python3 main.py
```

预期输出：

```
🚀 飞书 Claude Bot 启动中...
   App ID      : cli_xxx...
✅ 连接飞书 WebSocket 长连接（自动重连）...
```

> 从旧版升级的用户可运行 `python3 migrate_sessions.py` 迁移 session 数据（会自动备份）。

### Agent回归入口（无真实飞书网络）

`simulate_message_burst.py` 是本地消息注入模拟器，用于 agent/CI 验证同一聊天的防抖合并与处理链路。它不连接飞书、不发送真实消息；输出 JSON 中的 `reply_text` 是本地模拟回复，用来断言“模拟消息已走完处理链路并产生回复文本”。

```bash
cd apps/feishu-bridge
python3 simulate_message_burst.py 第一条 第二条 第三条 --debounce 0.05
```

验收重点：`input_count` 等于输入条数，防抖场景下 `process_count` 通常为 1，`reply_count` 大于 0，`processed[].text_preview` 保留合并后的提示词预览。真实 Feishu 往返、入站图片、`/stop` 与回滚演练仍归半值守验收。

## 命令速查

输入 `/` 可弹出按钮菜单，也可以直接输入命令。

### 会话管理

| 命令 | 说明 |
|------|------|
| `/new` | 开始新 session |
| `/new plan` | 新 session 并进入 Plan 模式 |
| `/resume` | 列出历史 session（按钮选择） |
| `/resume 3` | 恢复第 3 个 session |
| `/stop` | 停止当前运行中的任务 |
| `/status` | 查看当前 session 信息 |

### 模型与模式

| 命令 | 说明 |
|------|------|
| `/model` 或 `/models` | 打开模型切换按钮 |
| `/model kimi` | 切换到 Kimi Code；保留 Claude Code 工具调用能力 |
| `/model deepseek-code` | 切换到 DeepSeek Code；保留 Claude Code 工具调用能力 |
| `/model qwen-code` | 切换到 Qwen Code；保留 Claude Code 工具调用能力 |
| `/model ds` | 切换到 USTC DeepSeek chat 模型 |
| `/model qwen` | 切换到 USTC Qwen chat 模型 |
| `/model qwen-reasoner` | 切换到 USTC Qwen 推理 chat 模型 |
| `/model opus` | 切换到 Opus |
| `/model sonnet` | 切换到 Sonnet |
| `/model haiku` | 切换到 Haiku |
| `/mode bypass` | 跳过所有确认（默认） |
| `/mode plan` | 只规划不执行 |
| `/mode default` | 每次工具调用需确认 |
| `/mode accept` | 自动接受文件编辑 |

### 工作目录

| 命令 | 说明 |
|------|------|
| `/cd ~/project` | 切换工作目录 |
| `/ls` | 查看目录内容 |
| `/ws save api ~/projects/api` | 保存命名工作空间 |
| `/ws use api` | 绑定当前会话到工作空间 |
| `/ws list` | 列出所有工作空间 |
| `/ws remove api` | 删除工作空间 |

### 信息查询

| 命令 | 说明 |
|------|------|
| `/usage` | 查看 Claude Max 用量和重置时间 (macOS) |
| `/skills` | 列出已安装的 Claude Skills |
| `/mcp` | 列出 MCP Servers |
| `/help` | 帮助 |

### Skills 透传

`/commit`、`/review` 等未注册的斜杠命令直接转发给 Claude CLI 执行。你在 Claude Code 里能用的 Skill，飞书里也能用。

### 按需补 QQ 登录码

owner 私聊发 **`补码`**（或 `qq码` / `qq二维码` / `/qqcode`，大小写空格宽松）→ 不进模型，飞书桥写一个共享触发文件，orange pi 上的 `qq_selfheal` 守护轮询到就取当前最新 QQ 登录二维码回推你的飞书（无需等掉线、无需 SSH）。只对 owner（`ADMIN_USERS`）生效；普通文字消息和飞书富文本 `post` 都会走这条命令钩子。触发文件路径见 `RTIME_QQ_QR_REQUEST_FILE`，必须与守护的 `SELFHEAL_QR_REQUEST_FILE` 指向同一物理文件（详见 `apps/qq-bridge/README.md` 的“按需补码”）。

owner 私聊里的自然语言请求也支持工具调用：例如“我 QQ 小号掉线了，帮我把码发过来”“调一下 QQ 码”。这类消息会进入模型，本轮临时放行 `Bash(rtime-qq-code *)`，提示模型调用 `rtime-qq-code request`。该工具只写同一个共享触发文件，不读取 Docker、不复制二维码、不输出二维码图片；真正取码和回推仍由 host 上的 `qq_selfheal` 完成。

## 架构

```
┌──────────┐  WebSocket  ┌────────────────┐  subprocess  ┌────────────┐
│  飞书 App │◄───────────►│ feishu-claude  │─────────────►│ claude CLI │
│  (用户)   │  长连接      │  (main.py)     │ stream-json  │  (本机)     │
└──────────┘             └────────────────┘              └────────────┘
                                 │
                    ┌────────────┼────────────┐
                    │            │            │
              ┌─────▼──┐  ┌────▼─────┐  ┌──▼───────┐
              │commands│  │ session  │  │ feishu   │
              │        │  │ store    │  │ client   │
              └────────┘  └──────────┘  └──────────┘
```

**工作原理:**

1. 飞书通过 WebSocket 推送消息到本机
2. 调用 `claude` CLI 的 `--print --output-format stream-json` 模式
3. 解析 stream-json 事件流，提取文本增量和工具调用
4. 通过飞书卡片 PATCH API 实时更新消息内容
5. 每个聊天（私聊/群聊）维护独立的消息队列锁，保证并发安全

## 飞书应用配置

### 1. 创建应用

1. 打开 [飞书开放平台](https://open.feishu.cn/app)，点击「创建企业自建应用」
2. 填写应用名称（如 `Claude Code`），选择图标，点击创建

### 2. 添加机器人能力

1. 进入应用详情，左侧菜单选择「添加应用能力」
2. 添加「机器人」能力

### 3. 开启权限

进入「权限管理」页面，搜索并开启以下权限：

| 权限 scope | 说明 |
|-----------|------|
| `im:message` | 获取与发送单聊、群组消息 |
| `im:message:send_as_bot` | 以应用的身份发送消息 |
| `im:resource` | 获取消息中的资源文件（图片等） |

### 4. 启用长连接模式

1. 左侧菜单「事件与回调」→「事件配置」
2. 订阅方式选择「使用长连接接收事件」（不是 Webhook）
3. 添加事件：`im.message.receive_v1`（接收消息）
4. 确认卡片交互事件也由长连接接收，用于菜单按钮、模型切换、模式切换等交互

### 5. HTTP 卡片回调 (备用)

按钮交互优先走飞书 WebSocket 长连接事件；HTTP `/callback` 只是本机调试、反向代理或旧配置备用。

如需启用 HTTP 备用通道：

1. 「事件与回调」→「卡片交互配置」
2. 将 `CALLBACK_PORT`（默认 9981）通过 HTTPS 反向代理或 ngrok 暴露
3. 回调地址填写 `https://.../callback`

> 不启用 HTTP 回调时，消息收发和手动命令不受影响；按钮是否可用取决于飞书长连接卡片事件配置。

### 6. 获取凭证

1. 进入「凭证与基础信息」页面
2. 复制 App ID 和 App Secret，填入 `.env` 文件

### 7. 发布应用

1. 点击「版本管理与发布」→「创建版本」
2. 填写版本号和更新说明，提交审核
3. 管理员在飞书管理后台审核通过后即可使用

## 环境变量

| 变量 | 必填 | 默认值 | 说明 |
|------|:---:|-------|------|
| `FEISHU_APP_ID` | 否 | - | 飞书应用 App ID；设置后优先于 JSON |
| `FEISHU_APP_SECRET` | 否 | - | 飞书应用 App Secret；设置后优先于 JSON |
| `FEISHU_CONFIG_JSON` | 否 | `~/.config/rtime-assistant/feishu.json` | 包含 `appId`/`appSecret` 或 `app_id`/`app_secret` 的本机凭证 JSON |
| `DEFAULT_MODEL` | 否 | `claude-opus-4-6` | 默认 Claude 模型 |
| `MODEL_ALIASES_JSON` | 否 | Opus/Sonnet/Haiku | `/model` 使用的 JSON 映射；值为空字符串时走包装器默认模型。推荐保留 `deepseek-code`/`qwen-code` 为工具路径，`ds`/`qwen` 为 USTC chat 路径 |
| `DEFAULT_CWD` | 否 | `~` | Claude CLI 默认工作目录 |
| `WATCHDOG_MAX_UPTIME_SECONDS` | 否 | `14400` | 看门狗主动重启窗口，单位秒；设为 `0` 时禁用固定周期退出 |
| `PERMISSION_MODE` | 否 | `default` | 工具权限模式；代码默认 `default`。rtime 单用户 Feishu 生产机器人经 owner 明确授权使用 `bypassPermissions`，并必须限制 `ALLOWED_USERS`/`ADMIN_USERS` |
| `FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS` | 否 | `0` | owner 显式授权的个人库只读入口；设为 `1` 后，个人库/个人信息相关请求会临时允许 `Read`/`Grep`/`Glob`/`LS` 访问 `/mnt/brain/personal-data`、`profile`、`memory`。仅限单用户生产机器人 |
| `OUTPUT_STYLE` | 否 | `segmented` | `segmented` 时正文按多条消息发送 |
| `SHOW_TOOL_CALLS` | 否 | `0` | 设为 `1` 才显示工具调用细节，正常聊天保持隐藏 |
| `RTIME_ASSISTANT_RUN_LOG` | 否 | `~/.local/state/rtime-assistant/run-log.jsonl` | 结构化 JSONL 运行日志路径；不要放进 git 仓库 |
| `STREAM_CHUNK_SIZE` | 否 | `20` | 非 segmented 输出时的流式推送字符阈值 |
| `STATUS_HEARTBEAT_SECONDS` | 否 | `6` | 模型无输出时更新状态卡的间隔；设为 `0` 禁用 |
| `MESSAGE_DEBOUNCE_SECONDS` | 否 | `0` | 输入侧防抖窗口；生产 Compose 默认 `2.0`，同一 chat 近同时普通文本合并一次处理 |
| `MESSAGE_DEBOUNCE_MAX_MESSAGES` | 否 | `20` | 单次合并最多包含的消息数 |
| `MESSAGE_DEBOUNCE_MAX_CHARS` | 否 | `12000` | 合并后 prompt 的最大字符数，超出时截断并标注 |
| `ALLOWED_USERS` | 否 | 空 | 逗号分隔 open_id；生产必须设置为允许的个人用户 |
| `ADMIN_USERS` | 否 | `ALLOWED_USERS` | 逗号分隔 open_id；owner/管理权限（如按需补码只对这些人生效） |
| `RTIME_QQ_QR_REQUEST_FILE` | 否 | `/var/lib/rtime-assistant/qq-qr-request` | 按需补码触发文件（容器视角）。owner 私聊发“补码/QQ码”等普通文字或飞书富文本 `post` 时写此文件；owner 私聊中的自然语言“发码/调码/QQ 小号掉线”等请求会让模型调用 `rtime-qq-code request` 写同一文件。host 上的 `qq_selfheal` 守护读同一物理文件（其 `SELFHEAL_QR_REQUEST_FILE`）取最新码回推飞书 |
| `ALLOWED_CHATS` | 否 | 空 | 逗号分隔群 chat_id；为空时群聊默认不响应 |
| `REQUIRE_MENTION_IN_GROUP` | 否 | `1` | 群聊启用后是否要求 @ bot |
| `CLAUDE_CLI_PATH` | 否 | 自动查找 | Claude CLI 可执行文件路径 |
| `RTIME_CLAUDE_FALLBACK` | 否 | `/usr/local/bin/claude-kimi` | `claude-rtime` 遇到非 USTC 模型时委托的后端 |
| `RTIME_DEEPSEEK_CLAUDE_WRAPPER` | 否 | `/usr/local/bin/claude-deepseek` | DeepSeek Code 的 Claude Code wrapper |
| `RTIME_DEEPSEEK_API_KEY_FILE` | 否 | `/run/secrets/rtime-assistant/deepseek-api-key` | DeepSeek Code key 文件路径；真实 key 不进仓库 |
| `RTIME_QWEN_CLAUDE_WRAPPER` | 否 | `/usr/local/bin/claude-qwen` | Qwen Code 的 Claude Code wrapper |
| `RTIME_QWEN_API_KEY_FILE` | 否 | `/run/secrets/rtime-assistant/qwen-api-key` | Qwen/Model Studio key 文件路径；真实 key 不进仓库 |
| `RTIME_USTC_BASE_URL` | 否 | `https://api.llm.ustc.edu.cn/v1` | USTC OpenAI-compatible endpoint |
| `RTIME_USTC_API_KEY_FILE` | 否 | `/run/secrets/rtime-assistant/ustc-api-key` | USTC key 文件路径；真实 key 不进仓库 |
| `RTIME_USTC_MODELS` | 否 | DS/Qwen 候选 | 逗号分隔，允许由 `claude-rtime` 直接处理的模型 ID |
| `RTIME_ASSISTANT_HTTP_PROXY` / `RTIME_ASSISTANT_HTTPS_PROXY` | 否 | 空 | 生产 Docker 运行期代理；默认不透传宿主机代理 |
| `RTIME_ASSISTANT_NO_PROXY` | 否 | `localhost,127.0.0.1` | 运行期代理排除列表 |
| `CALLBACK_PORT` | 否 | `9981` | HTTP 回调、健康检查和 handover 端口；卡片按钮优先走 WebSocket 事件 |

图片消息会临时允许 Claude Code 使用 `Read` 读取飞书下载到临时目录的图片。
明确包含 URL、网页、网站、联网、网络搜索、网上、搜一下等词的请求会临时允许
`WebFetch`、`WebSearch` 和只读 `rtime-web-fetch` fallback。当前 Kimi wrapper
下 Claude Code 内置 WebFetch 可能在安全校验阶段失败，此时模型应改用
`rtime-web-fetch url <URL>` 或 `rtime-web-fetch search <query>`。

当 `FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS=1` 且消息明确提到个人库、个人信息、
聊天记录、经历、简历等个人资料时，桥会给本轮 Claude Code 调用补充只读工具
和运行提示。这个授权只面向单用户 Feishu 入口；不要把它理解成全局
`rtime-library-gateway` 默认放开 `personal-data`。长期 context source 仍不允许
登记 `personal-data`，个人资料写入、移动或整理仍需要用户单独确认。

### 延迟审计与 live truth

候选 Python 桥会向 `RTIME_ASSISTANT_RUN_LOG` 写两类附加事件：

- `feishu_latency_trace`：按 `trace_id` 串起 webhook、debounce、queue、placeholder、model_spawn、first_stdout、first_card_update/first_text_update、status_heartbeat、done 等阶段；
- `feishu_api_call`：记录 Feishu API operation、attempts、dur_ms、ok/error_type。

这些事件只含 hash、阶段名、耗时和非敏感状态，不记录消息正文、图片内容、token、open_id 或 chat_id 原值。

确认线上实际桥时，优先在 orangepi 本地运行只读审计：

```bash
python3 scripts/feishu-live-audit.py
python3 scripts/feishu-live-audit.py --include-journal
```

输出 `live_bridge` 可能为 `npm`、`python`、`docker`、`mixed` 或
`unknown-or-stopped`。脚本不读取 env 文件、不打印 secret、不停启服务。run-06
生产切换未完成前，不要仅因为 Python 候选代码改变就重建 live Feishu 容器。

## 部署

### macOS (launchctl)

```bash
cp deploy/feishu-claude.plist ~/Library/LaunchAgents/com.feishu-claude.bot.plist
# 修改 plist 中的路径为实际路径

launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.feishu-claude.bot.plist
launchctl list | grep feishu-claude
tail -f /tmp/feishu-claude.log
```

### Linux (systemd)

```bash
sudo cp deploy/feishu-claude.service /etc/systemd/system/
# 修改 service 中的路径和 User

sudo systemctl daemon-reload
sudo systemctl enable --now feishu-claude
journalctl -u feishu-claude -f
```

服务会自动重启。看门狗默认每 4 小时主动重启一次进程，刷新 WebSocket 连接；Docker 生产建议设置 `WATCHDOG_MAX_UPTIME_SECONDS=0`，由 healthcheck/systemd 处理真实故障。

## CLI Handover

从终端把当前 Claude Code 会话移交到飞书继续：

```bash
python3 handover.py "对话中的一段独特文本"
```

脚本会在 `~/.claude/projects/` 中搜索匹配的 session，然后通知飞书 Bot 切换过去。适合电脑前调试完，出门用手机继续跟进的场景。

---

## English

**feishu-claude-code** bridges your local Claude Code CLI with Feishu/Lark messenger via WebSocket.

- **No public IP needed** - Feishu WebSocket long connection, runs on your local machine
- **Segmented chat output** - Assistant text is sent as natural messages, while tool details stay hidden by default
- **Reuses Claude Max/Pro subscription** - No API key required
- **Cross-device sessions** - Continue conversations between phone and desktop
- **Group chat support** - @mention filtering, per-group session isolation, concurrent groups
- **Interactive buttons** - Options and confirmations rendered as clickable buttons
- **Image recognition** - Send screenshots for Claude to analyze
- **Skills passthrough** - `/commit`, `/review`, etc. work directly in Feishu
- **CLI handover** - Transfer terminal sessions to Feishu on the go
- **Smart idle timeout** - Detects active child processes, won't kill long compilations

Quick start: clone, `pip install -r requirements.txt`, configure `.env` with Feishu app credentials, run `python3 main.py`.

See Chinese sections above for detailed setup instructions.

## License

[MIT](LICENSE)
