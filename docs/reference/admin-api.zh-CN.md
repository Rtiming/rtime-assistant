# admin-api 使用参考(L2 HTTP 控制 API + 面板骨架)

状态:已建(合 main,对抗复审 13 缺陷全修 + 每条回归测试)。代码:
packages/rtime-admin-api/。设计依据:docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §六。

admin-api 是 admin-core(L1)之上的 FastAPI 薄层:配置树读写 + validate/diff(dry-run)
+ apply/rollback + history/audit + 面板。它与问答入口(web-chat)严格分离——不同容器/
端口/无共享中间件;永远绑 127.0.0.1(非回环须显式 opt-in)。运维 agent 与人类面板是
同一 API 的同源同权限客户端。

事实基线以 app.py / auth.py / wiring.py / panel.py / locking.py 为准。

## 一、端点清单

前缀 /v1(面板静态路由除外)。除公开面板壳外,所有端点都要求 Bearer 鉴权。非 2xx 响应
统一形状(errors.py:38):{"error": {"code":…, "message":…[, "errors":[…]]}}。

读:
| 动词 | 路径 | scope | 响应 |
|---|---|---|---|
| GET | /v1/schema | read | 200 {"modules": {模块名: schema, …}} |
| GET | /v1/config | read | 200 {"values": {path: 值}, "etag": …};响应头 ETag |
| GET | /v1/config/{path} | read | 200 {"path":…, "value":…} |
| GET | /v1/history | read | 200 {"snapshots": [{id, ts, note}, …]} |
| GET | /v1/audit | read | 200 {"entries": […]}(?limit=,默认 50,1..1000) |
| GET | /v1/health | read | 200 {"ok":true, "version":…, "needs_restart":[…]} |

GET /v1/config 与 /v1/config/{path} 支持 ?reveal=1(需 read:sensitive)看明文;
GET /v1/config 支持 ?provenance=1,每个值变 {"value":v, "provenance":层}。

Dry-run(只校验/预览,永不落盘、不审计):
| 动词 | 路径 | scope | 响应 |
|---|---|---|---|
| POST | /v1/config/validate | read | 恒 200 {"ok", "errors", "diff", "hot", "restart_required"} |
| POST | /v1/config/diff | read | 200 {"diff": {path: {before, after}}} |

两者 body 都是 {"changes": {path: value, …}}。validate 的判定在 body 不在状态码;未知
path 作为错误条目返回,不是 404。diff 对非 read:sensitive 调用方,每个 secret path 恒返
{"before":"***","after":"***"} 且不因值相等被丢弃(堵等值预言机)。

**J1 预览影响(validate 增强)**:validate 现在除 ok/errors 外,还返回这次改动的
`diff`(redacted,同 /v1/config/diff 脱敏纪律)+ `hot`(会热生效的 path)+ `restart_required`
(需重启的 path,按 x-reload 分类)——**不落盘、不审计**。这是面板"预览影响"按钮和 AI
提交前预检的入口(Netdata TEST 命令语义):先看"这次改哪些要重启"再决定 apply。
底层 = store.classify_reload(纯分类不 apply)+ store.diff。config-and-access-architecture §2.3。

**J1 provenance/drift/unset(消灭"改了 UI 不生效")**:
- `GET /v1/config?provenance=1`:每个值带 `{"value", "provenance": env|store|profile|default}`,
  面板给每个字段标"生效层"。
- `GET /v1/config/drift`(read):列 store override 被 profile 层遮蔽(值不同)的
  `[{path, store, profile, secret}]`(secret 恒 ***)。面板据此把这些字段标"由 profile 管理"。
- `DELETE /v1/config/{path}`(write + 字段 x-scope + If-Match + 审计 action=unset)**J5 secret 两段式**:unset
  非 secret 一步完成;unset **secret** 字段(丢明文值不可逆)不带 `?confirm=<token>` 返 409+confirm_token(plan),带才删;token 绑 path+ETag 陈旧即失效。清 store
  override,值落回下层(profile/默认)——K8s SSA ownership 交还,非"文件赢/UI 赢"开关;
  幂等(无 override=no-op 成功)。config-and-access-architecture §2.2。

**K2/K5 模块与模型目录端点(read scope;注入座纹理,未接线返 501)**:
- `GET /v1/modules`(K5):deploy/modules.json 的 doctor 报告——全部模块+每个装没装
  (按 COMPOSE_PROFILES)+hot_pluggable+config_module+docs+校验 issues。wiring 由
  `RTIME_MODULES_MANIFEST`(指向 modules.json)启用;`RTIME_COMPOSE_FILE` 可覆盖
  compose 路径。面板"模块"tab 的数据源(一处总览+跳 schema 表单细配)。
- `GET /v1/models/catalog`(K2):解析后的 model-registry.json(无密钥值)+ 生效路由
  默认(models.default_model)。**选默认写回走 PATCH /v1/config**,不开第二写口。
