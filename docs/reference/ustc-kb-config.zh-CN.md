# ustc-kb 抓取器配置项参考(UstcKbConfig)

设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md(§二 批 3 · ustc-kb)。

状态:已建(配置全覆盖 批 3 · coverage-sweep)。科大公开资料抓取器(packages/ustc-kb)的 USTC_KB_* 配置面已表达为 schema 驱动的 pydantic-settings 模型 UstcKbConfig,并注册进 admin-core registry 的 ustc-kb 模块,面板/配置 Agent 可管理(全覆盖)。本次为收编+注册,抓取器运行时零变更。

## 一、代码位置与真相源

字段真相源:packages/ustc-kb/src/ustc_kb/config_schema.py 的 UstcKbConfig(RtimeBaseSettings)。

为何是独立模块(不在 config.py 里):ustc_kb.config 是一个导入即算派生路径常量的模块,供一个不依赖 admin-core 栈的独立确定性抓取器使用;保持它 stdlib-only 避免给每次 ustc-kb 调用强加 pydantic。admin-core 懒加载 config_schema 里的 UstcKbConfig 来注册(照 qq/web-chat 的叶子导入样板),抓取器运行时(ustc_kb.config)不动。注册对行为中立。

注册:packages/rtime-admin-core/src/rtime_admin_core/registry.py 的 register_ustc_kb_module(registry) 与 default_registry(include_ustc_kb=...);从 ustc_kb.config_schema 懒加载,admin-core 不硬依赖它(叶子性保持)。config_schema 依赖 rtime-config(pydantic-settings 基类),经 ustc-kb pyproject 的 [tool.uv.sources] workspace 源可编辑装入——抓取器 runtime 的 config.py 仍只用 stdlib。

自动生成的字段表:docs/config/ustc-kb.md(由 python -m rtime_config ustc_kb.config_schema:UstcKbConfig 生成,golden 测试 packages/ustc-kb/tests/test_config_schema.py 守其不漂移)。

## 二、字段一览

完整表见 docs/config/ustc-kb.md。三个字段,全部 x-scope=write:library,无密钥(登录密码交互输入,从不入 env):

一、data_root(USTC_KB_DATA):抓取产物根目录(原始 HTML/文件/笔记/索引/台账),默认 ~/Desktop/ustc-kb-data,在仓库外避免 git 膨胀;~ 展开。config.py 导入时据此派生所有子目录,故 restart。

二、workers(USTC_KB_WORKERS):抓取并发数(I/O 密集,并发提速),默认 8。restart。

三、today(USTC_KB_TODAY):入库日期(脚本环境无 Date.now,显式给),默认 2026-06-20。restart。

## 三、向后兼容与行为保持

一、抓取器运行时零变更:ustc_kb/config.py 与 crawl.py 未动,仍直接 os.environ.get 读 USTC_KB_*。本 schema 只是注册/覆盖镜像。

二、默认值逐字一致:每字段默认 == 抓取器 os.environ.get(..., DEFAULT) 的 DEFAULT(DATA_ROOT 的 ~/Desktop/ustc-kb-data、DEFAULT_WORKERS=8、TODAY=2026-06-20);golden 测试对表校验,漂移即红。

## 四、生成与校验命令

生成字段表(改 schema 后必须重跑并复核 diff):

    uv run --all-packages python -m rtime_config ustc_kb.config_schema:UstcKbConfig --title 'ustc-kb 配置项' --out docs/config/ustc-kb.md

跑本模块测试:

    uv run --all-packages python -m pytest packages/ustc-kb/tests -q

看覆盖率(ustc-kb 现已计入):

    uv run --all-packages python -m rtime_admin_core.coverage_doctor
