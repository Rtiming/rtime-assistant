#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# brain-visualmd 本地视觉模型一键部署(Mac / Linux 通用,开箱即用)。
#
# 在任意设备上:确保 ollama → 起带大 context 的持久服务 → 拉模型 → 验证 → 打印 env。
# 幂等:重复跑安全。用法:
#   bash deploy/bootstrap.sh                 # 默认 qwen3-vl:8b,ctx 16384,仅本机
#   VISUALMD_MODEL=qwen2.5vl:7b bash deploy/bootstrap.sh
#   VISUALMD_OLLAMA_HOST=0.0.0.0 bash deploy/bootstrap.sh   # 对外服务(给 orangepi 等瘦客户端连)
set -euo pipefail

MODEL="${VISUALMD_MODEL:-qwen3-vl:8b}"
CTX="${VISUALMD_OLLAMA_CTX:-16384}"
PORT="${VISUALMD_OLLAMA_PORT:-11434}"
# 127.0.0.1 = 仅本机;0.0.0.0 = 对外(让 orangepi 等设备连入,做瘦客户端架构)。
HOST="${VISUALMD_OLLAMA_HOST:-127.0.0.1}"
OS="$(uname)"
have() { command -v "$1" >/dev/null 2>&1; }
log() { printf '[bootstrap] %s\n' "$*"; }

# 1) ensure ollama ---------------------------------------------------------
if ! have ollama; then
  log "ollama not found; installing…"
  if have brew; then brew install ollama
  elif [ "$OS" = "Linux" ]; then curl -fsSL https://ollama.com/install.sh | sh
  else log "install ollama manually: https://ollama.com/download"; exit 1; fi
fi
log "ollama: $(ollama --version 2>/dev/null | head -1)"

# 2) persistent service with a big context window --------------------------
# A full-page slide is ~3700 vision tokens; the default 4096 ctx truncates.
start_nohup() {
  pkill -x ollama 2>/dev/null || true
  OLLAMA_HOST="$HOST:$PORT" OLLAMA_CONTEXT_LENGTH="$CTX" OLLAMA_FLASH_ATTENTION=1 \
    nohup ollama serve >"${TMPDIR:-/tmp}/visualmd-ollama.log" 2>&1 &
  log "started ollama serve via nohup (ctx=$CTX host=$HOST)"
}

if [ "$OS" = "Darwin" ]; then
  PLIST="$HOME/Library/LaunchAgents/com.rtime.visualmd-ollama.plist"
  mkdir -p "$HOME/Library/LaunchAgents"
  cat >"$PLIST" <<PL
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.rtime.visualmd-ollama</string>
  <key>ProgramArguments</key><array>
    <string>$(command -v ollama)</string><string>serve</string></array>
  <key>EnvironmentVariables</key><dict>
    <key>OLLAMA_HOST</key><string>$HOST:$PORT</string>
    <key>OLLAMA_CONTEXT_LENGTH</key><string>$CTX</string>
    <key>OLLAMA_FLASH_ATTENTION</key><string>1</string></dict>
  <key>RunAtLoad</key><true/><key>KeepAlive</key><true/>
</dict></plist>
PL
  launchctl unload "$PLIST" 2>/dev/null || true
  launchctl load "$PLIST" 2>/dev/null && log "loaded launchd service (ctx=$CTX)" || start_nohup
elif [ "$OS" = "Linux" ]; then
  # systemd --user if available, else nohup (works on orangepi too).
  if have systemctl && systemctl --user show-environment >/dev/null 2>&1; then
    UNIT="$HOME/.config/systemd/user/visualmd-ollama.service"
    mkdir -p "$(dirname "$UNIT")"
    cat >"$UNIT" <<UNITEOF
[Unit]
Description=brain-visualmd ollama (big context)
After=network.target
[Service]
Environment=OLLAMA_HOST=$HOST:$PORT
Environment=OLLAMA_CONTEXT_LENGTH=$CTX
Environment=OLLAMA_FLASH_ATTENTION=1
ExecStart=$(command -v ollama) serve
Restart=on-failure
Nice=10
[Install]
WantedBy=default.target
UNITEOF
    systemctl --user daemon-reload
    systemctl --user enable --now visualmd-ollama.service && log "systemd --user service up (ctx=$CTX)" || start_nohup
  else
    start_nohup
  fi
else
  start_nohup
fi

# 3) wait for the endpoint, pull the model ---------------------------------
for _ in $(seq 1 30); do
  curl -sf "http://localhost:$PORT/api/tags" >/dev/null 2>&1 && break; sleep 1
done
log "pulling $MODEL (first time may take a while)…"
ollama pull "$MODEL"

# 4) verify + print env ----------------------------------------------------
curl -sf "http://localhost:$PORT/api/tags" >/dev/null && log "ollama endpoint OK on :$PORT"
cat <<EOF

# ready — export these (or use deploy/run-scan.sh which sets them):
export VISUALMD_VISION_BASE_URL=http://localhost:$PORT/v1
export VISUALMD_VISION_MODEL=$MODEL
# optional speed: cap image long side (needs Pillow):
# export VISUALMD_VISION_MAX_IMAGE_PX=1280
EOF
