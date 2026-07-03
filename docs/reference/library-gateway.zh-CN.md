# library-gateway 使用参考(scope 强制模型 + 网关配置字段)

状态:2026-07-02。对应代码 packages/rtime-library-gateway/(gate.py / mcp_server.py / dispatch.py)与 packages/rtime-admin-core/src/rtime_admin_core/schemas.py(LibraryGatewayConfig)。设计依据:
docs/design/p5-impl-scoped-encryption-2026-07.zh-CN.md(P5 加密与阶段0硬化)、
docs/design/library-sharing-multitenant-2026-07.zh-CN.md(scope/共享模型)、
docs/design/index-settings-adjustment-2026-07.zh-CN.md(索引设置旋钮)。

本文只讲「怎么用/字段含义/scope 强制怎么落」;为什么这么设计见上面三份设计稿。运维用文档(单元/端口/部署)见 docs/rtime-library-gateway.md。

---

## 一、网关是什么

rtime-library-gateway 是 brain 库读写的单一权限门 + 元数据审计。每个方法调用(允许或拒绝)先过 gate.py `enforce`,再 dispatch 到后端 CLI,输出经 redact 脱敏,最后写一行仅含元数据的审计。现网跑两个进程,同一份代码、同一 brain root、同一索引,靠进程/端口/policy 隔离:

- owner 全开 127.0.0.1:8780(library-gateway-policy.json,allowed_path_prefixes=[]=全库、redact 关、写开)。
- 学生会 scoped 只读 127.0.0.1:8781(studentunion-policy.json,scope=['knowledge/institutions/ustc']、excluded=['personal-data','profile']、redact 开、写三重拒)。

身份来自「连到哪个进程/端口 + 那个进程加载的 policy」,不来自自报的 MCP client 名(未认证)。

---

## 二、scope 强制模型(READ 层)

scope = policy 里的 `allowed_path_prefixes`(brain 相对子树前缀列表)。空/缺失 = scope 关(全库,单 owner 默认);非空 = 把每个读收窄到这些子树。核心在 gate.py `_apply_read_scope`。

分四层,从主边界到纵深兜底:

