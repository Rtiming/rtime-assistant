# 实例部署与一键更新(deploy/update.sh)

本机制面向 release-tag 下游实例:每个实例一份独立配置目录,代码统一来自仓库的发布 tag,用 `deploy/update.sh` 做检查更新/应用更新/回滚/看状态。

注意当前生产里的学生会并不是独立实例目录,而是主线仓库上的 `studentunion` profile(QQ 桥
`RTIME_PROFILE=studentunion`,只读硬门 + 8781 scoped gateway)。如果未来把学生会或其他组织
拆成独立部署,再按本文的实例目录和 tag 更新机制接管。开源/更新目标见
[open-source-update-goals.zh-CN.md](open-source-update-goals.zh-CN.md)。

owner 主实例(orangepi 上现有的 `scripts/docker-prod-check.sh --build --up` 流程)**保持不变**,不受本机制影响;本机制也不改 docker-prod-check.sh。两者可以共存:主实例继续跟 main 手动部署,其他实例跟发布 tag 自动化更新。

## 1. 发布通道

- 发布 = owner 在 main 上打**附注 tag** `vX.Y.Z`(`git tag -a v0.3.0 -m "..."` 后推送)。
- 实例侧"有新版"判定 = `git fetch --tags` 后存在比当前 checkout 版本更高的 `v*` tag(语义化排序)。
- 每个版本在仓库根 `CHANGELOG.md` 有对应 `## [X.Y.Z]` 节;节内:
  - `BREAKING: ` 行 = 破坏性变更,`update.sh check` 退出码 20,`apply` 默认拒绝、需 `--yes`;
  - `MIGRATION: NNN_xx.sh` 行 = 该版本携带实例迁移脚本(见 `deploy/migrations/README.md`)。

## 2. 实例目录约定

每实例一个目录,建议 `~/rtime-instances/<name>/`:

```
~/rtime-instances/<name>/
├── .env                  # 实例配置(compose --env-file 用),含凭据路径等,权限自管
├── compose.override.yml  # 实例差异:端口/挂载只读/profile/资源限制(模板见 deploy/compose.override.example.yml)
├── data/                 # 实例自己的 bind mount 数据
└── state/                # update.sh 专用:current-version、previous-version、
                          # migrations-applied、backups/、last-update.json、update.lock
```

规则:

- 升级流程**永不写实例目录**,唯一例外是 `state/`(含 `backups/`)。
- compose 统一调用形态(update.sh 内部即如此,手工排障也用同一形态):

```
docker compose -f <repo>/compose.prod.yml -f <instance>/compose.override.yml \
  --env-file <instance>/.env -p rtime-<name> <cmd>
```

- `-p rtime-<name>` 的 name 默认取实例目录名,可用实例 `.env` 里 `UPDATE_INSTANCE_NAME` 覆盖;多实例共机靠不同项目名 + 不同端口/数据目录隔离。
- 实例 `.env` 里 update.sh 认的可选键(其余键全部归 compose,update.sh 不 source 整个 .env):
  - `UPDATE_INSTANCE_NAME`:compose 项目名后缀;
  - `UPDATE_BACKUP_CMD`:应用级备份钩子(如 DB dump),备份阶段以 `bash -c` 执行,可用 `RTIME_INSTANCE_DIR`/`RTIME_BACKUP_DIR`/`RTIME_REPO_DIR` 环境变量;
  - `UPDATE_HEALTHCHECK_URL`:健康检查在容器全绿之外再 curl 一个 URL(**不要在 URL 里带 token/凭据**);
  - `UPDATE_HEALTH_RETRIES`(默认10)/`UPDATE_HEALTH_INTERVAL`(默认6秒)。

## 3. 首次起一个新实例

以学生会实例 `union` 为例(在目标机器上):

1. 准备代码副本(实例专用 clone,不与 owner 主实例共用工作区):
   `git clone <仓库地址> ~/rtime-instances/union-repo && cd ~/rtime-instances/union-repo && git checkout v0.3.0`(选一个发布 tag)
2. 建实例目录:
   `mkdir -p ~/rtime-instances/union/{data,state}`
3. 写 `.env`:从主实例部署文档(docs/docker-production.md)的变量清单出发,把挂载源、端口、UID、凭据文件路径都指向本实例自己的资源;加上第2节列的 UPDATE_* 可选键。
4. 写 `compose.override.yml`:
   `cp ~/rtime-instances/union-repo/deploy/compose.override.example.yml ~/rtime-instances/union/compose.override.yml`,按需启用端口/只读挂载/资源限制示例。
5. 预检与首次启动:

