#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# deploy/update.sh — 实例一键更新执行器。
#
# 发布通道:main 上的附注 tag vX.Y.Z(owner 发布时打)。实例侧"有新版"判定 =
# git fetch --tags 后存在比当前 checkout 版本更高的 v* tag。
#
# 实例目录约定(见 docs/instance-deploy.zh-CN.md):
#   <instance>/.env                  实例配置(compose --env-file 用)
#   <instance>/compose.override.yml  实例差异(端口/挂载/profile 等)
#   <instance>/data/                 bind mount 数据
#   <instance>/state/                current-version、migrations-applied、backups/、update.lock
# 升级流程只写 state/(含 backups/),永不写实例目录其他部分。
#
# 用法:
#   deploy/update.sh check    --instance <dir> [--repo <dir>]
#   deploy/update.sh apply    --instance <dir> [--repo <dir>] [--version vX.Y.Z] [--yes] [--dry-run]
#   deploy/update.sh rollback --instance <dir> [--repo <dir>] [--dry-run]
#   deploy/update.sh status   --instance <dir> [--repo <dir>]
#   --instance 可用环境变量 RTIME_INSTANCE_DIR 代替;--repo 默认取本脚本所在仓库根。
#
# compose 统一调用形态:
#   docker compose -f <repo>/compose.prod.yml -f <instance>/compose.override.yml \
#     --env-file <instance>/.env -p rtime-<name> <cmd>
#
# 实例 .env 里本脚本认的可选键(其余键全部留给 compose,本脚本不 source .env):
#   UPDATE_INSTANCE_NAME    compose 项目名后缀,默认实例目录名
#   UPDATE_BACKUP_CMD       应用级备份钩子(如 DB dump),备份阶段以 bash -c 执行,
#                           可用环境变量 RTIME_INSTANCE_DIR/RTIME_BACKUP_DIR/RTIME_REPO_DIR
#   UPDATE_HEALTHCHECK_URL  健康检查额外 curl 校验的 URL(勿在 URL 里带 token/凭据)
#   UPDATE_HEALTH_RETRIES   健康检查重试次数,默认 10
#   UPDATE_HEALTH_INTERVAL  健康检查重试间隔秒,默认 6
#
# 退出码:
#    0  成功 / check=已最新
#    1  apply 未完成,系统仍在(或已回滚到)旧版本;或 rollback 后健康检查未过
#    2  用法/配置错误(参数缺失、仓库脏、tag 不存在等)
#    5  锁被占用(另一次更新正在进行)
#    6  目标区间含 BREAKING 且未加 --yes,拒绝执行
#    7  apply 失败且回滚后健康检查仍失败(需人工介入)
#   10  check: 有可用更新
#   20  check: 有可用更新且目标区间 CHANGELOG 含 BREAKING:
#
# 输出约定:JSON 单行到 stdout;人类日志到 stderr。token/凭据绝不进 URL 或命令行参数。
set -Eeuo pipefail

SCRIPT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)

SUBCMD=""
INSTANCE_DIR="${RTIME_INSTANCE_DIR:-}"
REPO_DIR=""
TARGET_VERSION=""
ASSUME_YES=0
DRY_RUN=0
LOCKDIR_HELD=""

log() { printf '[update] %s\n' "$*" >&2; }
die() {
  log "错误: $*"
  emit_json "{\"status\":\"error\",\"message\":\"$(json_escape "$*")\"}"
  exit 2
}

usage() {
  sed -n '2,50p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2
}

# ---------- JSON 工具 ----------

