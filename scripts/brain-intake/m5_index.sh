#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

BRAIN_ROOT="$HOME/OrangePi-Store/sync/brain"
RUN_DIR="work/pipeline/run-01"
QUERY_TERMS=("声子热容" "热力学" "同步辐射")
while [ "$#" -gt 0 ]; do
  case "$1" in
    --brain-root) BRAIN_ROOT="$2"; shift 2 ;;
    --run-dir) RUN_DIR="$2"; shift 2 ;;
    --vault-root) shift 2 ;;
    --query) QUERY_TERMS+=("$2"); shift 2 ;;
    *) shift ;;
  esac
done

mkdir -p "$RUN_DIR"
RUN_ID="$(basename "$RUN_DIR")"
INDEX="/tmp/${RUN_ID}-brain-library.sqlite"
# 冒烟测试：每次建到唯一的 /tmp 临时索引(从不预先存在)，故用 --force 做干净的从零全量构建，
# 正是要验证全量索引路径本身。此处 --incremental 无意义(无旧索引可复用)；日常生产重建走
# rebuild-brain-index.sh 的增量。
PYTHONPATH=packages/brain-library/src python -m brain_library index build "$BRAIN_ROOT" --out "$INDEX" --force > "$RUN_DIR/M5-index-build.json"
PYTHONPATH=packages/brain-library/src python -m brain_library index status "$INDEX" > "$RUN_DIR/M5-index-status.json"
for term in "${QUERY_TERMS[@]}"; do
  safe_term="$(printf '%s' "$term" | tr '/ ' '__')"
  PYTHONPATH=packages/brain-library/src python -m brain_library index query "$INDEX" "$term" --limit 5 > "$RUN_DIR/M5-query-${safe_term}.json"
done
cat > "$RUN_DIR/M5-log.json" <<JSON
{
  "ok": true,
  "run_id": "$RUN_ID",
  "local_index": "$INDEX",
  "query_terms": $(printf '%s\n' "${QUERY_TERMS[@]}" | python -c 'import json,sys; print(json.dumps([line.rstrip("\n") for line in sys.stdin], ensure_ascii=False))'),
  "remote_orangepi": "not_run_by_wrapper; run rtime-doctor and remote query separately"
}
JSON
cat > "$RUN_DIR/M5-报告.md" <<MD
# M5 索引报告

- run_id: $RUN_ID
- local_index: $INDEX

## 做了什么
- 本地派生索引已构建到 /tmp，不写入 brain。
- 完成本地冒烟查询：${QUERY_TERMS[*]}。

## 跳过什么
- orangepi 远端索引由主流程在 rtime-remote doctor 后单独执行并追加记录。

## 异常
- 无
MD
printf '%s\n' '{"ok":true}'
