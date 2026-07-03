# 飞书桥配置项参考(FeishuBridgeConfig)

设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md(§二 批 1 · Lane A)。

状态:已建(批 1 · Lane A 合入本分支)。飞书桥的配置面已从 apps/feishu-bridge/bot_config.py 的模块级 os.getenv 迁到 schema 驱动的 pydantic-settings 模型 FeishuBridgeConfig,并注册进 admin-core registry 的 feishu 模块,面板/配置 Agent 可管理(全覆盖)。本次为收编+注册,行为保持不变,桥暂未改为从 registry 消费配置(仍走 from_env,见下)。

## 一、代码位置与真相源

字段真相源:apps/feishu-bridge/feishu_config.py 的 FeishuBridgeConfig(RtimeBaseSettings)。该模块刻意 import-safe(不加载凭据、除 load_dotenv 外无副作用),admin-core 懒加载它来注册,不会因进程未配置飞书凭据而报错。

兼容层:apps/feishu-bridge/bot_config.py 保留桥内其他模块 import 的模块级常量(FEISHU_APP_ID/CLAUDE_CLI/SESSIONS_DIR/ALLOWED_USERS 等),现在全部由一次 FeishuBridgeConfig.from_env() 派生,取值与类型与迁移前逐字一致;凭据仍走 _load_feishu_credentials 的 JSON 文件回落(未配置时抛错,失败即闭)。

注册:packages/rtime-admin-core/src/rtime_admin_core/registry.py 的 register_feishu_module(registry) 与 default_registry(include_feishu=...);从 feishu_config 懒加载 FeishuBridgeConfig,admin-core 不硬依赖 app(叶子性保持)。

自动生成的字段表:docs/config/feishu.md(由 python -m rtime_config feishu_config:FeishuBridgeConfig 生成,golden 测试 apps/feishu-bridge/tests/test_config_schema.py 守其不漂移)。

## 二、字段一览

env 名给出的是该字段实际接受的旧名(x-env-aliases);property key 恒为 Python 字段名。密钥字段标 x-secret(面板/API 只见 ***)。生效档:hot=下条会话即生效(元数据先行,桥内接线在后),restart=需重启进程。x-scope 是 admin-api 写鉴权边界。

