#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${RTIME_ASSISTANT_ROOT:-}"

if [ -z "$REPO_ROOT" ]; then
  if [ -d "$PLUGIN_DIR/../../packages/rtime-automation/src" ]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
  elif [ -d "$HOME/Desktop/rtime-assistant/packages/rtime-automation/src" ]; then
    REPO_ROOT="$HOME/Desktop/rtime-assistant"
  elif [ -d "$HOME/rtime-assistant/packages/rtime-automation/src" ]; then
    REPO_ROOT="$HOME/rtime-assistant"
  else
    printf 'error: set RTIME_ASSISTANT_ROOT to the rtime-assistant repository\n' >&2
    exit 2
  fi
fi

export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/packages/rtime-automation/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -z "${RTIME_REMINDERS_PATH:-}" ]; then
  if [ -f "$HOME/OrangePi-Store/sync/brain/_system/reminders.jsonl" ]; then
    export RTIME_REMINDERS_PATH="$HOME/OrangePi-Store/sync/brain/_system/reminders.jsonl"
  elif [ -f "/mnt/brain/_system/reminders.jsonl" ]; then
    export RTIME_REMINDERS_PATH="/mnt/brain/_system/reminders.jsonl"
  fi
fi

exec "${PYTHON:-python3}" -m rtime_automation.mcp_server