1. **path_prefix LIKE 下推(主边界)**。可枚举读(lib.search/lib.recent 的 path_prefix、lib.tree 的 path、lib.list 的 root):省略子树参数时,单前缀注入、多前缀要求调用方显式挑一个;传了就校验必须落在 scope 内;边界相等的前缀补尾斜杠钉死 LIKE(防兄弟同名目录 `activities` 命中 `activities-2026`)。注入后 path_prefix 变成 SQL `path LIKE 'prefix/%'`,scope 外的行**在索引层就不进结果**(不是拉回后过滤)。取路径的读(lib.read/lib.stat)目标在 scope 外直接 PolicyDenied。

   **不可收窄的读一律拒(不接受诱饵 path,对抗审查 MEDIUM #1)**。一个读方法能被 scope 收窄,当且仅当它的后端 builder **真正消费**某个 brain-path 参数来约束读取(gate.py `SCOPE_CONSTRAINABLE_KEYS`,与 dispatch `_build_*` 静态对齐、有一致性测试)。builder 忽略 path 的全库聚合方法(lib.freshness/courses/context/profile/automation/hub/jobs.*)在非空 scope 下**直接 DENY**——给它们一个"看起来在 scope 内"的诱饵 `path` 也不放行,因为那 path 根本不被消费、方法仍会跑全库(否则 freshness 会回全库 document_count 含 personal-data、泄露越界目录名)。这让 scope 成为**自立门户**的完整约束层,不依赖 client allow-list 兜底。可收窄的方法(lib.read/stat/docpack/review/runtime 消费 path;lib.meta/citation 消费 root)必须带一个 in-scope 的消费键才放行。owner(空 scope)不受影响。

2. **index-reject(P5 阶段0 / H1,代码级强制)**。非空 scope 下,**拒绝任何调用方自带的 `index` 参数**(抛 PolicyDenied),强制网关用自己 server-side 的 `default_index()`。原因:path_prefix 是 scope 的唯一 backstop,而它跟着「查哪个索引」走;`index` 刻意不在 PATH_LIKE_KEYS(它是 brain root 之外的派生缓存),没有别的检查管它。一个能触到全库索引文件的 scoped 消费者若能把 `index` 重指过去,就绕开 path_prefix 读全库。此检查在 `_apply_read_scope` 进入后、`SCOPE_EXEMPT_METHODS` 早退**之前**运行,所以 lib.get(同样收 `index`)也被覆盖。空 scope(owner)不受影响,照旧可命名索引。

3. **纵深结果过滤(belt-and-suspenders)**。除下推的 LIKE 外,mcp_server.py `_scope_filter_results` 对 search/recent 的返回行再做一道 O(返回行数) 的路径前缀二次收窄:丢弃任何路径不在 `allowed_path_prefixes` 内的行。它独立于 `hide_excluded_in_results`(后者只丢 excluded 顶层目录如 personal-data;纵深过滤丢**任何** scope 外行)。它是纵深、不是主边界——主边界仍是索引层 LIKE 下推;这一层只在「注入漂移 / 后端 bug / 未来漏了下推的代码路径」时兜底一个误返回的行。见「四、速度/准确度」。

4. **hide_excluded_in_results(顶层目录隐藏)**。丢掉顶层目录在 excluded_top_dirs 里的行(personal-data/profile),让 agent 连它们的路径/标题都看不到。

写方法不过 scope(scoped 部署经 policy `default_write:deny` + client deny globs + 非空 allow-list 三重拒写,是更强的切法)。

### 策略解析 fail-closed(对抗审查 HIGH #2)

`gate.load_policy()` 对**显式命名的策略**(env `RTIME_LIBRARY_GATEWAY_POLICY`)必须 fail-closed:该文件缺失/不可读/坏 JSON/非 JSON 对象时**直接 raise `GateError`**,绝不静默回退到更宽的默认。原因:8781 公开网关显式指向 studentunion-policy.json;若它被改坏,静默降级到全库 owner 默认(空 `allowed_path_prefixes`)= 服务全库含 personal-data,是隐私 fail-open。通用原则:显式配置的安全策略加载失败=致命,不得降级放宽。候选回退链(repo 默认 → 内置 `_DEFAULT_POLICY`)**只在完全未命名任何策略**(单 owner 零配置)时才允许。`invoke` 把这个 `GateError` 转成干净的 `ToolError`(拒绝该调用),不让坏策略崩溃循环或服务全库。

### scope-exempt 方法与 lib.get 收敛(H2)

SCOPE_EXEMPT_METHODS(lib.doctor/policy/status/preview/audit/get)是自描述/元数据面,scope 下仍可调。其中 **lib.get** = `index status`,原始结果含全库聚合(document_count/fts_count/vector_count)、brain 根路径(root)、整个 meta blob——对 scoped 消费者是全库聚合面泄露。

收敛做法(mcp_server.py `_scope_trim_get`):**保留豁免**(scoped 消费者可查索引 liveness),但非空 scope 下**裁剪**结果——去掉 root / document_count / fts_count / vector_count / meta;保留 ok / index / schema_version / tokenizer / created_at / embed_model / embed_dim / has_vectors(够回答「索引在不在、新不新、有没有向量」)。owner(空 scope)拿完整聚合不变。

### 声明层纵深:scoped 容器不 bind 全量索引

代码门之外,声明层再兜一道:scoped 消费者(qq-bridge)**不得** bind 全量索引 sqlite 进容器——库访问全走它的 scope 网关。若 bind 了(哪怕只读),容器内任意进程能直接开 sqlite 跑 SQL 读 personal-data,绕过 allowed_path_prefixes。已从 compose.prod.yml 删除 qq-bridge 的 `/brain-index` bind(web-chat 本就无此 bind,是模板),并加 compose 可移植性断言测试(test_scoped_consumer_does_not_bind_full_index_in_compose)。这是纵深防御,不替代代码级 index-reject。

---

## 三、scope 内容仍完整(不降召回/精度)

阶段0 硬化**不改变 scope 内的检索行为**:一个 scoped 搜索仍只返回 scope 内的行,且返回的就是原来那批行。

- index-reject 是纯参数检查(免费,不碰查询)。
- 纵深结果过滤是对**已被 LIKE 下推收窄**的小结果集(默认 limit=10)做路径前缀检查,不是重扫索引——scoped 搜索的 SQL 仍是 `path LIKE 'prefix/%'` 下推,过滤是事后、在小结果集上,只会「多余地」丢掉一个被误返回的越界行(正常路径下无行可丢)。所以 scope 内召回/精度不变、延迟无可测增量。

---

## 四、速度/准确度门(owner 硬约束)

- 同 scope 延迟:lib.search 的 p50/p95 不劣于基线(scoped 查同库同 scope 应更快或持平)。index-reject 免费;纵深过滤 O(返回行数)。
- recall@k/precision@k 不劣于基线:过滤只去 scope 外候选,绝不重排 scope 内结果。
- 不下推红线:任何维度必须是索引下推谓词或「选哪个索引」,「先拉全量再应用层过滤」判失败。纵深结果过滤不违反此红线——它作用在已下推收窄的结果集上,是安全冗余,不是主过滤。

---

## 五、网关配置字段(收编模型 library-gateway)

真实收编模型 = packages/rtime-admin-core/src/rtime_admin_core/schemas.py `LibraryGatewayConfig`,注册在 admin-core registry 的 `library-gateway` 模块下(经 `register_library_gateway_module`,`default_registry` 默认已含,对所有 caller 有效)。它覆盖网关进程实际读取的每个 env(gate.py / mcp_server.py / dispatch.py,以及经 dispatch `_env_for` 传给 indexer 子进程与进程内 warm 检索器的嵌入模型 env)。之前这些是网关内裸 `os.environ.get`、样例未接线;收编后进 admin-core registry,由覆盖率守卫机械保证不回落(见 docs/reference/config-coverage.zh-CN.md)。

字段(点路径 `library-gateway.<field>`;x-env-aliases 保全旧 env 名):

| 字段 | env 别名 | 默认 | reload | 说明 |
|---|---|---|---|---|
| http_host | RTIME_LIBRARY_GATEWAY_HTTP_HOST | 127.0.0.1 | restart | HTTP 绑定地址;仅回环,勿 0.0.0.0 |
| http_port | RTIME_LIBRARY_GATEWAY_HTTP_PORT | 8780 | restart | 端口 1..65535(owner=8780、学生会=8781);仅当 env 非空才走 HTTP 传输 |
| socket_path | RTIME_LIBRARY_GATEWAY_SOCKET | None | restart | unix socket(常驻 warm 守护);HTTP 优先,都不设走 stdio |
| idle_timeout | RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT | 1800 | **hot** | stdio stdin 空闲超时(秒),防半开 ssh 泄漏 mcp 进程;<=0 关 |
| prewarm | RTIME_LIBRARY_GATEWAY_PREWARM | **True** | restart | 启动预热 jieba+embedder;默认开,0/false/no/off 关(见下「漂移修」) |
| policy_path | RTIME_LIBRARY_GATEWAY_POLICY | None | restart | 策略 JSON;None=内置默认(owner 全开);scoped 实例指 studentunion-policy.json |
| audit_log | RTIME_LIBRARY_GATEWAY_AUDIT_LOG | None | restart | 审计 JSONL;None=用 policy 里的;两进程须分流 |
| index_path | BRAIN_LIBRARY_INDEX / RTIME_LIBRARY_GATEWAY_INDEX | None | restart | 索引 sqlite(显式最高优先);None=default_index() 探测 |
| brain_root | BRAIN_ROOT / RTIME_BRAIN_ROOT | None | restart | 库根;None=候选探测 |
| hub_root | RTIME_HUB_ROOT | None | restart | rtime-hub 事实库根 |
| reminders_path | RTIME_REMINDERS_PATH | None | restart | 提醒 JSONL 路径 |
| embed_model | BRAIN_LIBRARY_EMBED_MODEL | None | restart | 嵌入模型 key;None=默认 bge-small;切模型须全量重 embed |
| embed_model_dir | BRAIN_LIBRARY_EMBED_MODEL_DIR | None | restart | 本地 ONNX 模型目录;None=不启用向量(纯 BM25) |

所有字段 x-scope = `write:library`(改网关运维旋钮不解密、不读库内容)。索引本身无密钥,此模型无 x-secret 字段(P5 加密的 DEK 另论,见 p5-impl)。

注意事项(与设计稿一致):
- owner wrapper **从不设** RTIME_LIBRARY_GATEWAY_POLICY(走内置默认策略);改 wrapper 设策略会静默改 owner 行为。
- default_index() 逻辑在三处重复(dispatch.py / rtime_jobs/handlers.py / scripts/rebuild-brain-index.sh),收编时应收敛到单一解析器(留问,index-settings §四)。
- embed_model / embed_model_dir 由 indexer 子进程 + 进程内 warm 检索器读;网关本身不直接用,但它俩决定检索是否带向量,故登记进网关域。

### prewarm 默认漂移修(schema == 运行时)

老代码只在 `os.environ.get('RTIME_LIBRARY_GATEWAY_PREWARM','1') == '0'` 时关——裸字符串等值:只有字面 `'0'` 生效,`false`/`False`/`no`/`off` 全都静默保持 ON(板子内存紧,每 prewarm 钉一份 ONNX embedder,是坑)。且样例 schema 默认写成 False,与运行时真实默认(ON)漂移。

修法:
- 运行时 `mcp_server._maybe_prewarm` 改用共享 `_env_bool("RTIME_LIBRARY_GATEWAY_PREWARM", "1")`({1,true,yes,on}=真,与 apps/assistant-gateway/_common.py env_bool 同语义),识别 0/false/no/off 为关,unset 默认 ON。
- schema `prewarm` 默认改为 `True`,与运行时真实默认对齐(无漂移)。
- 测试钉死:`test_prewarm_default_on_and_falsy_values_disable`(default ON + 各 falsy 值关)、`test_prewarm_schema_default_matches_runtime_default`(schema 默认 == 运行时默认)、`test_env_bool_truthy_and_falsy`。

## redact_student_pii:在校学生 PII 输出层脱敏开关(A3 决策1)

policy 字段 `redact_student_pii`(布尔,默认 **False**)。控制网关**输出层**对在校学生
PII 的 INLINE token 脱敏——与 `redact_sensitive` 的整行替换不同,这里只抹匹配 token,
答案骨架(姓名、学院、公开任职)保留可读。

抹除模式(`gate.STUDENT_PII_SUBS`,CJK 安全的非字母数字环视):
- 学号(两位字母+8位数字,如 PB00000001)、18位身份证、中国大陆手机号、邮箱 → `***`
- `政治面貌：X` → `政治面貌：***`(只抹值)

语义与用法:
- **默认 False = 内测阶段完全开放**(应请求可查同学,行为不变);
- 翻 **True** = 收紧姿态(对外/studentunion 实例):在 policy 文件加
  `"redact_student_pii": true`,或未来经管理面板翻开。**网关层控制,不靠模型提示词**
  (owner 明确要求),面板可管(lib.policy 报告已含该字段)。
- 与 `redact_sensitive` 独立:两个开关各管各的,不互相吞没。

设计依据:docs/design/a3-studentunion-usage-findings-2026-07.zh-CN.md §四.1。
测试:tests/test_rtime_library_gateway_gate.py(inline 脱敏/独立性/JSON/默认关)。
