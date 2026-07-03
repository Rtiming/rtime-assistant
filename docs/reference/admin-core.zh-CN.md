# admin-core 使用参考(L1 管理核心库)

状态:已建(合 main,对抗复审 13 缺陷全修)。代码:packages/rtime-admin-core/。
设计依据:docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §六、
docs/design/config-full-coverage-plan-2026-07.zh-CN.md。

面向对象:给"要新增一个配置模块"的开发者,以及要直接调 ConfigStore 的上层
(admin-api、profile 消费链)。admin-core 是纯 Python 库,不含 HTTP/网络;它是 L2
HTTP API、CLI、面板共同依赖的唯一配置真相层。L0 元数据词汇在 packages/rtime-config
(config_field/secret_field/Reload),L1 在本包。

## 一、四层优先级(env > store > profile > default)

读取时解析,自高到低:env(只读 overlay,secrets 与运维强制项)> store(面板/API 写
入的稀疏覆盖层)> profile(git 声明层,守护进程只读)> default(schema 默认)。

层名常量(store.py:88-91):

    PROV_ENV = "env"
    PROV_STORE = "store"
    PROV_PROFILE = "profile"
    PROV_DEFAULT = "default"

解析次序见 ConfigStore.get(store.py:240):先查 env,命中即返回;再查 store(config
或 secrets);再查 profile 层;最后落 schema 默认。哨兵 _NO_ENV(store.py:85)区分
"env 里没有这个键"与"env 里这个键是 None"。store 层稀疏(只存显式 set 过的键,
unset≠set-to-default),这样 git 推送的 profile 更新能流过面板从未碰过的所有键,
面板微调又能在每次 pull 后存活。profile 层由 profile loader 编译产出、构造时注入,
只读,守护进程绝不回写(git 是其唯一写者)。

字段归属可查询:ConfigStore.provenance(path)(store.py:255)返回该 path 由哪层胜出
(env/store/profile/default)。

## 二、ConfigStore 公共 API

类:rtime_admin_core.store.ConfigStore。所有写方法都要求调用方注入 ts 与 snapshot_id
(core 保持确定性,时间与 id 由上层给),并返回 ApplyResult(store.py:104,含
snapshot_id/changed/hot/restart_required/diff)。

读:
- get(path) -> Any(store.py:240):按四层优先级解析单个 path。
- provenance(path) -> str(store.py:255):返回胜出层名(见上)。
- get_all(*, redact=True, provenance=False) -> dict(store.py:271):全量解析的
  {path: value};redact=True 时 secret 掩码;provenance=True 时每个值变成
  {"value": v, "provenance": <层>}。
- persisted_flat() -> dict(store.py:335):仅 PERSISTED 层(config+secrets store,
  不含 env 与 schema 默认)的扁平 {module.field: value}。ETag 就是对它算的。
- diff(new, *, redact=True) -> dict(store.py:409):对一组提议的扁平改动返回
  {path: {"before":…, "after":…}}。
- profile_layer(property, store.py:878):当前 profile 层的只读副本。

校验:
- validate(partial) -> list(store.py:379):把扁平 {path: value} 合到"被引用模块的
  PERSISTED 状态"上校验(不 env-merge、不校验全部模块),返回 FieldError 列表(空=通过)。

写(事务):
- set(path, value, **apply_kwargs) -> ApplyResult(store.py:427):单键 apply 便捷封装。
- apply(changes, *, ts, snapshot_id, actor="system", source="core", note=None) ->
  ApplyResult(store.py:432):扁平 {path: value} 事务应用。步骤:①对被引用模块校验 →
  ②快照当前 persisted 状态入 history(有上限)→ ③原子写(secrets 入 secret store,
  其余入 config)→ ④写一条审计(diff 已脱敏)→ ⑤返回受影响 path,按 hot/restart 分区。
- unset(path, *, ts, snapshot_id, …) -> ApplyResult(store.py:701):清 store 覆盖,
  值落回下一层(profile,否则 schema 默认)。这是 K8s SSA 式的"所有权移交"动词。
- snapshot(snapshot_id, ts, *, note=None) -> str(store.py:602):显式打快照。
- rollback(snapshot_id, *, ts, new_snapshot_id, actor="system", source="core") ->
  ApplyResult(store.py:640):恢复某快照的 persisted 状态;回滚前先把当前状态存进
  new_snapshot_id(所以回滚可逆),再恢复,再审计。
- reload_profile(new_layer, *, ts, snapshot_id, …) -> ApplyResult(store.py:883):
  整份 profile 层原子 validate-then-swap(Caddy /load 语义):新层与当前 store 视图
  合并后校验,失败则保持旧层生效并抛错(绝不半套);成功则先快照、原子替换、写一条
  action="profile_reload" 的审计。

回滚/漂移辅助:
- list_history() -> list(store.py:615):快照描述(id/ts/note,无 payload),最新在前。
- rollback_changed_paths(snapshot_id) -> list(store.py:620):纯预览,回滚会改哪些
  persisted path(不写、不快照、不审计)。
- drift_report() -> list(store.py:1065):store 覆盖值 ≠ profile 值的"被遮蔽键"清单,
  每条 {path, store, profile, secret}。面板/doctor 用来暴露运行时归属冲突。

## 三、Registry:新增一个配置模块(照 qq 样板)

一个模块 = 一个 pydantic-settings 模型 = 一份 JSON Schema = 面板里的一棵子树。模块名是
dotted-path 顶段(如 qq、models),不含点。

Registry API(registry.py):
- register(module, model)(registry.py:34):把模型注册到模块名下;重复注册报错;
  模块名须非空且不含点。
