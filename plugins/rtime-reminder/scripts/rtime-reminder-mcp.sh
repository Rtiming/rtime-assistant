#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${RTIME_ASSISTANT_ROOT:-}"

if [ -z "$REPO_ROOT" ]; then
  if [ -d "$PLUGIN_DIR/../../deploy/bin" ]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
  elif [ -d "$HOME/Desktop/rtime-assistant/deploy/bin" ]; then
    REPO_ROOT="$HOME/Desktop/rtime-assistant"
  elif [ -d "$HOME/rtime-assistant/deploy/bin" ]; then
    REPO_ROOT="$HOME/rtime-assistant"
  else
    printf 'error: set RTIME_ASSISTANT_ROOT to the rtime-assistant repository\n' >&2
    exit 2
  fi
fi

export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export RTIME_REMINDER_REGISTER="${RTIME_REMINDER_REGISTER:-$REPO_ROOT/deploy/bin/rtime-reminder-register}"

exec "${PYTHON:-python3}" "$REPO_ROOT/deploy/bin/rtime-reminder-mcp"