json_escape() {
  local s=$1
  s=${s//\\/\\\\}
  s=${s//\"/\\\"}
  s=${s//$'\n'/\\n}
  s=${s//$'\r'/\\r}
  s=${s//$'\t'/\\t}
  printf '%s' "$s"
}

# 换行分隔的列表 -> JSON 字符串数组
json_array() {
  local out="[" first=1 line
  while IFS= read -r line; do
    [ -n "$line" ] || continue
    if [ "$first" -eq 1 ]; then first=0; else out+=","; fi
    out+="\"$(json_escape "$line")\""
  done
  out+="]"
  printf '%s' "$out"
}

emit_json() { printf '%s\n' "$1"; }

# ---------- 实例 .env 读取(只读特定键,不 source) ----------

env_get() { # $1=key $2=default
  local key=$1 def=${2-} line val
  line=$(grep -E "^[[:space:]]*${key}=" "$INSTANCE_DIR/.env" 2>/dev/null | tail -n 1 || true)
  if [ -z "$line" ]; then
    printf '%s' "$def"
    return 0
  fi
  val=${line#*=}
  # 去掉成对的首尾引号
  if [[ $val == \"*\" ]]; then val=${val#\"}; val=${val%\"}; fi
  if [[ $val == \'*\' ]]; then val=${val#\'}; val=${val%\'}; fi
  printf '%s' "$val"
}

# ---------- git / 版本工具 ----------

g() { git -C "$REPO_DIR" "$@"; }

fetch_tags() {
  if [ -n "$(g remote 2>/dev/null)" ]; then
    if ! g fetch --tags --force --quiet 2>/dev/null; then
      log "警告: git fetch --tags 失败,继续用本地已有 tag(结果可能滞后)"
    fi
  else
    log "警告: 仓库无 remote,跳过 fetch,用本地 tag"
  fi
}

current_tag() {
  g describe --tags --match 'v[0-9]*' --abbrev=0 2>/dev/null || printf 'none'
}

latest_tag() {
  local t
  t=$(g tag --list 'v[0-9]*' --sort=-v:refname | head -n 1)
  printf '%s' "${t:-none}"
}

# ver_gt A B => A 严格大于 B(语义化 vX.Y.Z;none 视为最小)
ver_gt() {
  local a=${1#v} b=${2#v}
  [ "$1" = "$2" ] && return 1
  [ "$1" = "none" ] && return 1
  [ "$2" = "none" ] && return 0
  local IFS=.
  local -a A=($a) B=($b)
  local i x y
  for i in 0 1 2; do
    x=${A[i]:-0}; y=${B[i]:-0}
    # 非纯数字段按 0 处理(通道约定 tag 恒为 vX.Y.Z)
    [[ $x =~ ^[0-9]+$ ]] || x=0
    [[ $y =~ ^[0-9]+$ ]] || y=0
    if [ "$x" -gt "$y" ]; then return 0; fi
    if [ "$x" -lt "$y" ]; then return 1; fi
  done
  return 1
}

# (from, to] 区间内的 tag,按版本升序
tags_between() { # $1=from $2=to
  local t
  g tag --list 'v[0-9]*' --sort=v:refname | while IFS= read -r t; do
    if ver_gt "$t" "$1" && { [ "$t" = "$2" ] || ! ver_gt "$t" "$2"; }; then
      printf '%s\n' "$t"
    fi
  done
}

changelog_at() { # $1=ref;取该 ref 下的 CHANGELOG.md,拿不到则回退工作区副本
  g show "$1:CHANGELOG.md" 2>/dev/null || cat "$REPO_DIR/CHANGELOG.md" 2>/dev/null || true
}

changelog_section() { # stdin=changelog 全文 $1=版本号(不带 v)
  awk -v ver="$1" '
    index($0, "## [" ver "]") == 1 { insec = 1; print; next }
    insec && /^## / { exit }
    insec { print }
  '
}

# (from, to] 区间任一版本的 CHANGELOG 节含 BREAKING: 即真
breaking_in_range() { # $1=from $2=to
  local t sec cl
  cl=$(changelog_at "$2")
  while IFS= read -r t; do
    [ -n "$t" ] || continue
    sec=$(printf '%s\n' "$cl" | changelog_section "${t#v}")
    if printf '%s\n' "$sec" | grep -q 'BREAKING:'; then
      return 0
    fi
  done < <(tags_between "$1" "$2")
  return 1
}

migrations_at() { # $1=ref;该 ref 下 deploy/migrations/ 的 NNN_*.sh 文件名,升序
  g ls-tree -r --name-only "$1" -- deploy/migrations 2>/dev/null \
    | sed 's#.*/##' \
    | grep -E '^[0-9]{3}_.+\.sh$' \
    | LC_ALL=C sort || true
}

pending_migrations() { # $1=ref;减去 state/migrations-applied 里已记账的
  local ledger="$STATE_DIR/migrations-applied" name
  migrations_at "$1" | while IFS= read -r name; do
    [ -n "$name" ] || continue
    if [ -f "$ledger" ] && grep -Fxq "$name" "$ledger"; then
      continue
    fi
    printf '%s\n' "$name"
  done
}

# ---------- 锁(flock 优先;无 flock 的平台退化为 mkdir 锁) ----------

on_exit() {
  if [ -n "$LOCKDIR_HELD" ]; then
    rm -rf "$LOCKDIR_HELD" 2>/dev/null || true
  fi
}
trap on_exit EXIT

lock_busy() {
  log "锁被占用: 另一次更新正在进行 ($STATE_DIR/update.lock)"
  emit_json '{"status":"locked","message":"another update is in progress"}'
  exit 5
}

acquire_lock() {
  mkdir -p "$STATE_DIR"
  local lockfile="$STATE_DIR/update.lock"
  local method="${RTIME_UPDATE_LOCK_METHOD:-auto}"
  if [ "$method" = auto ]; then
    if command -v flock >/dev/null 2>&1; then method=flock; else method=dir; fi
  fi
  if [ "$method" = flock ]; then
    exec 9>"$lockfile"
    flock -n 9 || lock_busy
  else
    local lockdir="$lockfile.d" pid
    if ! mkdir "$lockdir" 2>/dev/null; then
      pid=$(cat "$lockdir/pid" 2>/dev/null || true)
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        lock_busy
      fi
      log "发现失效锁(持有进程 ${pid:-未知} 已退出),接管"
      rm -rf "$lockdir"
      mkdir "$lockdir" 2>/dev/null || lock_busy
    fi
    printf '%s\n' "$$" >"$lockdir/pid"
    LOCKDIR_HELD="$lockdir"
  fi
}

# ---------- compose / 健康检查 ----------

COMPOSE=()
build_compose_cmd() {
  local raw name
  raw=$(env_get UPDATE_INSTANCE_NAME "$(basename "$INSTANCE_DIR")")
  name=$(printf '%s' "$raw" | tr '[:upper:]' '[:lower:]' | sed -e 's/[^a-z0-9_-]/-/g')
  PROJECT_NAME="rtime-${name}"
  COMPOSE=(docker compose -f "$REPO_DIR/compose.prod.yml")
  if [ -f "$INSTANCE_DIR/compose.override.yml" ]; then
    COMPOSE+=(-f "$INSTANCE_DIR/compose.override.yml")
  else
    log "警告: 实例缺 compose.override.yml,只用 compose.prod.yml(实例约定应提供该文件)"
  fi
  COMPOSE+=(--env-file "$INSTANCE_DIR/.env" -p "$PROJECT_NAME")
}

compose_ps_json() {
  "${COMPOSE[@]}" ps --all --format json 2>/dev/null
}

# 所有容器 running 且(若有健康探针)healthy 才算过
compose_ps_healthy() {
  local out
  out=$(compose_ps_json) || return 1
  if command -v python3 >/dev/null 2>&1; then
    printf '%s\n' "$out" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
items = []
if raw.startswith("["):
    items = json.loads(raw)
else:
    for line in raw.splitlines():
        line = line.strip()
        if line:
            items.append(json.loads(line))
if not items:
    sys.stderr.write("no containers\n")
    sys.exit(1)
bad = []
for it in items:
    state = (it.get("State") or "").lower()
    health = (it.get("Health") or "").lower()
    if state != "running" or health not in ("", "healthy"):
        bad.append("%s:%s/%s" % (it.get("Service") or it.get("Name"), state, health or "-"))
if bad:
    sys.stderr.write("unhealthy: " + ", ".join(bad) + "\n")
    sys.exit(1)
' >&2
  else
    # 降级:无 python3 时做文本判定
    [ -n "$out" ] || return 1
    printf '%s\n' "$out" | grep -q '"State" *: *"running"' || return 1
    if printf '%s\n' "$out" | grep -Eqi '"State" *: *"(exited|dead|restarting|paused|created)"'; then
      return 1
    fi
    if printf '%s\n' "$out" | grep -qi '"Health" *: *"unhealthy"'; then
      return 1
    fi
  fi
}

health_check() {
  local retries interval url attempt
  retries=$(env_get UPDATE_HEALTH_RETRIES 10)
  interval=$(env_get UPDATE_HEALTH_INTERVAL 6)
  url=$(env_get UPDATE_HEALTHCHECK_URL "")
  [[ $retries =~ ^[0-9]+$ ]] || retries=10
  [[ $interval =~ ^[0-9]+$ ]] || interval=6
  for ((attempt = 1; attempt <= retries; attempt++)); do
    if compose_ps_healthy; then
      if [ -z "$url" ]; then
        log "健康检查通过 (attempt $attempt/$retries)"
        return 0
      fi
      if curl -fsS -o /dev/null --max-time 10 "$url" 2>/dev/null; then
        log "健康检查通过(容器 + URL) (attempt $attempt/$retries)"
        return 0
      fi
      log "healthcheck URL 未通过 (attempt $attempt/$retries)"
    else
      log "容器健康检查未通过 (attempt $attempt/$retries)"
    fi
    if [ "$attempt" -lt "$retries" ]; then
      sleep "$interval"
    fi
  done
  return 1
}

# ---------- 变更操作包装(dry-run 只打印) ----------

run_mut() {
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: 将执行: $*"
    return 0
  fi
  log "+ $*"
  "$@"
}

now_iso() { date -u +%Y-%m-%dT%H:%M:%SZ; }

write_last_update() { # $1=action $2=from $3=to $4=status $5=backup $6=migrations(逗号JSON数组)
  [ "$DRY_RUN" -eq 0 ] || return 0
  printf '{"timestamp":"%s","action":"%s","from":"%s","to":"%s","status":"%s","backup":"%s","migrations_run":%s}\n' \
    "$(now_iso)" "$1" "$(json_escape "$2")" "$(json_escape "$3")" "$4" "$(json_escape "$5")" "$6" \
    >"$STATE_DIR/last-update.json"
}

# ---------- 子命令: check ----------

cmd_check() {
  fetch_tags
  local current latest update_available=false breaking=false
  current=$(current_tag)
  latest=$(latest_tag)
  local excerpt="" pending_json="[]" ref
  if [ "$latest" != "none" ] && ver_gt "$latest" "$current"; then
    update_available=true
  fi
  if [ "$update_available" = true ]; then
    ref=$latest
    if breaking_in_range "$current" "$latest"; then breaking=true; fi
    excerpt=$(changelog_at "$latest" | changelog_section "${latest#v}" | head -n 30)
  else
    ref=HEAD
  fi
  pending_json=$(pending_migrations "$ref" | json_array)
  local migration_pending=false
  [ "$pending_json" = "[]" ] || migration_pending=true
  emit_json "{\"current\":\"$(json_escape "$current")\",\"latest\":\"$(json_escape "$latest")\",\"update_available\":$update_available,\"breaking\":$breaking,\"migration_pending\":$migration_pending,\"pending_migrations\":$pending_json,\"changelog_excerpt\":\"$(json_escape "$excerpt")\"}"
  if [ "$update_available" = true ]; then
    if [ "$breaking" = true ]; then exit 20; fi
    exit 10
  fi
  exit 0
}

# ---------- 子命令: apply ----------

do_backup() { # 设置全局 BACKUP_DIR;失败返回非零
  local ts
  ts=$(date +%Y%m%d-%H%M%S)
  BACKUP_DIR="$STATE_DIR/backups/$ts"
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: 将备份 .env + state 快照到 $BACKUP_DIR"
    return 0
  fi
  # 注意:本函数以 `do_backup || 处理` 形式调用,errexit 在函数内被抑制,
  # 关键步骤必须逐条 `|| return 1`。
  mkdir -p "$BACKUP_DIR" || return 1
  cp "$INSTANCE_DIR/.env" "$BACKUP_DIR/env.snapshot" || return 1
  if [ -f "$INSTANCE_DIR/compose.override.yml" ]; then
    cp "$INSTANCE_DIR/compose.override.yml" "$BACKUP_DIR/compose.override.yml.snapshot" || return 1
  fi
  tar -czf "$BACKUP_DIR/state-snapshot.tar.gz" \
    --exclude 'state/backups*' --exclude 'state/update.lock*' \
    -C "$INSTANCE_DIR" state || return 1
  local backup_cmd
  backup_cmd=$(env_get UPDATE_BACKUP_CMD "")
  if [ -n "$backup_cmd" ]; then
    log "执行应用级备份钩子 UPDATE_BACKUP_CMD"
    RTIME_INSTANCE_DIR="$INSTANCE_DIR" RTIME_BACKUP_DIR="$BACKUP_DIR" RTIME_REPO_DIR="$REPO_DIR" \
      bash -c "$backup_cmd" || return 1
  fi
  log "备份完成: $BACKUP_DIR"
}

# 回滚代码/镜像层到 $1,返回 0=回滚后健康
rollback_code_layer() { # $1=ref
  log "回滚代码层到 $1"
  run_mut git -C "$REPO_DIR" checkout --quiet --detach "$1" || return 1
  run_mut "${COMPOSE[@]}" build || return 1
  run_mut "${COMPOSE[@]}" up -d --remove-orphans || return 1
  health_check
}

fail_and_rollback() { # $1=reason $2=from(旧版本) $3=target $4=migrations_json
  local reason=$1 from=$2 target=$3 migs=$4
  log "apply 失败: $reason;开始自动回滚代码层"
  local rb_ref=$from
  [ "$rb_ref" != "none" ] || rb_ref=$OLD_REF
  local rb_healthy=true rb_exit=1
  if ! rollback_code_layer "$rb_ref"; then
    rb_healthy=false
    rb_exit=7
  fi
  write_last_update apply "$from" "$target" "failed-rolled-back" "${BACKUP_DIR:-}" "$migs"
  local note="代码/镜像层已回滚;已执行过的迁移不会自动撤销,数据层如受影响请从备份恢复: ${BACKUP_DIR:-无}"
  emit_json "{\"status\":\"rolled-back\",\"from\":\"$(json_escape "$from")\",\"attempted\":\"$(json_escape "$target")\",\"reason\":\"$(json_escape "$reason")\",\"rollback_healthy\":$rb_healthy,\"backup\":\"$(json_escape "${BACKUP_DIR:-}")\",\"migrations_run\":$migs,\"note\":\"$(json_escape "$note")\"}"
  exit "$rb_exit"
}

cmd_apply() {
  if [ "$DRY_RUN" -eq 0 ]; then
    acquire_lock
  else
    log "dry-run: 跳过加锁与一切写操作"
  fi
  fetch_tags
  local current target
  current=$(current_tag)
  OLD_REF=$(g rev-parse HEAD)
  if [ -n "$TARGET_VERSION" ]; then
    target=$TARGET_VERSION
    g rev-parse -q --verify "refs/tags/$target" >/dev/null || die "tag 不存在: $target"
  else
    target=$(latest_tag)
    [ "$target" != "none" ] || die "仓库没有任何 v* tag,无法确定目标版本"
  fi

  local recorded=""
  [ -f "$STATE_DIR/current-version" ] && recorded=$(cat "$STATE_DIR/current-version")
  if [ "$target" = "$current" ] && [ "$recorded" = "$target" ]; then
    log "已在目标版本 $target,无事可做"
    emit_json "{\"status\":\"already-current\",\"current\":\"$(json_escape "$current")\"}"
    exit 0
  fi

  # BREAKING 门:目标区间任一版本 CHANGELOG 含 BREAKING: 时默认拒绝
  if ver_gt "$target" "$current" && breaking_in_range "$current" "$target"; then
    if [ "$ASSUME_YES" -ne 1 ]; then
      log "目标区间 ($current, $target] 含 BREAKING 变更,默认拒绝;确认已读 CHANGELOG 后加 --yes 重试"
      emit_json "{\"status\":\"refused-breaking\",\"current\":\"$(json_escape "$current")\",\"target\":\"$(json_escape "$target")\",\"message\":\"target range contains BREAKING changes; re-run with --yes\"}"
      exit 6
    fi
    log "目标区间含 BREAKING,已加 --yes,继续"
  fi
  if ! ver_gt "$target" "$current" && [ "$target" != "$current" ]; then
    log "警告: 目标 $target 不高于当前 $current(降级操作),继续执行"
  fi

  # 仓库工作区必须干净(避免 checkout 吃掉本地改动)
  if [ -n "$(g status --porcelain)" ]; then
    if [ "$DRY_RUN" -eq 1 ]; then
      log "警告: 仓库工作区不干净,真实 apply 会拒绝"
    else
      die "仓库工作区不干净($REPO_DIR),先提交/清理再更新"
    fi
  fi

  local pending pending_json
  pending=$(pending_migrations "$target")
  pending_json=$(printf '%s\n' "$pending" | json_array)

  if [ "$DRY_RUN" -eq 1 ]; then
    do_backup
    log "dry-run: 将执行: git -C $REPO_DIR checkout --detach $target"
    local m
    while IFS= read -r m; do
      [ -n "$m" ] || continue
      log "dry-run: 将执行迁移: deploy/migrations/$m 并记账"
    done <<<"$pending"
    log "dry-run: 将执行: ${COMPOSE[*]} build"
    log "dry-run: 将执行: ${COMPOSE[*]} up -d --remove-orphans"
    log "dry-run: 将做健康检查,成功后写 state/current-version=$target"
    emit_json "{\"status\":\"dry-run\",\"from\":\"$(json_escape "$current")\",\"to\":\"$(json_escape "$target")\",\"pending_migrations\":$pending_json,\"backup\":\"$(json_escape "$BACKUP_DIR")\"}"
    exit 0
  fi

  do_backup || {
    write_last_update apply "$current" "$target" "backup-failed" "${BACKUP_DIR:-}" "[]"
    emit_json "{\"status\":\"backup-failed\",\"current\":\"$(json_escape "$current")\",\"target\":\"$(json_escape "$target")\",\"note\":\"备份钩子失败,未做任何变更\"}"
    exit 1
  }

  # 代码层切换
  if ! run_mut git -C "$REPO_DIR" checkout --quiet --detach "$target"; then
    run_mut git -C "$REPO_DIR" checkout --quiet --detach "$OLD_REF" || true
    write_last_update apply "$current" "$target" "checkout-failed" "$BACKUP_DIR" "[]"
    emit_json "{\"status\":\"checkout-failed\",\"current\":\"$(json_escape "$current")\",\"target\":\"$(json_escape "$target")\",\"backup\":\"$(json_escape "$BACKUP_DIR")\"}"
    exit 1
  fi

  # 迁移:按序执行未记账的 NNN_*.sh,每个成功后立即记账(幂等约定见 deploy/migrations/README.md)
  local m migs_json="[]"
  local -a ran=()
  while IFS= read -r m; do
    [ -n "$m" ] || continue
    log "执行迁移: deploy/migrations/$m"
    if ! (cd "$REPO_DIR" && RTIME_INSTANCE_DIR="$INSTANCE_DIR" RTIME_REPO_DIR="$REPO_DIR" \
        RTIME_UPDATE_FROM="$current" RTIME_UPDATE_TO="$target" \
        bash "deploy/migrations/$m" </dev/null); then
      migs_json=$(printf '%s\n' "${ran[@]:-}" | json_array)
      fail_and_rollback "migration $m failed" "$current" "$target" "$migs_json"
    fi
    printf '%s\n' "$m" >>"$STATE_DIR/migrations-applied"
    ran+=("$m")
  done <<<"$pending"
  migs_json=$(printf '%s\n' "${ran[@]:-}" | json_array)

  # 构建 + 启动 + 健康检查
  if ! run_mut "${COMPOSE[@]}" build; then
    fail_and_rollback "docker compose build failed" "$current" "$target" "$migs_json"
  fi
  if ! run_mut "${COMPOSE[@]}" up -d --remove-orphans; then
    fail_and_rollback "docker compose up failed" "$current" "$target" "$migs_json"
  fi
  if ! health_check; then
    fail_and_rollback "health check failed" "$current" "$target" "$migs_json"
  fi

  printf '%s\n' "$current" >"$STATE_DIR/previous-version"
  printf '%s\n' "$target" >"$STATE_DIR/current-version"
  write_last_update apply "$current" "$target" "updated" "$BACKUP_DIR" "$migs_json"
  log "更新成功: $current -> $target"
  emit_json "{\"status\":\"updated\",\"from\":\"$(json_escape "$current")\",\"to\":\"$(json_escape "$target")\",\"backup\":\"$(json_escape "$BACKUP_DIR")\",\"migrations_run\":$migs_json}"
  exit 0
}

# ---------- 子命令: rollback ----------

cmd_rollback() {
  if [ "$DRY_RUN" -eq 0 ]; then
    acquire_lock
  fi
  local prev cur
  prev=$(cat "$STATE_DIR/previous-version" 2>/dev/null || true)
  [ -n "$prev" ] || die "state/previous-version 不存在,没有可回滚的记录"
  cur=$(cat "$STATE_DIR/current-version" 2>/dev/null || current_tag)
  local latest_backup
  latest_backup=$(ls -1d "$STATE_DIR/backups"/*/ 2>/dev/null | LC_ALL=C sort | tail -n 1 || true)
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: 将回滚 $cur -> $prev(仅代码/镜像层),备份参考: ${latest_backup:-无}"
    emit_json "{\"status\":\"dry-run\",\"from\":\"$(json_escape "$cur")\",\"to\":\"$(json_escape "$prev")\",\"latest_backup\":\"$(json_escape "$latest_backup")\"}"
    exit 0
  fi
  local rb_ref=$prev
  [ "$rb_ref" != "none" ] || die "上一版本记录为 none,无法回滚(请手工指定 apply --version)"
  local healthy=true code=0
  if ! rollback_code_layer "$rb_ref"; then
    healthy=false
    code=1
  fi
  printf '%s\n' "$prev" >"$STATE_DIR/current-version"
  printf '%s\n' "$cur" >"$STATE_DIR/previous-version"
  write_last_update rollback "$cur" "$prev" "rolled-back-manual" "${latest_backup:-}" "[]"
  local note="仅回滚代码/镜像层;数据层(已执行迁移/应用数据)如需恢复,请用备份: ${latest_backup:-无}"
  log "$note"
  emit_json "{\"status\":\"rolled-back\",\"from\":\"$(json_escape "$cur")\",\"to\":\"$(json_escape "$prev")\",\"healthy\":$healthy,\"latest_backup\":\"$(json_escape "$latest_backup")\",\"note\":\"$(json_escape "$note")\"}"
  exit "$code"
}

# ---------- 子命令: status ----------

cmd_status() {
  local recorded describe containers="\"unknown\"" last="null"
  recorded=$(cat "$STATE_DIR/current-version" 2>/dev/null || true)
  describe=$(g describe --tags --always 2>/dev/null || true)
  local out
  if out=$(compose_ps_json) && [ -n "$out" ] && command -v python3 >/dev/null 2>&1; then
    containers=$(printf '%s\n' "$out" | python3 -c '
import json, sys
raw = sys.stdin.read().strip()
items = []
try:
    if raw.startswith("["):
        items = json.loads(raw)
    else:
        for line in raw.splitlines():
            line = line.strip()
            if line:
                items.append(json.loads(line))
except Exception:
    print("\"unknown\"")
    sys.exit(0)
out = [
    {
        "service": it.get("Service") or it.get("Name"),
        "state": it.get("State"),
        "health": it.get("Health") or "",
    }
    for it in items
]
print(json.dumps(out, ensure_ascii=False))
' 2>/dev/null) || containers="\"unknown\""
  fi
  if [ -s "$STATE_DIR/last-update.json" ]; then
    last=$(tr -d '\n' <"$STATE_DIR/last-update.json")
  fi
  emit_json "{\"instance\":\"$(json_escape "$INSTANCE_DIR")\",\"current_version\":\"$(json_escape "${recorded:-unknown}")\",\"repo_describe\":\"$(json_escape "$describe")\",\"containers\":$containers,\"last_update\":$last}"
  exit 0
}

# ---------- 参数解析 ----------

[ "$#" -ge 1 ] || { usage; exit 2; }
SUBCMD=$1
shift
case "$SUBCMD" in
  check | apply | rollback | status) ;;
  -h | --help)
    usage
    exit 0
    ;;
  *)
    die "未知子命令: $SUBCMD(可用: check/apply/rollback/status)"
    ;;
esac

while [ "$#" -gt 0 ]; do
  case "$1" in
    --instance)
      shift
      INSTANCE_DIR="${1:-}"
      ;;
    --repo)
      shift
      REPO_DIR="${1:-}"
      ;;
    --version)
      shift
      TARGET_VERSION="${1:-}"
      ;;
    --yes)
      ASSUME_YES=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h | --help)
      usage
      exit 0
      ;;
    *)
      die "未知参数: $1"
      ;;
  esac
  shift
done

[ -n "$INSTANCE_DIR" ] || die "--instance 必填(或设 RTIME_INSTANCE_DIR)"
[ -d "$INSTANCE_DIR" ] || die "实例目录不存在: $INSTANCE_DIR"
INSTANCE_DIR=$(cd -- "$INSTANCE_DIR" && pwd)
[ -f "$INSTANCE_DIR/.env" ] || die "实例缺 .env: $INSTANCE_DIR/.env"
STATE_DIR="$INSTANCE_DIR/state"

if [ -z "$REPO_DIR" ]; then
  REPO_DIR=$(cd -- "$SCRIPT_DIR/.." && pwd)
fi
[ -d "$REPO_DIR" ] || die "仓库目录不存在: $REPO_DIR"
REPO_DIR=$(cd -- "$REPO_DIR" && pwd)
git -C "$REPO_DIR" rev-parse --git-dir >/dev/null 2>&1 || die "不是 git 仓库: $REPO_DIR"
[ -f "$REPO_DIR/compose.prod.yml" ] || die "仓库缺 compose.prod.yml: $REPO_DIR"

if [ -n "$TARGET_VERSION" ] && [[ ! $TARGET_VERSION =~ ^v[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  die "--version 需形如 vX.Y.Z: $TARGET_VERSION"
fi

build_compose_cmd

case "$SUBCMD" in
  check) cmd_check ;;
  apply) cmd_apply ;;
  rollback) cmd_rollback ;;
  status) cmd_status ;;
esac