- list_modules() -> list(registry.py:50)、has(module)(registry.py:54)、
  model(module)(registry.py:57)、get_schema(module)(registry.py:67,用
  model_json_schema(by_alias=False),property key 恒为 Python 字段名)。

qq 注册样板(registry.py:83-95)——新模块照抄这个模式:

    _QQ_MODULE = "qq"

    def register_qq_module(registry, *, module=_QQ_MODULE):
        from qq_bridge.config import QQBridgeConfig   # 惰性 import:app 不是 admin-core 的依赖
        registry.register(module, QQBridgeConfig)

关键点:admin-core 保持"叶子",绝不硬依赖任何 app;所以模块模型定义在 app/包里,
注册时惰性 import(app 不可导入则抛 ModuleNotFoundError,由调用方决定软/硬依赖)。
default_registry(include_qq=False)(registry.py:98)预装三个样例模块;include_qq=True
时额外注册真实 qq 模块。

新增模块的最小步骤:
1. 在该模块所属 app/包里建一个 RtimeBaseSettings 子类,每字段用 config_field /
   secret_field 声明(见下节)。
2. 写一个 register_<module>_module(registry) 函数,惰性 import + registry.register。
3. 面板与 admin-api 零改动即出现新子树与自动表单(schema 驱动)。
4. 过配置覆盖率守卫(见 config-coverage.zh-CN.md):新字段的 env 名要么被 schema 接受,
   要么进 allowlist。

## 四、字段元数据:x-secret / x-reload / x-scope

L0 词汇在 packages/rtime-config/src/rtime_config/fields.py。用 config_field / secret_field
声明字段,元数据落进 json_schema_extra,随 model_json_schema() 出到所有下游(docs、
admin-api、面板)。

- x-secret(bool):该值是凭据。secret_field(fields.py:138)自动打上。作用:get_all /
  diff / audit / 校验错误里一律脱敏;值走 env/keyfile,面板/API 只见掩码。是可靠脱敏的
  结构性前提(覆盖率守卫硬断言每个 secret_field 都带 x-secret)。
- x-reload(str,取值 "hot" | "restart",默认 "restart"):hot=可热应用免重启;
  restart=进程须重启才生效。枚举 Reload.HOT/Reload.RESTART(fields.py:40)。apply/
  rollback 用它把改动分成 hot 与 restart_required 两组(store.py:_classify_reload)。
  默认 restart 因为审计发现约 220 个配置点都是启动读一次;真正热的(keyfile、campus
  URL 表)显式标 hot。
- x-scope(str | None,如 "write:models"):可选的 admin-api 写鉴权 scope;省略=模块级
  scope。admin-core 自身不做鉴权,只存元数据;L2(admin-api)按它做每字段写鉴权。

元数据解析:field_meta(registry, path) -> FieldMeta(metadata.py:62),FieldMeta
(metadata.py:25)含 module/field/path/secret/reload/scope。另有 is_secret(registry, path)
(metadata.py:76)、secret_paths(registry)。

旧 env 名兼容:config_field/secret_field 的 env_aliases=[...] 参数(fields.py:19-24)
是"旧名不破"的唯一申报点——它同时写进字段 validation_alias(AliasChoices,所有旧名都
继续能载值)与 x-env-aliases(供 docs 与覆盖率守卫)。golden schema 测试守护这点。

## 五、快照 / 回滚 / history

- Snapshot(history.py:26):id/ts/config/secrets/note,config、secrets 是嵌套
  {module: {field: value}} 深拷贝。to_meta() 给 list_history 用(无 payload)。
- HistoryStore 协议(history.py:43):add/get/list(旧→新)/prune(返回被丢弃的 id)。
  实现:InMemoryHistory(测试)、FileHistory(每快照一个 JSON 文件,名
  <ts>__<seq>__<id>.json,seq 单调防时间戳撞车)。
- 每次 apply/rollback/unset/reload_profile 前都会内部 _take_snapshot(store.py:606)。
- max_history(ConfigStore.__init__,store.py:132)最小为 1(最近一次快照必须能立刻回滚);
  每次快照后 prune。
- 回滚可逆:rollback 先把当前状态存进 new_snapshot_id 再恢复目标快照。

## 六、审计(仅元数据 + 脱敏 diff)

- AuditEntry(audit.py:37):ts(ISO-8601,调用方注入)/actor(token_id / 用户名 /
  "system")/source(http | mcp | cli | panel | test)/action(apply | rollback |
  unset | profile_reload)/outcome(OUTCOME_OK="ok" | OUTCOME_ERROR="error",
  audit.py:33-34)/paths(受影响 path 列表)/diff(脱敏的 before/after)/
  snapshot_id/detail(错误信息或备注)。
- diff 里的 secret 值在到达 AuditEntry 之前已被生产方替换成哈希占位(不是明文)。diff
  在校验之前就算好,所以失败的审计条目里也有 diff。
- AuditHook 类型(audit.py:61):Callable[[AuditEntry], None]。实现:InMemoryAuditSink
  (测试)、JsonlAuditSink(每条一行 JSON,append-only,锁保护,文件 0600、父目录 0700;
  read_all() 读回)。
- 每次事务结束(或事务中出错)都会调 _audit(store.py:1110),回滚与 profile_reload 也
  各是一条审计条目。

## 七、导出

rtime_admin_core.__init__ 导出:ConfigStore、Registry、default_registry、
register_qq_module、config_field/secret_field 相关、Snapshot/history/audit 类型、
metadata 辅助等(以 __init__.py 的 __all__ 为准)。coverage_doctor / coverage_allowlist
见 config-coverage.zh-CN.md。
