#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# 安装/更新 Obsidian 本地网关为 macOS launchd 常驻服务(开机自启,监听 127.0.0.1:8765)。
# 供 Obsidian 插件「本机 Claude Code(Opus)」provider 使用;仅 macOS。
# 复刻既有 com.rtime.obsidian-local-gateway 服务,改完代码重跑本脚本即热更新。
#
# 设计要点(见 docs/development-log-2026-06-19-obsidian-local-claude.md):
#   - 用 /usr/bin/env -i 起干净环境,只显式给 HOME/PATH/RTIME_OBSIDIAN_*,避免继承杂环境
#   - node 用绝对路径(launchd 不读 shell profile;裸名会退码 78)
#   - PATH 含 /opt/homebrew/bin,让网关 spawn 的 claude 能找到
#   - 订阅 OAuth token 由网关自己从 ~/.config/rtime/obsidian-gateway.env(600)读,不写进 plist
#   - RTIME_OBSIDIAN_RUNNER 默认 remote-claude-kimi(非 claude-local 请求走 kimi);
#     claude-local(本机 Opus)由请求里的 model_provider_id 路由,不受此默认影响
#   - 生成的 plist 落 ~/Library/LaunchAgents(含本机绝对路径,故不入库);入库的只有本脚本
#     (全部用 $HOME / 脚本相对路径 / command -v node 计算,无硬编码个人主目录)
set -euo pipefail

LABEL="com.rtime.obsidian-local-gateway"
HOST="${RTIME_OBSIDIAN_HOST:-127.0.0.1}"
PORT="${RTIME_OBSIDIAN_PORT:-8765}"
RUNNER="${RTIME_OBSIDIAN_RUNNER:-remote-claude-kimi}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
GATEWAY="$REPO_ROOT/apps/obsidian-rtime-assistant/dev/local-gateway.mjs"
WORKDIR="$REPO_ROOT/apps/obsidian-rtime-assistant"
NODE="$(command -v node || true)"; [ -n "$NODE" ] || NODE="/opt/homebrew/bin/node"
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
LOG="$HOME/Library/Logs/rtime-obsidian-local-gateway.log"
ERRLOG="$HOME/Library/Logs/rtime-obsidian-local-gateway.err.log"
UID_NUM="$(id -u)"

[ -f "$GATEWAY" ] || { echo "找不到网关脚本: $GATEWAY" >&2; exit 1; }
[ -x "$NODE" ] || { echo "找不到可执行 node: $NODE" >&2; exit 1; }
mkdir -p "$(dirname "$PLIST")" "$(dirname "$LOG")"

cat > "$PLIST" <<PLISTEOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$LABEL</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/env</string>
    <string>-i</string>
    <string>HOME=$HOME</string>
    <string>PATH=/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin</string>
    <string>RTIME_OBSIDIAN_HOST=$HOST</string>
    <string>RTIME_OBSIDIAN_PORT=$PORT</string>
    <string>RTIME_OBSIDIAN_RUNNER=$RUNNER</string>
    <string>$NODE</string>
    <string>$GATEWAY</string>
  </array>
  <key>WorkingDirectory</key><string>$WORKDIR</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ProcessType</key><string>Background</string>
  <key>StandardOutPath</key><string>$LOG</string>
  <key>StandardErrorPath</key><string>$ERRLOG</string>
</dict>
</plist>
PLISTEOF

# bootout 是异步的,紧接着 bootstrap 会撞 "5: Input/output error";重试到成功为止
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
bootstrapped=0
for _ in 1 2 3 4 5 6 7 8 9 10 11 12; do
  if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null; then bootstrapped=1; break; fi
  launchctl print "gui/$UID_NUM/$LABEL" >/dev/null 2>&1 || true  # 占位等 bootout 落定(不用 sleep)
done
[ "$bootstrapped" = 1 ] || { echo "launchctl bootstrap 多次失败;稍后手动重跑本脚本" >&2; exit 1; }
launchctl kickstart -k "gui/$UID_NUM/$LABEL"

echo "已安装/更新并重启: $LABEL  →  http://$HOST:$PORT"
echo "日志: $LOG ; $ERRLOG"
echo "停止并卸载: launchctl bootout gui/$UID_NUM/$LABEL && rm '$PLIST'"
