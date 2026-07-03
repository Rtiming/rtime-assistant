# profile 编写参考(声明式实例配置)

状态:已建(T1 机制 + T2 消费接线/学生会 owner 收编/read_only 端到端硬门,全部合 main)。
代码:packages/rtime-config/src/rtime_config/profile/、profiles/、
apps/qq-bridge/qq_bridge/config.py。设计依据:
docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §二。

profile 是 git 内一份人写的、有嵌套结构的声明文件,承载一个实例的 模型 / 系统提示词 /
权限与工具策略 / 库 scope / 用户分级 / 渠道绑定 / 输出渲染。加载器把它编译成扁平的
module.field 键值集合,作为 ConfigStore 读取链里 store 之下、default 之上的只读层。
admin-core 零寻址改动,单一真相(schema 注册一次)不被破坏。

术语提醒:本文说的"profile"是这套配置机制。另有一个同名不同物的 packages/rtime-profile
包,那是给上游 agent 用的 profile/policy 顾问工具(doctor/scan/panel/plan),与本机制无关。

## 一、文件布局与绑定

    profiles/                        # git 内,仓库顶层
      _base/
        qq.yaml                      # 渠道级默认(单层继承的唯一父)
        feishu.yaml                  # 骨架(飞书接 loader 属 T2 后续)
        web.yaml                     # 骨架(web-chat 接 loader 属 T5b)
        prompts/qq-system.md
      owner/    profile.yaml + prompts/system.md
      studentunion/  profile.yaml + prompts/system.md + direct-rules.json + library-policy.json

一桥进程一 profile。绑定=桥容器 env RTIME_PROFILE=<id>(compose per-service),
profiles 目录只读挂载进容器 /etc/rtime/profiles。RTIME_PROFILE 未设=回落旧 env-only 路径。
详见 deploy/PROFILE-CUTOVER.md。

## 二、profile.yaml schema

顶层键(schema.py:156-172;所有段 extra="forbid",未知键报错):

| 键 | 类型 | 必填 | 说明 |
|---|---|---|---|
| schema_version | int | 是 | 必须为 1(SUPPORTED_SCHEMA_VERSION) |
| profile | 见下 | 是 | id + 单层继承 |
| identity | 段 | 否 | 显示名 + 系统提示词文件引用 |
| model | 段 | 否 | 默认模型别名、锁定、admin 别名、params |
| permissions | 段 | 否 | read_only、permission_mode、工具增减 |
| library | 段 | 否 | 网关 URL、scope、脱敏开关 |
| plugins | 段 | 否 | 直答规则文件、campus_fetch、mcp_servers |
| users | 段 | 否 | admins / allowed / blocked |
| channels | 段 | 否 | 目前只有 qq 是强类型 |
| output | 段 | 否 | 渲染档 plain_text / rich / markdown |

各段字段(均可选,None=不贡献到编译层):
- profile.id(str,必填)、profile.extends(str|None):单层继承,如 _base/qq。
- identity.name(str|None)、identity.system_prompt_file(str|None,相对 profile 目录的
  文件引用,加载为内容)。
- model.default(str|None,别名经 model-registry.json 解析)、pinned_for_non_admin
  (bool|None)、admin_aliases(list[str]|None)、params(dict|None,唯一深合并的 dict)。
- permissions.read_only(bool|None,限制类字段,见五)、permission_mode(str|None,
  read_only=true 时被代码强制忽略)、tool_allow_extra / tool_deny_extra(list[str]|None,
  裸 "Bash" 被拒)。
- library.gateway_url(str|None)、scope(list[str]|None,单一真相生成 policy)、
  redact_sensitive / hide_excluded_in_results(bool|None)。
- plugins.direct_rules_file(str|None,文件引用,投影为路径)、campus_fetch(dict|None)、
  mcp_servers(dict|None,每 server 须显式 enabled;禁内联凭据)。
