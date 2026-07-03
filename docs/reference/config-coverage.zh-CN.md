# 配置覆盖率守卫使用参考(doctor + 棘轮测试 + allowlist)

状态:已建(批 0 合 main:52b6660 / 合并 73e6658)。代码:
packages/rtime-admin-core/src/rtime_admin_core/coverage_doctor.py、coverage_allowlist.py、
packages/rtime-admin-core/tests/test_config_coverage.py。设计依据:
docs/design/config-full-coverage-plan-2026-07.zh-CN.md §三。

目的:让"配置全覆盖"靠 CI 可证,而非口头承诺。审计发现全仓约 220 个 env 变量启动读一次
(docs/audit/codebase-audit-2026-07.zh-CN.md §二);P2 配置收编逐模块把它们迁到 rtime-config
schema 并注册进 admin-core registry。这套守卫度量迁移进度,并把它变成棘轮:新增一个未注册、
未登记的 os.getenv 会让测试变红。批 0 是纯观测,零功能变更。

## 一、跑 doctor

    python -m rtime_admin_core.coverage_doctor

输出三行汇总 + 未覆盖清单:
- modules covered: X/Y(有 ≥1 个 env 被用到的模块 / 总注册模块数)
- fields covered:  Z/N(env 名被用到的注册字段 / 总注册字段)
- env keys: C covered / U used(K uncovered)
- 随后逐条列 uncovered env key 及其首个 file:line。

doctor 会尽力把 apps/qq-bridge 放上 sys.path 让 qq 模块能注册(qq 是已迁移样板),
用 default_registry(include_qq=True) 得到完整基线;qq app 不可导入时回落 no-qq。
admin-core 仍是叶子,不硬依赖 app。

## 二、两个采集端与覆盖公式

- 采集端 A(USED_ENV):AST 扫 apps/ packages/ deploy/ 下的 .py(跳过 venv/site-packages/
  __pycache__/build/dist 等,以及 tests/test_ 文件),收集字面量 key 的
  os.getenv("X") / os.environ.get("X") / os.environ["X"] / 裸 getenv("X"),每处记 file:line。
- 采集端 B(REGISTERED_ENV):对 registry 每模块 get_schema,取每字段实际接受的 env 名——
  声明了 x-env-aliases 就用它,否则用 pydantic-settings 推导的 <env_prefix><FIELD>(大写)。
- 公式:covered = REGISTERED_ENV ∩ USED_ENV;uncovered = USED_ENV − REGISTERED_ENV。

## 三、读棘轮(测试如何绿/红)

测试文件 tests/test_config_coverage.py。基线绿(allowlist 列全了当前所有未注册键)。

核心断言(棘轮):
- test_no_unregistered_env_outside_allowlist:USED_ENV − REGISTERED_ENV − ALLOWLIST 必须
  为空。新加一行 os.getenv("NEW_X") 既没注册字段又没进 allowlist → 红,PR 卡住;失败信息
  逐条打 key 与 file:line,并提示"注册字段(首选)或按 reason 加进 allowlist"。
- test_no_stale_allowlist_entries(反向哨兵):ALLOWLIST − USED_ENV 必须为空——allowlist
  里已无人使用的死项即腐烂,收编某模块时应删掉该批键,忘删或键停用即被抓。
- test_baseline_numbers_are_sane:下限护栏(≥4 模块、≥40 字段、有覆盖),防扫描器因 import
  破坏静默塌成 0。
- test_every_secret_field_marked(硬不变量):每个 secret_field 都带 x-secret(脱敏可靠性的
  结构前提);canary 断言 models.ustc_api_key / models.litellm_master_key / qq.access_token
  在 secret 集里。
- test_config_fields_have_scope_warns_at_baseline(基线仅警告):非 secret 配置字段应带
  x-scope;qq pilot 有意省略(用模块级 scope),故基线是 warning 不 fail,等各模块都声明
  字段 scope 后收紧为硬断言。
- secret 不泄漏自测:get_all / diff / audit 里绝不出现明文 secret(canary "sk-PLAINTEXT-
  CANARY";get_all 掩码为 ***,diff 的 after 以 "hmac:" 开头,audit blob 不含明文)。

单独跑:

    uv run pytest packages/rtime-admin-core/tests/test_config_coverage.py

也挂在 orangepi post-receive main 后的 pytest 慢门,覆盖率数字回显进 PR
(见 docs/ci-server-gate.zh-CN.md)。

## 四、往 allowlist 加/减项

allowlist 是 coverage_allowlist.py 的 ALLOWLIST 字典({env_name: reason},保持排序)。
reason 语法:"<category>[:<batch>] 自由文本"。类别(计划 §2):

- bootstrap:进程自身机械 env,永不是被管配置字段(如 admin-api 读自己的
  RTIME_ADMIN_API_* —— 它是配置权威不是被配置模块)。
- deploy-path:部署层注入的路径/解释器/根(挂载点、run-log 位置、PYTHONPATH 垫片)。
- dev-override:dev/debug/标准 OS 开关(XDG_*、*_DEBUG)。
- derived-alias:schema 字段的 loader 变换而非逐字读的旧 env(如 QQ_MAX_DOWNLOAD_MB →
  max_download_bytes);字段已注册,这个裸名是它的输入,留着免得裸读被标红。

:batch 后缀命名未来收编该键的批次(TODO-batch:feishu / gateway / models / web-chat /
qq-selfheal / visualmd / ustc-kb / library-gateway / jobs 等),grep 即知每批要删哪几行。
纯类别(无 TODO-batch)的项是"本就不是被管配置",预期永久留 allowlist。

加项:register 字段(首选)否则加一行 {env: reason},reason 必须有类别。减项:某收编批次
注册了模块后,删掉该批 TODO-batch 键;若键彻底不再被任何代码读,也要删(反向哨兵会抓死项)。

## 五、每批实施纪律

- env_aliases 保全旧名一个版本;每模块迁移配一个"from_env 旧值 == 新模型加载值"等价测试。
- 每批合 main 后把 doctor 的 Z/N 数字更新进 config-full-coverage-plan-2026-07.zh-CN.md 顶部;
  数字下降视为回归。
- 基线约 37/220(乐观口径,按 include_qq)。目标:余下约 11 个模块迁 schema 注册,字段覆盖
  逼近 220,余项全落 allowlist 且每条有 reason,守卫全绿即"全覆盖"成立。

## 只增不减硬门(J2)

`test_coverage_floor_only_increases` 钉了覆盖地板(2026-07-04:模块9、字段164、env150),
随收编提升手动上调、**绝不下调**——去掉任一字段/env的覆盖=CI红。这是配置全覆盖的
护城河(config-and-access-architecture §2.1:业界 Grafana/GitLab 官方都不保证多入口可达)。
