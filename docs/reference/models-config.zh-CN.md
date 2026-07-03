# models 配置模块使用参考

状态:已建(配置全覆盖批1·Lane B——把示例 models 模块换成真配置);K2 已加 provider 目录管理(增删/设默认/探测,§七)。代码:packages/rtime-admin-core/src/rtime_admin_core/schemas.py 的 ModelsConfig,经 registry.py:default_registry() 恒注册为模块 models;目录管理在 packages/rtime-models/src/rtime_models/manage.py。字段清单(机器生成)见 docs/config/models.md。设计依据:docs/design/config-full-coverage-plan-2026-07.zh-CN.md 二·批1;覆盖率守卫用法见 reference/config-coverage.zh-CN.md。

## 一、这个模块管什么
models 是"模型目录/路由"域(rtime-models 域)的单一可管理面。它把散在 claude-rtime 包裹脚本、飞书桥、assistant-gateway、rtime-models 装载器里的 env 承载点收编成一棵 schema 子树,让面板与配置Agent能读写、让覆盖率守卫能证明。分两层,刻意分开:

配置字段(schema)：env 驱动的路由/选型旋钮与 provider 密钥,就是本模块注册的字段。
provider 目录数据(非字段)：每个 provider 的 model id、别名、tier、能力,住在数据文件 packages/rtime-models/model-registry.json,由 rtime_models 装载器读。它是目录不是旋钮,不逐条铺成 schema 字段。两层的唯一桥是 model_registry_path——指向该数据文件的那个字段。

行为说明：本批只做注册与覆盖,不改任何模型路由的运行时行为。字段默认值等于包裹脚本/装载器现在用的兜底值,每个旧 env 名都经 AliasChoices 继续可载,所以将来把某个消费者接到 ConfigStore 是逐字节一致的迁移。

## 二、字段=env 还是 model-registry.json 数据
判断某个模型相关配置该查哪里:

env 承载(本模块字段)：改变"选哪个模型/走哪条路径/连哪个端点/用哪把密钥"的旋钮。它们随部署/实例而变,故为 env、进 schema、可被面板管。
model-registry.json 数据：模型身份本身——provider id/label/protocol、每个 model 的 id/cli_model/aliases/capabilities、tier→model 映射、per-provider secret_env_names。它是跨消费者共享的目录默认,随代码走,不随实例变;改它是改目录内容而非配一个实例。secret 值永不入该文件(只列 secret_env_names 的名字,值运行时从 env/keyfile 读)。

一句话:目录里是"有哪些模型、各自是什么"(数据);本模块字段是"这台/这次用哪个、连哪里、拿哪把钥匙"(配置)。

## 三、字段分组
完整表(字段名/env 名/类型/默认/是否秘密/生效档/说明)见 docs/config/models.md,此处只讲分组与要点。

3.1 路由/选型
default_model(DEFAULT_MODEL,热)：新请求默认路由到的别名或模型 id;空串=经包裹脚本默认(kimi-code)。唯一热切字段,下条消息即生效。
model_registry_path(RTIME_MODEL_REGISTRY)：provider 目录数据文件路径;None=用包内置默认。
model_aliases_json(MODEL_ALIASES_JSON)：飞书 /model 的别名覆盖表(内联 JSON);空=用 registry 分层别名兜底。
ustc_agent(RTIME_USTC_AGENT)：科大 provider 是否走 claude-ustc(经 LiteLLM 做 Anthropic→OpenAI 协议翻译)带工具的 agent 路径;RTIME_USTC_AGENT=0 退回无工具纯聊天直连。
ustc_agent_model(RTIME_USTC_AGENT_MODEL)、ollama_model(RTIME_OLLAMA_MODEL)：两条本地/科大路径的默认模型 id;None=用 registry 默认。

3.2 provider base_url(端点覆盖,registry/部署给默认)
ustc_base_url(RTIME_USTC_BASE_URL)、ollama_base_url(RTIME_OLLAMA_BASE_URL)、moonshot_base_url(RTIME_MOONSHOT_BASE_URL)、deepseek_anthropic_base_url(RTIME_DEEPSEEK_ANTHROPIC_BASE_URL)、qwen_anthropic_base_url(RTIME_QWEN_ANTHROPIC_BASE_URL)。
litellm_base_url(RTIME_LITELLM_BASE_URL)：LiteLLM 网关 base_url,由部署拓扑决定,故是 env 而非 registry 值;claude-ustc agent 路径经它翻译。None=用部署默认。

3.3 凭据(全部秘密,x-secret,值走 env/keyfile,面板只见 ***)
litellm_master_key(LITELLM_MASTER_KEY)、ustc_api_key(RTIME_USTC_API_KEY)、ustc_api_key_file(RTIME_USTC_API_KEY_FILE,秘密文件路径)、moonshot_api_key(RTIME_MOONSHOT_API_KEY/MOONSHOT_API_KEY/KIMI_API_KEY)、deepseek_api_key(RTIME_DEEPSEEK_API_KEY/DEEPSEEK_API_KEY)、qwen_api_key(RTIME_QWEN_API_KEY/QWEN_API_KEY/DASHSCOPE_API_KEY)、kimi_keyfile(CLAUDE_KIMI_KEYFILE,秘密文件路径)。
多别名的凭据(moonshot/deepseek/qwen)任一旧名都可载入同一字段;这是为保住历史 env 名不破。