- users.admins / allowed / blocked(list[str]|None,blocked 是限制类字段,见五)。
- channels.qq.account_ref(str|None,指向 NapCat 状态/凭据的名字,绝不含凭据值)、
  private_access(str|None,见下)、public_groups / group_allowlist(list[str]|None)、
  open_public(bool|None,见下)、group_reply_at_sender(bool|None,见下)、
  group_invite_policy(str|None,reject/allow/owner)、autoleave(bool|None)。
- output.render(str|None,plain_text/rich/markdown)。

channels.qq.open_public(开放答疑模式,bool|None):True => 任何群里任何非黑名单成员
@bot 都能提问,**不再要求群在 public_groups 白名单里**(owner:"默认所有群所有人都能用")。
准入次序 blocked>admin>user 不变:黑名单仍一律拒(open 也压不过它),admin(在
users.admins 里)仍判 admin。只放开【群答疑的准入范围】——私聊由
admin / allowed / private_access 单独控制;绝不削弱 read_only / library.scope 硬门
(那些是独立的每次运行硬门,与谁在问无关)。配合 autoleave=false(开放模式下代码也强制
bot 留在被拉进的每个群)。
投影为 qq.open_public(env QQ_OPEN_PUBLIC,hot)。

channels.qq.private_access(私聊开放策略,str|None):取值
admin_allowed(默认,仅 admin + users.allowed 可私聊)、friends(所有 QQ 好友私聊可问)、
friends_and_temporary(好友私聊 + 群临时会话可问)。NapCat/OneBot v11 私聊消息里
sub_type=friend 代表好友私聊,sub_type=group 代表群临时会话,sub_type=other 仍拒。
这些放开的用户只判为普通 user 档:可问、可用 basic 命令,不可用 admin 命令/自选模型。
黑名单仍最高优先级。**好友请求不会因为 private_access 自动通过**;好友请求属于 request
事件,当前桥不自动 approve。投影为 qq.private_access(env QQ_PRIVATE_ACCESS,hot)。

channels.qq.group_reply_at_sender(群聊回复 @ 触发者,bool|None):True => 群聊文本回复
开头加 OneBot `CQ:at` 段 @ 原消息发送者,避免群里多人同时问时串线。默认 False 兼容旧行为;
只影响群文本回复,不改变私聊、访问门、好友请求或 read_only/scope。投影为
qq.group_reply_at_sender(env QQ_GROUP_REPLY_AT_SENDER,hot)。

## 三、extends(单层继承)与合并语义

- extends 是字符串引用,如 _base/qq → profiles/_base/qq.yaml(loader 自动补 .yaml)。
- 单层封顶:父 profile 自己再 extends → ProfileError(业内共识,防无界继承)。含 .. 逃出
  profiles_root 的路径 → ProfileError。
- 合并语义(OpenClaw):按顶层字段整体替换,唯一例外是 model.params 深合并一层(子键
  覆盖父键)。列表不深合并(父 allowed:[a,b] + 子 allowed:[c] = [c],不是并集)。合并后
  profile 段取子的 id/extends。

## 四、文件引用

某些字段值是"文件引用",在 YAML 里写相对 profile 目录的路径字符串,加载器展开:
- needs_file_content:读文件文本内联进编译值。例:identity.system_prompt_file:
  prompts/system.md → qq.system_prompt(值=文件内容)。
- needs_file_path:校验文件存在,注入解析后的完整路径字符串。例:
  plugins.direct_rules_file: direct-rules.json → qq.direct_rules_path(值=路径)。
- 文件缺失 → 硬错误(ProfileError);引用逃出 profile 目录 → ProfileError。

## 五、编译投影表(profile → 扁平 module.field)

加载器按一张显式表(mapping.py:PROJECTIONS)把嵌套 profile 投影到扁平 module.field。
表是数据不是代码,进 golden 测试。None 值稀疏投影(不贡献,落回 schema 默认,不遮蔽 store)。

