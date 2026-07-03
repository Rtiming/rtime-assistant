#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# qqbridge — operate the QQ bridge spike (NapCat + native bridge) on orange pi.
#
# Runs ON orange pi. Config from ./qqbridge.env (see qqbridge.env.example).
# From the Mac:  ssh orangepi 'cd ~/qq-bridge-spike/apps/qq-bridge/ops && ./qqbridge.sh <cmd>'
#
# Lessons baked in: NapCat uses `-q <ACCOUNT>` (ACCOUNT env) for quick-login from the
# saved session (no re-scan); the native bridge is detached with `setsid -f` and
# stopped via `fuser -k` on its port (a `pkill -f qq_bridge` matches this script's own
# command line and kills the shell); NapCat runs `--network host` so it reaches the
# bridge at 127.0.0.1.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${QQBRIDGE_ENV:-$HERE/qqbridge.env}"
[ -f "$ENV_FILE" ] && set -a && . "$ENV_FILE" && set +a

: "${SPIKE_DIR:=$HOME/qq-bridge-spike}"
: "${WS_PORT:=8080}"; : "${HTTP_PORT:=3000}"; : "${WEBUI_PORT:=6099}"
: "${NAPCAT_IMAGE:=mlikiowa/napcat-docker:latest}"; : "${NAPCAT_CONTAINER:=qqbr-napcat}"
: "${QQ_GROUP_INVITE_POLICY:=reject}"; : "${QQ_GROUP_ALLOWLIST:=}"
: "${QQ_OWNER_IDS:=}"; : "${QQ_ACCOUNT:=}"
APP_DIR="$SPIKE_DIR/apps/qq-bridge"
RUNTIME_SRC="$SPIKE_DIR/packages/rtime-chat-runtime/src"
BRIDGE_LOG="$SPIKE_DIR/bridge.log"
ARCHIVE="$SPIKE_DIR/qq-bridge-messages.jsonl"
RUN_LOG="$SPIKE_DIR/qq-bridge-run.jsonl"
API="http://127.0.0.1:$HTTP_PORT"

api() { curl -s --max-time 12 "$API/$1" -H 'Content-Type: application/json' -d "${2:-{}}"; }
rc()  { python3 -c 'import json,sys; print(json.load(sys.stdin).get("retcode"))' 2>/dev/null || echo '?'; }

napcat_up() {
  docker rm -f "$NAPCAT_CONTAINER" >/dev/null 2>&1 || true
  mkdir -p "$SPIKE_DIR/state/napcat/QQ" "$SPIKE_DIR/state/napcat/config"
  # NAPCAT_QUICK_PASSWORD:配了它,会话失效(被踢/过期)后 NapCat 自动用密码重登,
  # 无需人工扫码——把"掉线要扫码"变成秒级自动恢复。值来自 qqbridge.env(gitignored,
  # 服务器 600),绝不进 git。需与 ACCOUNT 同时设。见 qqbridge.env.example。
  docker run -d --name "$NAPCAT_CONTAINER" --network host \
    ${QQ_ACCOUNT:+-e ACCOUNT="$QQ_ACCOUNT"} \
    ${NAPCAT_QUICK_PASSWORD:+-e NAPCAT_QUICK_PASSWORD="$NAPCAT_QUICK_PASSWORD"} \
    -e NAPCAT_UID="$(id -u)" -e NAPCAT_GID="$(id -g)" \
    -e HTTP_PROXY= -e HTTPS_PROXY= -e FTP_PROXY= -e ALL_PROXY= \
    -e http_proxy= -e https_proxy= -e ftp_proxy= -e all_proxy= \
    -e NO_PROXY='*' -e no_proxy='*' \
    -v "$SPIKE_DIR/state/napcat/QQ:/app/.config/QQ" \
    -v "$SPIKE_DIR/state/napcat/config:/app/napcat/config" \
    --restart unless-stopped "$NAPCAT_IMAGE" >/dev/null
  echo "napcat up (account=${QQ_ACCOUNT:-<scan QR>}); WebUI :$WEBUI_PORT"
}

