# 安装向导使用参考(deploy/setup-wizard.py)

状态:已建+RPi真机验证(K4,2026-07-03,Raspberry Pi 4 / Python 3.14 / ARM 冷启动实测通过)。
设计:docs/design/module-system-and-open-source-2026-07.zh-CN.md(三层配置第一层"装机选装")。
清单真相:deploy/modules.json;实例目录约定:docs/instance-deploy.zh-CN.md 与 deploy/update.sh 头注。

## 一、干什么
新机器 git clone 后的**第一条命令**:选模块 → 自动带依赖闭包 → 拼 COMPOSE_PROFILES →
产标准实例目录(.env/compose.override.yml/data/state)。纯 stdlib,在任何 venv/依赖
存在之前就能跑(裸机 bootstrap)。向导只写文件不碰 docker。

## 二、用法
```bash
python3 deploy/setup-wizard.py list [--json]            # 看全部模块(可选/恒装)
python3 deploy/setup-wizard.py plan --instance DIR --modules channel-qq,channel-web [--json]
python3 deploy/setup-wizard.py init --instance DIR --modules channel-qq,channel-web [--name N] [--json]
```
init 不带 --modules 且在 TTY 上 → 交互式编号勾选(新手友好);非 TTY 必须显式
--modules(可空串=只装 core)——agent/脚本用 --json 拿结构化输出(有经验者自由度)。

## 三、产出与后续
```
<instance>/.env                  UPDATE_INSTANCE_NAME + COMPOSE_PROFILES + 每模块装配要点注释
<instance>/compose.override.yml  实例差异骨架(--force 重来也不覆盖你改过的这份)
<instance>/data/  state/         数据/状态
<instance>/state/install.lock    INSTALL_LOCK:{ts, modules, compose_profiles};重复 init 拒绝,--force 放行
```
接下来:按 .env 里各模块要点补齐真实 env(参考 deploy/env/*.example 与各模块 docs)→
`docker compose -f <repo>/compose.prod.yml -f <instance>/compose.override.yml --env-file <instance>/.env -p rtime-<name> up -d`
→ 之后升级/回滚走 deploy/update.sh;venv 建好后跑
`python -m rtime_admin_core.modules_cli doctor` 做完整对账;面板"模块"页(K5)看装态。

## 四、行为细节
- 依赖闭包:选 channel-qq 自动带上 core-config/gateway-core;core(optional=false)恒含。
- profile 校验:所选模块的 compose_profile 必须真在 compose.prod.yml 里,缺 → exit 2。
- 未知 module id → exit 2 并提示用 list。
- INSTALL_LOCK 语义(J8):防的是"忘了这台已初始化又跑一遍把 .env 覆盖掉";--force 是
  显式知情重来,且永不覆盖已存在的 compose.override.yml。

## 五、测试
沙箱:deploy/tests/test_setup_wizard.py(真 manifest;list/依赖闭包/init产物/锁拒绝/
--force不覆盖override/未知id/非TTY守卫/仅core,6 用例)。
真机:RPi4 冷启动(git archive 快照→解包→list/init/锁)全通过——凡有 python3 的
Linux/ARM 盒子开箱即用。
