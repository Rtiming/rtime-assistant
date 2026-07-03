#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# 后台/夜跑:对一个资料目录增量严格转写(跳过已完成),写到暂存目录。
# 与 deploy/bootstrap.sh 配套:先 bootstrap 起好模型服务,再用本脚本跑量。
#
#   bash deploy/run-scan.sh <materials-dir> [out-dir]
#   nohup bash deploy/run-scan.sh /path/to/course ~/visualmd-out >run.log 2>&1 &
#
# 幂等可续:scan 跳过 source_sha256 未变的整篇,中断可重跑;增量只算新资料。
set -euo pipefail

SRC="${1:?usage: run-scan.sh <materials-dir> [out-dir]}"
OUT="${2:-$PWD/visualmd-out}"
PORT="${VISUALMD_OLLAMA_PORT:-11434}"
export VISUALMD_VISION_BASE_URL="${VISUALMD_VISION_BASE_URL:-http://localhost:$PORT/v1}"
export VISUALMD_VISION_MODEL="${VISUALMD_VISION_MODEL:-qwen3-vl:8b}"

# locate the CLI: installed entrypoint, else this repo checkout.
PKG="$(cd "$(dirname "$0")/.." && pwd)"   # packages/brain-visualmd
if command -v brain-visualmd >/dev/null 2>&1; then
  RUN=(brain-visualmd)
else
  RUN=(env "PYTHONPATH=$PKG/src" python3 -m brain_visualmd)
fi

# be a good neighbour to a busy box (orangepi also runs the brain gateway).
PRE=()
command -v nice >/dev/null 2>&1 && PRE=(nice -n 15)
command -v ionice >/dev/null 2>&1 && PRE+=(ionice -c3)

echo "[run-scan] model=$VISUALMD_VISION_MODEL src=$SRC out=$OUT"
exec "${PRE[@]}" "${RUN[@]}" scan "$SRC" --out "$OUT" --backend vision