- `GET /v1/models/probe?provider=&timeout=&check_url=`(K2):provider 就绪灯
  (密钥 env 是否已设+endpoint 是否活;从不读密钥值,URL 全来自 registry 无 SSRF 面)。

写(都要求 If-Match,缺→428,过期→412;都要求 write scope + 字段级 x-scope):
| 动词 | 路径 | body | 响应 |
|---|---|---|---|
| PATCH | /v1/config | {"changes": {path:value}, "note": null\|str} | 200 成功 / 422 校验失败 |
| POST | /v1/rollback | {"snapshot_id": …} | 200 成功 / 404 未知快照 |

PATCH 的 changes 必须非空(空→400)。成功响应形如:{"ok":true, "ts":…(UTC ISO-8601),
"snapshot_id":…(uuid4 hex), "changed":[…], "hot":bool, "restart_required":[…],
"diff":{…}, "etag":…},响应头 ETag 为新标签。rollback 成功同形。

面板静态路由(公开,无鉴权,panel.py):
- GET /、GET /panel → index.html(壳);GET /panel.js、GET /panel.schema.js → JS;
  GET /panel/{asset} → 白名单资产(仅 index.html / panel.js / panel.schema.js,
  其余 404)。壳无鉴权但本身惰性无用:必须运行时粘贴 token 才能调 /v1;且整服务
  127.0.0.1-only。面板路由 include_in_schema=False 且最后注册,绝不遮蔽 /v1。

## 二、鉴权与 scope 模型

scope 常量(auth.py:42-44):SCOPE_READ="read"、SCOPE_WRITE="write"、
SCOPE_READ_SENSITIVE="read:sensitive"。

- read:所有 GET + dry-run POST(validate/diff)。

**J3 RBAC 身份(config-and-access §一)**:keys 每项可选 `is_platform_super`(bool,
owner+开发助手=平台超管,行使删数据/签token/改RBAC等超管独占能力)+ `project_roles`
(`{project: owner|admin|user|readonly-guest}`,只在该 project 内有效)。与 scope 正交:scope
管"能调哪个 API 动词",RBAC 管"平台超管 vs 项目角色"。`key.principal()` 映射成 RBAC
Principal;`require_capability(key, cap, project=)` 是能力门(审核台等新端点用,现有端点仍
走 scope 门不变)。见 reference/rbac.zh-CN.md。

**J4 token TTL+吊销(config-and-access §3.2)**:keys 文件每项可选 `expires_at`
(ISO8601,到期 401 token expired;缺省=不过期,向后兼容)+ `revoked`(true=即时吊销
401 token revoked)。给每个消费方(MCP/QQ/飞书/面板)发独立、可到期、可吊销的 token,
避免共用全权 bearer。auth 恒定时匹配后再判吊销/过期,时序不泄哪个 key。
- write:PATCH /v1/config 与 POST /v1/rollback。
- read:sensitive:?reveal=1 看明文 secret 所需;不持有则一律脱敏。
- 字段级 x-scope:某字段 schema 声明了 x-scope(如 write:models),则改它额外需要该
  精确 scope 字符串;无 x-scope 的字段对普通 write 开放。每个写动词(PATCH/rollback)
  都对"本次改动涉及的所有 path 的 x-scope 并集"查一遍(app.py:_require_field_scopes)。

keys 文件格式(auth.py,由 RTIME_ADMIN_API_KEYS 指向,在 git 外):JSON 数组,每项
{name, key, scopes}:

    [
      {"name": "ops-agent", "key": "<至少16字符随机>", "scopes": ["read", "write"]},
      {"name": "panel-user", "key": "<另一随机>", "scopes": ["read", "read:sensitive", "write"]}
    ]

约束(auth.py:63-113):非空 JSON 数组;name 必填非空且唯一;key 必填、长度≥16
(MIN_KEY_LENGTH=16);scopes 必填、非空字符串列表;name 或 key 重复报错。每个部署者的
agent 发最小 scope token。

鉴权流程(auth.py:116-152):解析 Authorization: Bearer <token>;缺失/畸形/无效→401
(带 WWW-Authenticate: Bearer);对所有已配置 key 做常数时间比较(无早退,时序均匀);
命中返回 ApiKey。scope 不足→403(消息 "key '…' lacks required scope '…'")。审计 actor=
命中 key 的 name,source 恒 "http"。

## 三、ETag / If-Match 并发控制

- ETag 计算(app.py:126):对 store.persisted_flat()(config+secrets,未脱敏)的规范
  JSON(排序键、紧凑分隔、ensure_ascii=False)做 HMAC-SHA256(用 store.secret_salt 加
  盐)。强标签,仅服务端可算(加盐防离线猜)。用 persisted 层而非 resolved 视图:保证
  对 env-pinned 字段的写也会推进标签(resolved 值可能不变)。