| 字段 | env 名 | 类型 | 默认值 | 密钥 | 生效 | scope | 说明 |
|---|---|---|---|---|---|---|---|
| app_id | FEISHU_APP_ID | str/null | null | 是 | restart | write:channel | 飞书 app id。未设时回落 config_json 文件的 appId/app_id。 |
| app_secret | FEISHU_APP_SECRET | str/null | null | 是 | restart | write:channel | 飞书 app secret。未设时回落 config_json 文件的 appSecret/app_secret。 |
| config_json | FEISHU_CONFIG_JSON | str | ~/.config/rtime-assistant/feishu.json | | restart | write:channel | 凭据 JSON 文件路径(路径本身非密,文件内含密钥)。 |
| claude_cli | CLAUDE_CLI_PATH | str | claude | | restart | write:channel | claude CLI/claude-rtime 包装器。from_env 依次 CLAUDE_CLI_PATH→PATH→字面 claude。 |
| model | DEFAULT_MODEL | str | ""(空) | | hot | write:models | 默认模型;空=model_routing.default_model()(kimi-code)。 |
| default_cwd | DEFAULT_CWD | str | ~ | | restart | write:channel | 模型运行目录,~ 展开(默认 $HOME)。 |
| permission_mode | PERMISSION_MODE | str | default | | restart | write:channel | 工具权限模式(default/acceptEdits/bypassPermissions/plan)。 |
| mcp_config | FEISHU_MCP_CONFIG | str/null | null | | restart | write:channel | 传给 CLI 的 MCP 配置(内联 JSON 或路径)。空=None=沿用 ~/.claude.json+/mnt/brain(无变更)。 |
| model_aliases_json | MODEL_ALIASES_JSON | str | ""(空) | | restart | write:models | 追加/覆盖注册表基础别名的 JSON 对象;空=仅基础别名。 |
| allowed_users | ALLOWED_USERS | array<string> | []（空) | | hot | write:channel | 私聊白名单 open_id(逗号分隔)。 |
| allowed_chats | ALLOWED_CHATS | array<string> | []（空) | | hot | write:channel | 群 chat_id 白名单(逗号分隔)。 |
| admin_users | ADMIN_USERS | array<string> | []（空) | | hot | write:channel | 管理员 open_id 集;空=回落 allowed_users(向后兼容)。 |
| require_mention_in_group | REQUIRE_MENTION_IN_GROUP | bool | true | | restart | write:channel | 群内需 @bot 才回答(=0 关闭)。 |
| owner_personal_library_access | FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS | bool | false | | restart | write:channel | 放开 owner 个人库子树(tool_policy);=1 opt-in。 |
| sessions_dir | FEISHU_SESSIONS_DIR | str | ~/.feishu-claude | | restart | write:channel | 会话 id 存储,~ 展开。迁移前是硬编码路径(默认不变,补齐 env 消歧命名)。 |
| callback_port | CALLBACK_PORT | int | 9981 | | restart | | 可选 HTTP 回调/健康检查端口;卡片按钮优先走飞书 WebSocket。 |
| stream_chunk_size | STREAM_CHUNK_SIZE | int | 20 | | restart | | 流式卡片每积累多少字符推送一次。 |
| message_debounce_seconds | MESSAGE_DEBOUNCE_SECONDS | float | 0.0 | | restart | | 突发合并窗口(秒);0=关(Python 默认,生产在 env/Compose 开)。 |
| message_debounce_max_messages | MESSAGE_DEBOUNCE_MAX_MESSAGES | int | 20 | | restart | | 单窗口合并最大消息数。 |
| message_debounce_max_chars | MESSAGE_DEBOUNCE_MAX_CHARS | int | 12000 | | restart | | 单窗口合并最大字符数。 |
| status_heartbeat_seconds | STATUS_HEARTBEAT_SECONDS | float | 6.0 | | restart | | 模型静默时占位卡刷新间隔(秒);0 关。 |
| output_style | OUTPUT_STYLE | str | segmented | | restart | | 输出策略;segmented=按自然边界分条发、隐藏工具细节。 |
| show_tool_calls | SHOW_TOOL_CALLS | bool | false | | restart | | 是否展示所调用的工具/命令(=1 展示)。 |
| outbound_attachment_max_bytes | FEISHU_OUTBOUND_ATTACHMENT_MAX_BYTES | int | 31457280 | | restart | | 出站附件单文件上限(字节),模型 [[rtime-send-file/image]] 上传用。 |
| handover_model | CLAUDE_MODEL | str | claude-opus-4-6 | | restart | write:models | handover 深链记录的默认模型(handover.py)。 |
| watchdog_max_uptime_seconds | WATCHDOG_MAX_UPTIME_SECONDS | float | 14400.0 | | restart | | 强制重启上限(秒);看门狗到点重启进程,0 关(main.py 容错解析)。 |
| ngrok_domain | NGROK_DOMAIN | str | ""(空) | | restart | | 开发 ngrok 隧道域名(仅本地;生产走飞书 WebSocket)。 |

## 三、向后兼容与行为保持

一、旧 env 名全部继续可载:每字段经 env_aliases(=AliasChoices)声明其接受的旧名,x-env-aliases 记录同一份;golden 测试机械守其不漏。env_prefix="" 特意为之——只接受显式声明的名,不隐式扩面。

二、每模块迁移配对等价测试:apps/feishu-bridge/tests/test_config_schema.py 校验字段默认值 == 迁移前常量、旧 env 名仍载入、from_env 干净环境输出 == 旧计算默认(CLI PATH 查找、~ 展开、admin→allowed 回落、watchdog 容错)。桥内既有 249 项测试保持绿。

三、桥暂未改为消费 registry:本批只做收编(schema 化)+ 注册(可管理),不改桥的运行时取值链(仍 bot_config.from_env)。因此对飞书运行时零行为变更;registry 消费改造是后续工作。

## 四、部署依赖

FeishuBridgeConfig 依赖 rtime-config(pydantic-settings 基类)。三种运行形态都已接通:

一、uv workspace:feishu-bridge 是 workspace 成员,rtime-config 经 [tool.uv.sources] workspace 源可编辑装入 .venv。

二、仓库检出直跑(systemd):apps/feishu-bridge/_shared_runtime.py 启动时把 packages/rtime-config/src(与 rtime-chat-runtime)加入 sys.path;bot_config/feishu_config import 它取副作用。

三、Docker:docker/feishu-bridge.Dockerfile 的 bridge-base 阶段 COPY packages/rtime-config 到 /app/packages;requirements.txt 加 pydantic/pydantic-settings 作运行时依赖。

## 五、生成与校验命令

生成字段表(改 schema 后必须重跑并复核 diff):

    python -m rtime_config feishu_config:FeishuBridgeConfig --title 'feishu-bridge 配置项' --out docs/config/feishu.md

跑本模块测试:

    uv run --project apps/feishu-bridge --extra test python -m pytest apps/feishu-bridge/tests -q

看覆盖率(feishu 现已计入):

    uv run --all-packages python -m rtime_admin_core.coverage_doctor