```
cd ~/rtime-instances/union-repo
docker compose -f compose.prod.yml -f ~/rtime-instances/union/compose.override.yml \
  --env-file ~/rtime-instances/union/.env -p rtime-union config   # 先看合成结果
deploy/update.sh apply --instance ~/rtime-instances/union --version v0.3.0 --yes
```

首次 apply 会把 tag checkout、跑齐所有未记账迁移、build、up、健康检查,并写 `state/current-version`。之后的日常更新不再需要 `--yes`(除非目标区间含 BREAKING)。

## 4. update.sh 用法速查

```
deploy/update.sh check    --instance <dir> [--repo <dir>]
deploy/update.sh apply    --instance <dir> [--repo <dir>] [--version vX.Y.Z] [--yes] [--dry-run]
deploy/update.sh rollback --instance <dir> [--repo <dir>] [--dry-run]
deploy/update.sh status   --instance <dir> [--repo <dir>]
```

- `--instance` 必填(或设环境变量 `RTIME_INSTANCE_DIR`);`--repo` 默认取脚本所在仓库根。
- 输出:JSON 单行到 stdout(给编排/巡检脚本消费),人类日志到 stderr。
- `check`:fetch tags 后输出 `{current, latest, update_available, breaking, migration_pending, pending_migrations, changelog_excerpt}`。只读,无副作用。退出码:0=已最新,10=有更新,20=有更新且目标区间含 BREAKING。
- `apply`:`flock` 拿 `state/update.lock`(无 flock 的平台自动退化为 mkdir 锁)→ 备份(.env 快照 + state 快照 tar 到 `state/backups/<时间戳>/`,有 `UPDATE_BACKUP_CMD` 则一并执行)→ `git checkout <tag>` → 按序执行未记账迁移并记账 → `compose build` → `up -d` → 健康检查(compose ps 全 running/healthy,带重试;可选再 curl `UPDATE_HEALTHCHECK_URL`)→ 写 `state/current-version`。
  - **任一步失败自动回滚代码层**(checkout 回旧 tag → build → up → 再健康检查),输出里标明已执行的迁移不撤销、数据层如受影响需从备份恢复。
  - BREAKING 版本默认拒绝(退出码 6),需 `--yes`。
  - `--dry-run`:只打印将执行的动作(git/docker/状态写入全跳过),供预览与测试。
- `rollback`:回到 `state/previous-version` 记录的上一 tag,重建重启。**只回代码/镜像层**;数据层(已执行迁移、应用数据)如需恢复,用输出里给的备份路径手工恢复。
- `status`:当前版本 + 容器健康 + 上次更新结果,JSON。
- 退出码总表:0 成功/已最新;1 apply 未完成(已回滚或未起步)或 rollback 后不健康;2 用法/配置错误;5 锁被占用;6 BREAKING 未加 --yes;7 apply 失败且回滚后仍不健康(人工介入);10/20 仅 check。

## 5. 与现有主实例流程的关系

| | owner 主实例 | 其他实例(本机制) |
|---|---|---|
| 代码基线 | main(手动 `git pull`) | 发布 tag `vX.Y.Z` |
| 部署入口 | `scripts/docker-prod-check.sh --build --up` | `deploy/update.sh apply` |
| 配置 | 宿主机 docker.env(单份) | 每实例目录 `.env` + `compose.override.yml` |
| 迁移 | 随手工部署人工处理 | `deploy/migrations/` 自动按实例记账执行 |

主实例以后如想并轨,把它也做成一个实例目录即可,机制通用;在那之前两条路互不干扰。

## 6. 注意事项与排障

- 实例的代码 clone 是 update.sh 的工作对象,工作区必须保持干净(update.sh 会拒绝脏工作区);不要在实例 clone 里做开发。
- 锁:并发第二次 apply/rollback 会被拒绝(退出码 5)。若确认没有更新在跑但仍报锁占用(仅限 mkdir 退化锁、且持锁进程已死时会自动接管;flock 锁进程死掉即自动释放),检查 `state/update.lock.d/pid`。
- 备份只含 `.env` 快照 + `state/` 快照 + `UPDATE_BACKUP_CMD` 产物;`data/` 大数据不自动备份,重要数据的备份策略实例自行安排(可挂进 `UPDATE_BACKUP_CMD`)。
- 凭据纪律:token/密码只放宿主机文件由 compose 只读挂载,绝不进 `UPDATE_HEALTHCHECK_URL`、不进命令行参数、不进本仓库。
- 迁移脚本约定(幂等、单调编号、每实例恰好一次)见 `deploy/migrations/README.md`。
