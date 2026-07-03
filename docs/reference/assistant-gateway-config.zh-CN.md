# assistant-gateway 配置项参考(AssistantGatewayConfig)

设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md(§二 批 2)。

状态:已建(批 2 合入本分支)。assistant-gateway(Obsidian 后端网关)的配置面已从 apps/assistant-gateway/gateway_config.py 的 os.environ 读取块迁到 schema 驱动的 pydantic-settings 模型 AssistantGatewayConfig,并注册进 admin-core registry 的 assistant-gateway 模块,面板/配置 Agent 可管理(全覆盖)。本次为收编+注册,行为保持不变——load_config() 仍返回与迁移前逐字一致的配置字典,网关运行时零行为变更。

## 一、代码位置与真相源

字段真相源:apps/assistant-gateway/gateway_config_schema.py 的 AssistantGatewayConfig(RtimeBaseSettings)。该模块刻意 import-safe(除经 _shared_runtime 把 rtime_config 放上 sys.path 外无副作用:不 import rtime_models、不解析 Path.home()、import 时不读 env),admin-core 懒加载它来注册,不会因进程未配置网关而报错或做工作。

兼容层:apps/assistant-gateway/gateway_config.py 的 load_config() 现在是薄适配器:加载一次 AssistantGatewayConfig.from_env(),再拼回历史字典形状——同样的 key、同样的 Path 类型、同样的 rtime_models.base_url 回落、同样的 sanitize_permission_mode / access_mode 变换。gateway.py:981 仍 from gateway_config import load_config 并调用它,取值与类型与迁移前逐字一致。

为何拆两层:HOME/brain_root/log_dir 相对路径默认(claude_bin、index_db、memory_root、context_sources_path 等)不做成字段默认,而在 schema 里留空串(""),由 load_config 对着 Path.home() 等解析。这样 schema 模块保持严格 import-safe(import 时不碰 Path.home() / rtime_models),每个 tail 字符串只有一个 owner(在 gateway_config.py)。

注册:packages/rtime-admin-core/src/rtime_admin_core/registry.py 的 register_assistant_gateway_module(registry) 与 default_registry(include_assistant_gateway=...);从 gateway_config_schema 懒加载 AssistantGatewayConfig,admin-core 不硬依赖 app(叶子性保持)。coverage_doctor.py 的 auto-include 探测环加了 include_assistant_gateway(gateway_config_schema 可导入即计入)。

自动生成的字段表:docs/config/assistant-gateway.md(由 python -m rtime_config gateway_config_schema:AssistantGatewayConfig 生成,golden 测试 apps/assistant-gateway/tests/test_config_schema.py 守其不漂移)。

## 二、字段一览

env 名给出的是该字段实际接受的旧名(x-env-aliases);property key 恒为 Python 字段名。本模块无密钥字段——网关不内嵌凭据,ustc_api_key_file 只是 keyfile 路径(路径本身非密,文件内含密钥,由 models.py 的 _read_secret 读)。生效档全部 restart:网关 load_config() 在启动时只调用一次,任何字段改动都需重启进程才生效。x-scope 是 admin-api 写鉴权边界(纯调参旋钮按模块级 scope,有意省略字段级 x-scope,照 feishu 样板)。

完整字段/默认值表见自动生成的 docs/config/assistant-gateway.md(60 字段)。要点如下:

一、传输:bind(GATEWAY_BIND,默认 127.0.0.1)、port(GATEWAY_PORT,默认 8765,与 Obsidian 插件/部署 env/文档同步锁定)。

二、claude CLI 与超时:claude_bin(CLAUDE_BIN)、claude_timeout(110)、claude_max_turns(空=不设上限)、claude_investigation_timeout(180)、claude_web_timeout(170)、claude_runtime_diag_timeout(90)、claude_bare/claude_no_session_persistence/claude_exclude_dynamic_sections(默认开)、claude_permission_mode(CLAUDE_PERMISSION_MODE,默认 dontAsk,load_config 里经 sanitize_permission_mode 归一)。

三、访问与工具:approval_forwarding_enabled(GATEWAY_APPROVAL_FORWARDING)、gateway_access_mode(GATEWAY_ACCESS_MODE,默认 readonly,经 access_mode 归一为 readonly/full)、web_tools_enabled(GATEWAY_WEB_TOOLS_ENABLED)、extra_allowed_tools(GATEWAY_EXTRA_ALLOWED_TOOLS)。

四、记忆环:memory_capture_enabled/memory_failed_query_log_enabled(默认关)、memory_capture_max_chars(800)、memory_injection_enabled(默认开)、memory_root(MEMORY_ROOT,空=brain_root/memory)、memory_injection_max_cards(3)/max_chars(1200)、memory_access_log_enabled(默认开)。