| profile 路径 | 目标 module.field | 变换 |
|---|---|---|
| identity.system_prompt_file | qq.system_prompt | 文件内容 |
| model.default | qq.model | — |
| permissions.read_only | qq.read_only | — |
| permissions.permission_mode | qq.permission_mode | — |
| plugins.direct_rules_file | qq.direct_rules_path | 文件路径 |
| plugins.mcp_servers | qq.mcp_config | 序列化 JSON(enabled=false 剔除;全空→{"mcpServers":{}}) |
| users.admins | qq.admin_ids | frozenset |
| users.allowed | qq.allowed_users | frozenset |
| users.blocked | qq.blocked_users | frozenset |
| channels.qq.private_access | qq.private_access | — |
| channels.qq.group_reply_at_sender | qq.group_reply_at_sender | — |
| channels.qq.public_groups | qq.public_groups | frozenset |
| channels.qq.open_public | qq.open_public | — |
| channels.qq.group_allowlist | qq.group_allowlist | frozenset |
| channels.qq.group_invite_policy | qq.group_invite_policy | — |
| channels.qq.autoleave | qq.group_autoleave | — |

注:本轮真实接线的活模块是 qq,所以投影都落 qq.*;permissions.read_only 落 qq.read_only
而非样例 channel-common(样例仅示形状,未接活桥)。

## 六、secrets 绝不入 profile(三道)

1. schema 拒绝(编译期硬门,始终跑、fail-closed):编译产物逐键查 x-secret;凡命中
   x-secret 字段 → ProfileSecretError(加载失败,非 warning)。模块未注册无法分类也
   fail-closed 报错。因此 load_profile 必须传 registry(否则 ProfileError,该门不能被跳过)。
2. mcp_servers 内联凭据拒绝:任何 enabled server 内联了 token/secret/password/
   authorization 等凭据样键(大小写不敏感、含嵌套)→ ProfileSecretError。
3. secrets 清单继续走 env/keyfile,渲染永远脱敏,审计 diff 只见哈希占位;gitleaks 进 CI
   快门重点扫 profiles/。profile 只允许存引用(account_ref、keyfile 路径名)。

## 七、read_only / blocked 的 fail-closed 并集语义(限制类字段)

普通字段按 env > store > profile > default(last-wins)。但两个"限制类"字段是有意例外,
按并集解析,防止低层安全声明被高层空值静默降级(合 main 修复,commit 61b3283;实现
apps/qq-bridge/qq_bridge/config.py:_restriction_bool_union / _restriction_idset_union):

- read_only(布尔,OR 并集):任一层要求只读即生效;env 只能升级(=1 强开),绝不能降级
  (=0 / 空 无法关掉 profile 的 read_only: true,是 no-op,只打一条"该键无效"警告)。
- blocked_users(ID 集,∪ 并集):env ∪ store ∪ profile;env 只能新增拉黑,空 env 不能
  删掉低层已拉黑的人。

后果:studentunion 的 read_only: true 是端到端硬门——即使 docker.env 残留 QQ_READ_ONLY=0
也仍为只读。read_only 硬门在代码里强制 dontAsk 权限模式 + 禁写工具集(不信任提示词)。
迁移期若 legacy env 与 profile 同存:非限制字段 env 胜且警告 "legacy … env in use"
(一版后移除);限制字段 env≠1 打 "…is a no-op" 警告。

## 八、桥如何消费 profile(RTIME_PROFILE)

入口 QQBridgeConfig.load()(config.py:535):读 RTIME_PROFILE,空→from_env()(旧路径);
非空→from_profile(profile_id)。相关 env:RTIME_PROFILE、RTIME_PROFILES_ROOT
(默认 /etc/rtime/profiles)。

from_profile 消费链(config.py:554):
1. load_profile(profile_dir, registry, profiles_root, validate) 得到 CompiledProfile
   (含 .layer 扁平层、.config 解析模型、.files 文件引用等)。
2. 用 compiled.layer 建 ConfigStore(profile_layer 注入)。
3. 按 env > store > profile > default 解析:普通字段 store.get(path);限制类字段走并集函数。
4. env 拥有的字段(provenance=="env")用 from_env 的完全变换值(CLI PATH 查找、QQ_DEBUG→
   DEBUG、~ 展开等);其余取 store.get。

