# deploy/migrations — 实例升级迁移脚本

`deploy/update.sh apply` 在切到目标版本后、`docker compose build` 之前，按文件名顺序执行本目录里**尚未记账**的迁移脚本。

## 命名与执行规则

- 文件名格式：`NNN_描述.sh`，`NNN` 为三位数字且**单调递增**（`001_...`、`002_...`），不复用、不插号。
- 只识别匹配 `[0-9]{3}_*.sh` 的文件；本README等其他文件被忽略。
- 以 `bash <脚本>` 执行（不要求可执行位），工作目录为仓库根。
- 每个脚本对**每个实例恰好执行一次**：成功后其文件名立即追加到实例的 `state/migrations-applied`（一行一个），已记账的跳过。
- 传入环境变量：
  - `RTIME_INSTANCE_DIR` 实例目录绝对路径
  - `RTIME_REPO_DIR` 仓库根绝对路径
  - `RTIME_UPDATE_FROM` / `RTIME_UPDATE_TO` 本次升级的起止版本（如 `v0.1.0` / `v0.2.0`）
- 任一脚本失败即中止升级并自动回滚代码层；**已成功记账的迁移不会撤销**（数据层恢复靠 `state/backups/` 里的快照与 `UPDATE_BACKUP_CMD` 产物）。

## 幂等约定（必须遵守）

记账机制保证"正常路径只跑一次"，但脚本仍必须写成**幂等**的：执行到一半失败后重跑（记账发生在成功之后）、或人工手动重放时，重复执行不得造成二次破坏。做法：

- 动手前先探测目标状态，已达成就直接 `exit 0`（例：目录已搬走、配置键已存在）。
- 用 `mkdir -p`、`cp -n`、"先写临时文件再原子改名"等天然幂等的操作。
- 破坏性动作（删除/覆盖）前先确认源头形态符合预期，不符合就报错退出而不是硬来。
- 脚本自身 `set -euo pipefail`，任何失败以非零退出让 update.sh 感知。

## 这里放什么、不放什么

- **应用能自己迁的，走应用启动迁移**（如程序启动时自动升级自己的数据文件格式），不放这里。
- 这里只放**应用管不了的**一次性动作：
  - volume/bind mount 数据搬移、目录结构调整；
  - 实例配置格式升级（如 `.env` 键改名——注意只能改 `state/` 之外由脚本负责提示、由人确认的部分，实例 `.env` 本身属实例私有，脚本应输出指引而非直接改写）;
  - 一次性数据修复。
- 对应版本的 CHANGELOG 条目请加 `MIGRATION: NNN_描述.sh` 行，让 `update.sh check` 的使用者有预期。

## 示例骨架

```bash
#!/usr/bin/env bash
# 003_move_sessions_dir.sh — 把 data/sessions 搬到 data/runtime/sessions
set -euo pipefail
src="${RTIME_INSTANCE_DIR:?}/data/sessions"
dst="${RTIME_INSTANCE_DIR:?}/data/runtime/sessions"
[ -d "$src" ] || exit 0        # 没有旧目录 = 无事可做(幂等)
[ -e "$dst" ] && { echo "目标已存在,人工确认: $dst" >&2; exit 1; }
mkdir -p "$(dirname "$dst")"
mv "$src" "$dst"
```
