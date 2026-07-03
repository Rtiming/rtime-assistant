#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PLUGIN_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

find_repo_root() {
  local candidate
  for candidate in \
    "${RTIME_ASSISTANT_ROOT:-}" \
    "$PLUGIN_ROOT/../.." \
    "$PWD" \
    "$PWD/.." \
    "$PWD/../.."; do
    if [ -n "$candidate" ] \
      && [ -f "$candidate/packages/rtime-assistant-runtime/src/rtime_assistant_runtime/mcp_server.py" ] \
      && [ -f "$candidate/apps/feishu-bridge/main.py" ]; then
      cd "$candidate"
      pwd
      return 0
    fi
  done
  return 1
}

REPO_ROOT="$(find_repo_root)"
export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/packages/rtime-assistant-runtime/src${PYTHONPATH:+:$PYTHONPATH}"

exec "${PYTHON:-python3}" -m rtime_assistant_runtime.mcp_server "$@"
