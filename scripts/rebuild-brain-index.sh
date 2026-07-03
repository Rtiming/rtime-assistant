#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# 重建 brain 的 BM25 全文索引(派生缓存,输出在 brain 外、不入 git、Obsidian 看不到)。
# 详见 docs/brain-search-quickstart.md
set -euo pipefail

# 定位 brain 根:优先 BRAIN_ROOT,其次各机常见挂载点
BRAIN="${BRAIN_ROOT:-}"
if [ -z "$BRAIN" ]; then
  for c in "$HOME/OrangePi-Store/sync/brain" "$HOME/brain" "/mnt/brain"; do
    [ -d "$c" ] && BRAIN="$c" && break
  done
fi
if [ -z "$BRAIN" ] || [ ! -d "$BRAIN" ]; then
  echo "找不到 brain 根,设置 BRAIN_ROOT 后重试" >&2
  exit 1
fi

# 索引输出路径:显式 BRAIN_LIBRARY_INDEX 优先;否则优先 runtime state 目录,
# 其它机回退到 ~/.local/state。
# 路径在 brain 根之外,不触发 indexer 的 brain-root 包含校验。
if [ -n "${BRAIN_LIBRARY_INDEX:-}" ]; then
  OUT="$BRAIN_LIBRARY_INDEX"
elif [ -d /var/lib/rtime-assistant ] && [ -w /var/lib/rtime-assistant ]; then
  OUT="/var/lib/rtime-assistant/brain-library/brain-library.sqlite"
else
  OUT="$HOME/.local/state/rtime-assistant/brain-library/brain-library.sqlite"
fi
mkdir -p "$(dirname "$OUT")"
export PYTHONUTF8=1 PYTHONIOENCODING=utf-8

# 运行 brain-library：优先 PATH 上的控制台脚本，否则回退到仓库内模块形式
# （SSH 非交互 shell / 未装控制台脚本时也能跑，避免 "brain-library: 未找到命令"）。
run_brain_library() {
  if command -v brain-library >/dev/null 2>&1; then
    brain-library "$@"
  else
    _sd="$(cd "$(dirname "$0")" && pwd)"
    PYTHONPATH="$_sd/../packages/brain-library/src${PYTHONPATH:+:$PYTHONPATH}" "${PYTHON:-python3}" -m brain_library.cli "$@"
  fi
}

echo "重建索引: $BRAIN  ->  $OUT"
# 默认 auto-embed：配置了嵌入模型(BRAIN_LIBRARY_EMBED_MODEL_DIR + [vector] 依赖)时
# 自动加向量层走 schema 4 混合检索，没有则纯 BM25(schema 3)。强制纯 BM25 用 --no-embed。
# 默认增量(复用未变文档旧向量，只重嵌入新增/改动，大幅提速)；
# 换嵌入模型/索引损坏需全量重建时设 BRAIN_INDEX_FULL=1。
BUILD_FLAGS="--incremental"
[ -n "${BRAIN_INDEX_FULL:-}" ] && BUILD_FLAGS="--force"
run_brain_library index build "$BRAIN" --out "$OUT" $BUILD_FLAGS
echo "(增量重建；全量重建用 BRAIN_INDEX_FULL=1)"
echo "完成。查询: $(dirname "$0")/brain-search \"<查询词>\""