## 四、生效档与写鉴权
生效档(x-reload):default_model 是 hot(热切,下条消息生效);其余端点/凭据/路径都是 restart(进程重启才重读),因为它们只在启动时读一次。
写鉴权(x-scope):每个字段都带 write:models,admin-api 的 scoped token 按此授权;秘密字段的 x-secret 走 admin-core 已建的脱敏(get_all/diff/audit 输出恒为 *** 或哈希,绝不明文)。

## 五、改这个模块时
加/改字段照 qq/web-chat 的样板:用 config_field/secret_field 声明 default/description/env_aliases(旧名全保)/reload/scope;秘密一律 secret_field。改完两步保持一致:
重生成字段文档:python -m rtime_config rtime_admin_core.schemas:ModelsConfig --title 'models 配置项' --out docs/config/models.md,并复核 diff。
跑守卫:uv run --all-packages pytest packages/rtime-admin-core/tests/test_models_config.py packages/rtime-admin-core/tests/test_config_coverage.py。前者校验默认/别名/秘密/文档新鲜度,后者校验覆盖率棘轮。
覆盖率账本(config-full-coverage-plan 顶部)在收编批合 main 后更新;数字下降视为回归。

## 六、与覆盖率守卫的关系
本批新登记的 env 别名共四个原在 allowlist(TODO-batch:models)的键:MODEL_ALIASES_JSON、RTIME_MODEL_REGISTRY、RTIME_MOONSHOT_BASE_URL、RTIME_USTC_API_KEY_FILE。注册后它们由 schema 覆盖,应在合并时从 coverage_allowlist.py 删去该四行(反向哨兵会抓仍读但仅 allowlist 的死项)。其余 provider 密钥/base_url/路由旋钮的 env 名并非以字面 os.getenv 出现在被扫描的 .py(散在非 .py 的 claude-rtime 包裹脚本或经 _read_secret 列表间接读),故不影响守卫计数,但登记进 schema 让面板可管、让未来接线有一致的 env 名契约。

## 七、K2:provider 目录管理(增删/设默认/探测)
目录数据(model-registry.json)的改动不再手编 JSON,走定式动词。代码:packages/rtime-models/src/rtime_models/manage.py(纯 stdlib);每个编辑动词先对合并结果跑完整校验(validate_registry,含 default_model 必须能解析到目录内的 id/别名),过了才原子写盘(tmp+rename)——编辑永远不会把文件写坏。

7.1 CLI(操作对象=RTIME_MODEL_REGISTRY 指的文件;没设=包内仓库默认文件)
```bash
python -m rtime_models validate                # 结构校验(原有)
python -m rtime_models probe [--provider ID] [--timeout 5] [--no-net]
                                               # 就绪探测:密钥env设了吗+endpoint活着吗,输出JSON
python -m rtime_models add-provider FILE       # FILE=一个provider JSON对象('-'=stdin);校验后原子写
python -m rtime_models remove-provider ID      # default_model还路由在该provider上时拒绝
python -m rtime_models set-default ID_OR_ALIAS # 改registry兜底默认(''=包裹脚本默认)
```
部署实例改目录:把 models.model_registry_path(RTIME_MODEL_REGISTRY)指到实例本地副本再编辑;仓库默认文件的改动走 git 评审(它是跨消费者共享默认,tests/test_rtime_models.py 钉住关键值)。

7.2 admin-api 端点(面板数据源;wiring 注入,rtime_models 不可导入时 501)
GET /v1/models/catalog(read):解析后的目录(设计上无密钥值)+ 当前生效路由默认(effective_default_model=配置字段 models.default_model)。**面板选默认写回走 PATCH /v1/config {"models.default_model": ...}**——与所有配置同一条写路径(ETag/x-scope/审计/两段式一致),不开第二写口。
GET /v1/models/probe?provider=&timeout=&check_url=(read):逐 provider 就绪灯。只报"密钥是否已设"(布尔+env名)从不读值;发裸 GET,任何 HTTP 状态(401/404 也算)都证明 endpoint 活;URL 全来自目录文件,无 SSRF 面;?check_url=0 只查密钥不碰网。

7.3 探测语义
secret_present:secret_env_names 任一已设(*_FILE/*KEYFILE 名还要求文件真存在,否则记入 keyfile_missing);无密钥要求的 provider 为 null。reachable:null=没得测(无 base_url 或 check_url=0)。真机验证(2026-07-03):8 provider 全探测通过,6 个有端点的全部证活(moonshot/ustc/kimi 404、deepseek 401、ollama 200)。

测试:tests/test_rtime_models.py(manage 全动词+探测含本地 HTTP 真连)+ packages/rtime-admin-api/tests/test_models_endpoints.py(501/鉴权/透传/404)。