- 发标签:GET /v1/config、PATCH、rollback 的响应头 ETag(带引号)。
- 要标签:PATCH 与 rollback 必须带 If-Match(app.py:_required_if_match):缺失/空→428
  (Precondition Required);If-Match: * 或弱标签(W/…)→400;支持逗号多候选,任一等于
  当前标签即匹配(RFC 7232)。不匹配→412(Precondition Failed,app.py:_check_etag,
  常数时间、扫全部候选无早退)。
- 并发锁:写在进程内 threading.Lock 且跨进程 flock(<store>/.lock,locking.py)双重
  串行下执行——查 If-Match、apply、重算三步在锁内,线程与进程都不能交错读改写。

典型写流程:
1. GET /v1/config → 记下响应头 ETag。
2. (可选)POST /v1/config/diff 预览、POST /v1/config/validate 校验。
3. PATCH /v1/config,带 If-Match: "<上一步 ETag>" 和 {"changes":…}。
4. 若返回 412 → 说明有人先改了,重新 GET 拿新 ETag 再试;成功则响应头带新 ETag。

## 四、脱敏(secret 处理)

- 占位符 REDACTED_PLACEHOLDER(来自 rtime_admin_core.metadata,值为 "***")。
- GET config:set 过的 secret 显示 "***"(未 set 保持 null);要明文需 read:sensitive
  + ?reveal=1。
- diff/validate/PATCH:core 的脱敏 diff 用 store 盐把 secret 哈希;无 read:sensitive 时
  admin-api 重算成恒定 "***"(before 与 after 都 ***),且无论值是否相等都保留该 path
  (堵等值预言机);持 read:sensitive 时原样返回 core 的(哈希)diff。
- pydantic 校验错误:secret 字段的 message 置为固定串、input/ctx 整个丢弃(除非
  read:sensitive)。
- _safe_is_secret(app.py:177):判定失败一律按 secret 处理(fail-closed)。

## 五、运行服务

入口:python -m rtime_admin_api(__main__.py)。启动序列:读 host/port
(host_port_from_env)→ 建 app(app_from_env)→ 非回环且已 opt-in 时打警告 →
uvicorn.run(host, port);配置错误(ValueError)退出码 2。

环境变量(wiring.py):
- RTIME_ADMIN_STORE_DIR(必填):本部署的 admin 状态目录,含 config.json、
  secrets.json(0600)、history/、audit.jsonl、salt(0600)、.lock。目录 0700。
- RTIME_ADMIN_API_KEYS(必填):bearer keys JSON 文件路径(绝不入 git)。
- RTIME_ADMIN_API_HOST(默认 127.0.0.1):回环集合 {127.0.0.1, ::1, localhost};绑
  非回环须 RTIME_ADMIN_API_ALLOW_NONLOOPBACK 为真值(1/true/yes/on),否则启动即
  ValueError。
- RTIME_ADMIN_API_PORT(默认 8790):整数 1..65535。
- RTIME_ADMIN_API_ALLOW_NONLOOPBACK(默认空/假):见上,非回环显式开关。

FastAPI app 配置(app.py:287):openapi_url/docs_url/redoc_url 全 None(关文档,避免向
未鉴权者泄漏字段名如 ustc_api_key);app 级 Depends(_auth) 在 body 解析之前跑(未鉴权
的畸形 body 得 401 而非 422)。__version__ = "0.1.0"。

## 六、错误码速查

| 状态 | 触发 | code |
|---|---|---|
| 400 | If-Match: * / 弱标签 / 空 changes / 畸形 path | wildcard_if_match_rejected / weak_etag_rejected / empty_changes / invalid_path |
| 401 | 缺失/无效 token | unauthorized(带 WWW-Authenticate: Bearer) |
| 403 | token 有效但 scope 不足 | forbidden |
| 404 | 未知 path / 未知快照 / 未知面板资产 | unknown_path / unknown_snapshot / not_found |
| 412 | If-Match 过期 | etag_mismatch |
| 422 | 请求体形状错 / 配置校验失败 | invalid_request / validation_failed(errors[] 逐条) |
| 428 | 写缺 If-Match | precondition_required |
| 500 | 未处理异常 | internal_error |

## 七、安全姿态小结

- 永远 127.0.0.1-only,非回环须显式 opt-in;与 web-chat 零共享,公网化只反代 web-chat,
  admin 面纹丝不动。
- secret 默认脱敏,要明文需 read:sensitive + ?reveal=1;OpenAPI/docs 关闭防字段名泄漏。
- 写用 If-Match 乐观锁 + 跨进程 flock,防两个 agent 并发互踩(丢更新变 412)。
- 每次写都字段级 x-scope 查权;审计 append-only、secret 哈希占位、文件 0600/目录 0700。