reload:profile 层加载后不可变(CompiledProfile 冻结);热/重启由字段 x-reload 决定
(system_prompt/model/名单等标 hot,read_only/mcp_config 等重启级)。运行时热重载走
admin-api 的 POST /v1/profiles:reload(reload_profile 原子 validate-then-swap,见
admin-core.zh-CN.md §二)。

## 九、样例(以 profiles/ 现网为准)

学生会(profiles/studentunion/profile.yaml)——公开只读答疑:
- extends: _base/qq;read_only: true(端到端硬门);model.default: ds、
  pinned_for_non_admin: true、admin_aliases: [ds, kimi]。
- library.scope: [knowledge/institutions/ustc](单一真相,生成 library-policy.json);
  gateway_url 指 8781;redact_sensitive/hide_excluded 都 true。
- plugins.mcp_servers 只列 rtime-library-gateway(enabled: true;server 名必须精确,
  只读 allowlist glob 依赖);direct_rules_file: direct-rules.json。
- users.admins: ["<owner QQ号>"];private_access: friends_and_temporary
  (所有好友私聊 + 群临时会话可问,好友请求仍不自动通过);open_public: true(开放答疑:
  任何群任何非黑名单成员 @bot 都能问,不再限于 public_groups);group_reply_at_sender: true
  (群内回复开头 @ 触发者);public_groups/group_allowlist:
  ["<主群群号>"](保留记录主群,open_public 开时其准入被覆盖);group_invite_policy:
  allow、autoleave: false(运营群不自动退;open_public 也强制不退,双保险)。
- output.render: plain_text。

owner(profiles/owner/profile.yaml)——owner 私聊全功能:
- extends: _base/qq;read_only: false(可写);model.default: kimi、
  admin_aliases: [kimi, ds, opus, sonnet, qwen, ustc, ollama]。
- 不设 library.scope(走整库,compose 挂 /mnt/brain 真库);redact_sensitive: false。
- mcp_servers: {}(私聊默认不注入 MCP,直读 /mnt/brain 跳过冷启)。
- channels.qq.account_ref: napcat-owner、group_invite_policy: reject、autoleave: true。

## 十、doctor:库 scope 三方交叉核验

profile/doctor.py 是纯数据函数(不探活库),核验库 scope 三锁一致:
- check_profile_policy_file(profile_scope, policy_path):断言 git 内 library-policy.json
  == 由 profile.library.scope 生成(catch 手改 policy 或改 scope 未重生成);风险
  policy_file_missing / policy_file_differs_from_profile_scope / invalid_scope。
- cross_check_scope(profile_scope, policy_allowed_prefixes, mount_subtrees):三集必须相等
  (profile scope ↔ 生成 policy 的 allowed_path_prefixes ↔ compose /mnt/brain 只读子挂载),
  任两者不等即列风险。mount_target_to_scope 把 /mnt/brain/knowledge/... 规范成 brain 相对前缀。

## 十一、斜杠命令分层(basic / admin)

命令面是模块化声明式注册表(apps/qq-bridge/qq_bridge/commands.py:COMMANDS),不再是一条
大 if 链——加一条命令 = 加一个表项,加一个 tier = 扩 Tier + _tier_ok。命令的 tier 与
_actor_tier 判定的用户 tier 共用词汇(user / admin),直接比较无需映射。

两档:
- basic(user 档):对所有【可服务】用户生效(私聊 AND 群里)。/new /reset(开新对话)、
  /stream(开关流式)、/help(列出 caller 本档可用的命令)。都是 per-user/per-session、
  无成本无滥用面。
- admin(admin 档):仅 admin,任何地方(私聊 or 群)。/model(换模型=成本/滥用面)。
  非 admin 发 admin 命令 => 友好拒绝(不落模型);admin 的 /model 只改 admin 自己的会话,
  所以在群里也放开(安全)。

规则:未知 /foo => 落回普通问答(现状);带附件的 / 开头消息按普通媒体消息处理(不当命令)。
门控在 _actor_tier(准入:blocked/私聊门/群准入)之后跑——被拒的用户根本到不了命令表。
read_only / 库 scope 是独立硬门,与命令无关,每次运行都强制。owner:"切换模型这种指令只给
管理员;只给用户开最基础的指令"。
