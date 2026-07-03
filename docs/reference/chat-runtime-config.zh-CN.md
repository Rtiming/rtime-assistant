# chat-runtime 配置项参考(ChatRuntimeConfig)

设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md(§二 批 3 · rtime-chat-runtime)。

状态:已建(配置全覆盖 批 3 · coverage-sweep)。rtime-chat-runtime 包唯一的**直接字面量** env 旋钮(RTIME_CAMPUS_URLS_FILE)已表达为 schema 驱动的 pydantic-settings 模型 ChatRuntimeConfig,并注册进 admin-core registry 的 chat-runtime 模块,面板/配置 Agent 可管理(全覆盖)。本次为收编+注册,包运行时零变更。

## 一、代码位置与真相源

字段真相源:packages/rtime-admin-core/src/rtime_admin_core/schemas.py 的 ChatRuntimeConfig(RtimeBaseSettings),经 registry.py:default_registry() 恒注册为模块 chat-runtime。

为何 schema 归 admin-core(不在 rtime-chat-runtime 里):packages/rtime-chat-runtime 是刻意 dependencies=[] 的纯运行时叶子(五个渠道无关原语:run_log / run_control / access_policy / chat_queue / attachment_directives),不引入 pydantic。故其唯一直接读的 env 旋钮的 schema 由 admin-core 自有——与 library-gateway 同一决策(零依赖叶子的 schema 归这里)。注册后面板可管理、覆盖率守卫可证,不给 rtime-chat-runtime 加依赖、不改其运行时取值链(campus_urls.load_campus_urls 仍裸读 env),行为零变更。

自动生成的字段表:docs/config/chat-runtime.md(golden 测试 packages/rtime-admin-core/tests/test_chat_runtime_config.py 守其不漂移)。

## 二、字段一览

一个字段:campus_urls_file(RTIME_CAMPUS_URLS_FILE),str/null,默认 null,x-scope=write:channel,生效 hot。校园服务 URL 表覆盖/扩展文件(JSON)路径;None/空=用内置表。hot:campus 意图命中即按 mtime 重读(campus_urls.load_campus_urls 有 mtime 缓存)。

## 三、只收编"直接字面量读"的 env;其余为何不在这里

rtime-chat-runtime 还读别的 env,但它们不属于本模块:

一、RTIME_ASSISTANT_RUN_LOG(run_log.py):部署注入的运行日志路径,跨切面共享,由部署 owns —— 留 allowlist(deploy-path),不注册。

二、tool_policy.py 读的 read_only_env / personal_library_env:是**注入的 env 名**(ToolPolicy 的实例属性,如 QQ_READ_ONLY / QQ_PERSONAL_LIBRARY,由渠道入口在构造 ToolPolicy 时传入),不是本模块写死的字面量;那些 env 名归各渠道配置(qq / feishu)owns。本库只按入口给的名去读,不该重复注册。这也是覆盖率守卫的 AST 扫描看不到它们的原因(os.getenv(self.read_only_env) 的 key 是变量不是字面量)。

## 四、生成与校验命令

生成字段表(改 schema 后必须重跑并复核 diff):

    uv run --all-packages python -m rtime_config rtime_admin_core.schemas:ChatRuntimeConfig --title 'chat-runtime 配置项' --out docs/config/chat-runtime.md

跑本模块测试:

    uv run --all-packages python -m pytest packages/rtime-admin-core/tests/test_chat_runtime_config.py -q
