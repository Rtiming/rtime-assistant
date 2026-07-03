# 模块清单(module manifest)使用参考

设计: [../design/module-system-and-open-source-2026-07.zh-CN.md](../design/module-system-and-open-source-2026-07.zh-CN.md)(K1)。
清单: `deploy/modules.json`。代码: `packages/rtime-admin-core/src/rtime_admin_core/modules.py`
(纯 stdlib 加载/校验)+ `modules_cli.py`(doctor CLI)。

## 干什么

`deploy/modules.json` 是**所有可选配模块的单一真相**:装机向导、面板"模块"视图、doctor、
开源打包都读它——一处声明,四处受益。让"用户充分选择配置什么"有据可依。

四个消费者都已落地(2026-07-03):当前 22 个模块(K-audit 全资产盘点后扩全,含微信
公众号三件/reminders/jobs/docpack/citation/visualmd/诊断组/hub-connector/webdav/
memory-loop)。装机向导=deploy/setup-wizard.py(reference/setup-wizard.zh-CN.md,
RPi 真机验证);每个可选模块"接自己的X"教程=docs/connect-your-own.zh-CN.md(K6);面板视图=admin-api GET /v1/modules + 面板"模块"tab(K5,
RTIME_MODULES_MANIFEST 启用);开源打包=deploy/publish-manifest.json 白名单
(风险台账 docs/design/open-source-risk-register-2026-07.zh-CN.md 未清零前不发布)。

## 一个模块的字段

| 字段 | 含义 |
|---|---|
| `id` / `kind` / `title` | 唯一 id / 类(core/channel/gateway/panel/provider/integration/rules/extension)/ 显示名 |
| `optional` | 是否可选装(false=恒开的 core) |
| `compose_profile` | 装机开关:`COMPOSE_PROFILES` 里加它即装;null=非 compose 或恒开 |
| `config_module` | 面板配置:admin-core registry 的模块名(面板据此出表单);null=无面板配置 |
| `depends_on` | 依赖的其它 module id |
| `hot_pluggable` | `hot`(热载) / `restart`(要重启) / `none` |
| `data_paths` | 该模块碰的数据(**永在仓库外**,可审计) |
| `docs` / `setup_notes` | 该模块文档路径 / 装配要点 |

## doctor / list

```bash
# 校验 manifest 与现实一致(compose profile 真存在、config_module 真在 registry、docs 存在)
python -m rtime_admin_core.modules_cli doctor [--profiles qq,web]
# 列所有模块 + 装没装(按 --profiles 判)
python -m rtime_admin_core.modules_cli list [--profiles qq]
```

输出 `{ok, total, by_kind, modules:[{id,kind,installed,compose_profile,config_module,...}], issues}`。
issue 码:compose_profile_missing / config_module_unknown / dep_missing / docs_missing /
bad_kind / bad_hot_pluggable。**dev/CI 门**:引用错误(非 docs_missing)必须为空。

## Python API

```python
from rtime_admin_core.modules import load_manifest, validate_manifest, manifest_report
mods = load_manifest(open("deploy/modules.json").read())
issues = validate_manifest(mods, known_config_modules=..., known_profiles=..., docs_exists=...)
```
校验用依赖注入(已知 config_module 名 = `registry.KNOWN_MODULE_NAMES`、已知 profile、docs
判定函数),admin-core 不硬依赖 compose/repo 布局。

## 三层配置里的位置

manifest 是"装机选装(第一层)"的驱动:向导读它列 optional 模块 → 用户勾选 → 拼
`COMPOSE_PROFILES`。第二层(面板细配)靠 `config_module` 映射到 registry schema 表单;
第三层(文件/CLI/API)是同一 schema 真相(design §四)。

## 加一个模块

1. 在 `deploy/modules.json` 加一条(id/kind/compose_profile/config_module/depends_on/docs/data_paths)。
2. 若有配置:在 admin-core registry 注册 config_module(schema)。
3. 若装机可选:在 compose.prod.yml 给它一个 `profiles: ["<name>"]`。
4. 写它的 reference 文档,`docs` 字段指向它。
5. `modules_cli doctor` 必须绿。

测试:`packages/rtime-admin-core/tests/test_modules.py`(加载/校验/report/真实 manifest 对账)。
