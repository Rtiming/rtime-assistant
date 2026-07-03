# QQ 掉线自愈守护配置项参考(QQSelfhealConfig)

设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md(§二 批 2 · qq-selfheal)。

状态:已建(配置全覆盖 批 3 · coverage-sweep)。QQ 掉线自愈运维守护(apps/qq-bridge/ops/qq_selfheal.py)的 SELFHEAL_* 配置面已表达为 schema 驱动的 pydantic-settings 模型 QQSelfhealConfig,并注册进 admin-core registry 的 qq-selfheal 模块,面板/配置 Agent 可管理(全覆盖)。本次为收编+注册,守护运行时零变更(见下"为何独立模块")。

## 一、代码位置与真相源

字段真相源:apps/qq-bridge/qq_bridge/selfheal_config.py 的 QQSelfhealConfig(RtimeBaseSettings)。

为何是独立模块(不在 qq_selfheal.py 里):掉线自愈守护跑在系统 python(/usr/bin/python3,见 ops/qq-selfheal.service),刻意只用 stdlib + 系统 curl/docker(不引入第三方 Python 依赖),把"人工 SSH 取码"降级成"飞书里扫一下"。在守护里 import pydantic 会破坏这个部署契约。所以守护仍保留自己的纯 stdlib Config 类直接读 env,这份 schema 只按同一套 env 名镜像那些默认值,让覆盖率守卫把它们算作已覆盖,而不碰守护的热路径。这与 library-gateway 同构(零依赖运行时叶子,schema 归别处 owns):注册对行为中立,运行时未动。

注册:packages/rtime-admin-core/src/rtime_admin_core/registry.py 的 register_qq_selfheal_module(registry) 与 default_registry(include_qq_selfheal=...);从 qq_bridge.selfheal_config 懒加载,admin-core 不硬依赖 app(叶子性保持)。

自动生成的字段表:docs/config/qq-selfheal.md(由 python -m rtime_config qq_bridge.selfheal_config:QQSelfhealConfig 生成,golden 测试 apps/qq-bridge/tests/test_selfheal_config_schema.py 守其不漂移)。

## 二、字段一览

完整表(字段名/env 名/类型/默认/是否秘密/生效档/说明)见 docs/config/qq-selfheal.md。要点:

一、11 个字段:10 个 SELFHEAL_* 运维旋钮(状态端点/容器名/二维码路径/轮询防抖冷却/按需补码触发文件)+ owner 飞书 open_id。全部 x-scope=write:channel。

二、密钥:feishu_owner_open_id(FEISHU_OWNER_OPEN_ID)标 x-secret——它是 PII(不入 handoff/日志),面板/API 只见 ***。空 => 只重启不投递。

三、生效档:poll_seconds、qr_request_check_seconds 标 hot(守护主循环每轮读的语义;当前守护启动时读一次 Config,面板管理时按 hot 生效),其余 restart。

## 三、共享名不重复注册(避免双 own)

守护还读三个不属于本模块的 env,已由别的模块 own,故本模块**不重复注册**:

- FEISHU_APP_ID / FEISHU_APP_SECRET / FEISHU_CONFIG_JSON —— 由 feishu 模块(FeishuBridgeConfig)own,守护复用飞书桥同一份凭据。
- QQ_ONEBOT_ACCESS_TOKEN —— 由 qq 模块(QQBridgeConfig.access_token)own,守护复用 OneBot 控制端 token。

覆盖率守卫因此把这几个名计在 feishu/qq 模块名下,守护对它们的读被正确归属,不算未覆盖。

## 四、向后兼容与行为保持

一、守护运行时零变更:ops/qq_selfheal.py 的 Config 类未动,仍直接 os.getenv 读 SELFHEAL_*/FEISHU_OWNER_OPEN_ID,仍 stdlib-only。本 schema 只是注册/覆盖镜像,守护从不 import 它。

二、默认值逐字一致:每字段默认 == 守护 os.getenv(..., DEFAULT) 的 DEFAULT;golden 测试对表校验(漂移即红),避免面板默认误报。

三、部署:ops/qqbridge.env.example 的 SELFHEAL_* 段不变;systemd EnvironmentFile 复用 qqbridge.env。

## 五、生成与校验命令

生成字段表(改 schema 后必须重跑并复核 diff,须在 app 目录内跑,qq_bridge 才可导入):

    cd apps/qq-bridge && uv run --extra test python -m rtime_config qq_bridge.selfheal_config:QQSelfhealConfig --title 'qq-selfheal 配置项' --out ../../docs/config/qq-selfheal.md

跑本模块测试:

    uv run --project apps/qq-bridge --extra test python -m pytest apps/qq-bridge/tests/test_selfheal_config_schema.py -q

看覆盖率(qq-selfheal 现已计入):

    uv run --all-packages python -m rtime_admin_core.coverage_doctor
