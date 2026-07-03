#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# Fetch the ONNX embedding model used by brain-library's vector/hybrid search.
#
# Default model: bge-small (Xenova/bge-small-zh-v1.5, ~24MB quantized). Files land in
# a per-model directory; point BRAIN_LIBRARY_EMBED_MODEL_DIR at it (or its parent —
# the loader searches recursively) so `index build` embeds and `lib.search` runs hybrid.
#
# Usage:
#   scripts/fetch-embed-model.sh [bge-small|qwen3-0.6b] [target-dir]
#
# Env:
#   HF_ENDPOINT  Hugging Face base URL (default https://huggingface.co; set to a
#                mirror such as https://hf-mirror.com from networks that need it).
set -euo pipefail

MODEL="${1:-bge-small}"
HF_ENDPOINT="${HF_ENDPOINT:-https://huggingface.co}"
STATE_DEFAULT="${XDG_STATE_HOME:-$HOME/.local/state}/rtime-assistant/brain-library/models"

case "$MODEL" in
  bge-small)
    REPO="Xenova/bge-small-zh-v1.5"
    DEST="${2:-$STATE_DEFAULT/bge-small-zh-v1.5}"
    FILES=("onnx/model_quantized.onnx" "tokenizer.json")
    ;;
  qwen3-0.6b)
    # Opt-in, slower/heavier (~600MB int8 vs bge's ~24MB); pull only when switching.
    # The official Qwen repo ships safetensors only; the ONNX export lives under
    # onnx-community. model_quantized.onnx is self-contained (no external .onnx_data).
    REPO="onnx-community/Qwen3-Embedding-0.6B-ONNX"
    DEST="${2:-$STATE_DEFAULT/Qwen3-Embedding-0.6B-ONNX}"
    FILES=("onnx/model_quantized.onnx" "tokenizer.json")
    ;;
  *)
    echo "unknown model: $MODEL (expected bge-small or qwen3-0.6b)" >&2
    exit 2
    ;;
esac

echo "Fetching $MODEL from $HF_ENDPOINT/$REPO -> $DEST"
for rel in "${FILES[@]}"; do
  out="$DEST/$rel"
  mkdir -p "$(dirname "$out")"
  url="$HF_ENDPOINT/$REPO/resolve/main/$rel"
  echo "  $rel"
  curl -fL --retry 3 -o "$out" "$url"
done

echo
echo "Done -> $DEST"
if [ -n "${2:-}" ]; then
  # Custom dir: must be pointed at via env (it's outside the default models dir).
  echo "Enable with: export BRAIN_LIBRARY_EMBED_MODEL_DIR=$DEST"
else
  # Default models dir: auto-discovered by the embedder, no env var needed.
  echo "Fetched to the default models dir — auto-discovered, no env var needed."
fi
if [ "$MODEL" != "bge-small" ]; then
  echo "Select this model with: export BRAIN_LIBRARY_EMBED_MODEL=$MODEL"
fi
echo "Then rebuild the index so it carries vectors (schema 4):"
echo "  scripts/rebuild-brain-index.sh   # auto-embeds when a model is present"