五、动态上下文源:context_sources_enabled(默认开)、context_sources_path(空=brain_root/_system/rtime-context-sources.jsonl)、max_items(3)/max_chars(5000)。

六、记忆候选写入:memory_candidate_write_enabled(默认开)、memory_candidate_review_dir(空=brain_root/memory/review-queue)。

七、关系预取:relations_path(空=brain_root/_indexes/relations.jsonl)、related_prefetch_limit(5)/max_chars(1200)。

八、队列(v0.3 会话协议):queue_max(2)、queue_wait_timeout(30)、queue_heartbeat_secs(3)。

九、prepare 缓存与预热:prepare_cache_ttl(180)/max(64)、prewarm_enabled/live_prewarm_enabled(默认开)、live_prewarm_idle_seconds(240)、prewarm_ttl_seconds(240)、prewarm_timeout(30)。

十、历史/流式/入库/抽取:history_max_chars(4000)、stream_trace_enabled(默认开)、intake_max_mb(64)、file_extract_max_files(4)/max_chars(80000)。

十一、通知/提醒/模型目录/插件发布:notify_target、reminder_register(空=$HOME/.local/bin/rtime-reminder-register)、model_catalog_path(空=log_dir/model-catalog.json)、plugin_release_dir(空=$HOME/.local/share/…)、model_refresh_timeout(8)。

十二、provider base URL 与 keyfile:moonshot_base_url(RTIME_MOONSHOT_BASE_URL,空=rtime-models 注册表 base_url('moonshot-openai'),去尾斜杠)、ustc_base_url(同,ustc-openai)、ustc_api_key_file(RTIME_USTC_API_KEY_FILE,空=$HOME/.config/rtime-assistant/ustc-api-key)。

十三、网关客户端:gateway_url(RTIME_GATEWAY_URL,默认 http://127.0.0.1:8765,rtime_chat.py 客户端 POST 目标)。

## 三、向后兼容与行为保持

一、旧 env 名全部继续可载:每字段经 env_aliases(=AliasChoices)声明其接受的旧名,x-env-aliases 记录同一份;env_prefix="" 特意为之——网关字段前缀混杂(GATEWAY_*/CLAUDE_*/MEMORY_*/QUEUE_*/INDEX_*/RTIME_*/BRAIN_ROOT/HISTORY_*),只接受显式声明的名,不隐式扩面。golden 测试机械守其不漏。

二、行为保持等价测试:apps/assistant-gateway/tests/test_config_schema.py 校验字段默认值、旧 env 名仍载入、from_env 干净环境输出、load_config 适配器对空路径 sentinel 的解析(Path.home()/brain_root/log_dir 拼接)、sanitize_permission_mode / access_mode 归一、base URL 去尾斜杠。另在仓根 tests/test_assistant_gateway.py(115 项)保持绿——网关模块 import + 运行链不受影响。

三、from_env 里刻意不预归一:claude_permission_mode / gateway_access_mode 在 from_env 里留原始 env 值("" 当未设),归一(sanitize_permission_mode / access_mode)发生在 load_config 适配器——逐字对应迁移前 os.environ.get(...) 喂给这两个变换的写法。

四、网关暂未改为消费 registry:本批只做收编(schema 化)+ 注册(可管理),不改网关的运行时取值链(仍 gateway_config.load_config → from_env)。因此对网关运行时零行为变更;registry 消费改造是后续工作。

## 四、部署依赖

AssistantGatewayConfig 依赖 rtime-config(pydantic-settings 基类)与 rtime-models(from_env 读 provider base URL)。两种运行形态都已接通:

一、仓库检出直跑(systemd,生产形态):deploy/systemd/user/assistant-gateway.service 从仓库检出跑 apps/assistant-gateway/gateway.py;apps/assistant-gateway/_shared_runtime.py 启动时把 packages/rtime-config/src 与 packages/rtime-models/src 加入 sys.path;gateway_config / gateway_config_schema import 它取副作用。无需 pip install。

二、测试/文档生成:apps/assistant-gateway/pyproject.toml 是独立 uv 项目(非 workspace 成员,照 apps/qq-bridge),经 [tool.uv.sources] 路径源可编辑装入 rtime-config/rtime-models/rtime-admin-core。

## 五、生成与校验命令

生成字段表(改 schema 后必须重跑并复核 diff):

    PYTHONPATH="apps/assistant-gateway:packages/rtime-config/src:packages/rtime-models/src" \
      python -m rtime_config gateway_config_schema:AssistantGatewayConfig \
      --title 'assistant-gateway 配置项' --out docs/config/assistant-gateway.md

跑本模块测试(用 app 自带 venv,freshness 子进程才能找到 rtime_config):

    uv run --project apps/assistant-gateway --extra test python -m pytest apps/assistant-gateway/tests -q

看覆盖率(assistant-gateway 现已计入):

    uv run --all-packages python -m rtime_admin_core.coverage_doctor