bridge_up() {
  fuser -k "$WS_PORT/tcp" 2>/dev/null || true; sleep 1
  cd "$APP_DIR"
  RTIME_CHAT_RUNTIME_SRC="$RUNTIME_SRC" \
  RTIME_ASSISTANT_RUN_LOG="$RUN_LOG" \
  QQ_OWNER_IDS="$QQ_OWNER_IDS" \
  QQ_BRIDGE_WS_HOST=127.0.0.1 QQ_BRIDGE_WS_PORT="$WS_PORT" \
  QQ_BRIDGE_ARCHIVE="$ARCHIVE" \
  QQ_GROUP_INVITE_POLICY="$QQ_GROUP_INVITE_POLICY" \
  QQ_GROUP_ALLOWLIST="$QQ_GROUP_ALLOWLIST" \
  PYTHONPATH="$APP_DIR" \
  setsid -f python3 -m qq_bridge >"$BRIDGE_LOG" 2>&1 </dev/null
  sleep 2; tail -1 "$BRIDGE_LOG"
}

case "${1:-help}" in
  up)            napcat_up; bridge_up ;;
  napcat-up)     napcat_up ;;
  napcat-down)   docker rm -f "$NAPCAT_CONTAINER" >/dev/null 2>&1 && echo "napcat down" ;;
  napcat-restart) docker restart "$NAPCAT_CONTAINER" >/dev/null && echo "napcat restarted (quick-login)" ;;
  bridge-up)     bridge_up ;;
  bridge-down)   fuser -k "$WS_PORT/tcp" 2>/dev/null && echo "bridge down" || echo "bridge not running" ;;
  bridge-restart) bridge_up ;;
  down)          fuser -k "$WS_PORT/tcp" 2>/dev/null || true; docker rm -f "$NAPCAT_CONTAINER" >/dev/null 2>&1 || true; echo "all down" ;;
  status)
    echo "== bridge healthz =="; curl -s --max-time 4 "http://127.0.0.1:$WS_PORT/healthz"; echo
    echo "== login =="; api get_login_info
    echo; echo "== groups =="; api get_group_list | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("data") or []), "groups")'
    echo "== archive lines =="; wc -l "$ARCHIVE" 2>/dev/null || echo "(none yet)"
    ;;
  qr)
    docker cp "$NAPCAT_CONTAINER:/app/napcat/cache/qrcode.png" "$SPIKE_DIR/qrcode.png" 2>&1 \
      && echo "QR -> $SPIKE_DIR/qrcode.png (scp it to your Mac to scan)" || echo "no QR (already logged in?)"
    ;;
  groups)        api get_group_list | python3 -c 'import json,sys; [print(g["group_id"], g.get("group_name")) for g in (json.load(sys.stdin).get("data") or [])]' ;;
  groups-leave-all)
    for gid in $(api get_group_list | python3 -c 'import json,sys; print(" ".join(str(g["group_id"]) for g in (json.load(sys.stdin).get("data") or [])))'); do
      r=$(api set_group_leave "{\"group_id\":$gid}" | rc); echo "  $gid -> retcode=$r"; sleep 2
    done
    echo "remaining: $(api get_group_list | python3 -c 'import json,sys; print(len(json.load(sys.stdin).get("data") or []))')"
    ;;
  send)          api send_private_msg "{\"user_id\":$2,\"message\":\"$3\"}" ;;
  logs)          echo "== bridge =="; tail -n "${2:-20}" "$BRIDGE_LOG"; echo "== napcat =="; docker logs --tail "${2:-20}" "$NAPCAT_CONTAINER" 2>&1 | grep -vaiE 'heroui|rgba' ;;
  archive)       tail -n "${2:-10}" "$ARCHIVE" 2>/dev/null || echo "(no archive yet)" ;;
  *)
    cat <<EOF
qqbridge <cmd>
  up | down                 start/stop both NapCat + bridge
  napcat-up|-down|-restart  NapCat container (restart = quick-login, no re-scan)
  bridge-up|-down|-restart   native bridge process
  status                    healthz + login + group count + archive size
  qr                        copy NapCat login QR out (for first scan)
  groups | groups-leave-all list / leave all groups
  send <qq> <text>          send a private message via the OneBot API
  logs [n] | archive [n]     tail bridge+napcat logs / the chat archive
EOF
    ;;
esac
