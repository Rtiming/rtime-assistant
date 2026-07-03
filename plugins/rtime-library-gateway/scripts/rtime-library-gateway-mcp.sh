#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

PLUGIN_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPO_ROOT="${RTIME_ASSISTANT_ROOT:-}"

if [ -z "$REPO_ROOT" ]; then
  if [ -d "$PLUGIN_DIR/../../packages/rtime-library-gateway/src" ]; then
    REPO_ROOT="$(cd "$PLUGIN_DIR/../.." && pwd)"
  elif [ -d "$HOME/Desktop/rtime-assistant/packages/rtime-library-gateway/src" ]; then
    REPO_ROOT="$HOME/Desktop/rtime-assistant"
  elif [ -d "$HOME/rtime-assistant/packages/rtime-library-gateway/src" ]; then
    REPO_ROOT="$HOME/rtime-assistant"
  else
    printf 'error: set RTIME_ASSISTANT_ROOT to the rtime-assistant repository\n' >&2
    exit 2
  fi
fi

export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/packages/rtime-library-gateway/src${PYTHONPATH:+:$PYTHONPATH}"

if [ -z "${BRAIN_ROOT:-}" ]; then
  if [ -d "$HOME/OrangePi-Store/sync/brain" ]; then
    export BRAIN_ROOT="$HOME/OrangePi-Store/sync/brain"
  elif [ -d "/mnt/brain" ]; then
    export BRAIN_ROOT="/mnt/brain"
  fi
fi

if [ -z "${RTIME_HUB_ROOT:-}" ]; then
  if [ -d "$HOME/rtime-hub" ]; then
    export RTIME_HUB_ROOT="$HOME/rtime-hub"
  elif [ -d "/srv/rtime-hub" ]; then
    export RTIME_HUB_ROOT="/srv/rtime-hub"
  fi
fi

if [ -z "${RTIME_REMINDERS_PATH:-}" ]; then
  if [ -f "$HOME/OrangePi-Store/sync/brain/_system/reminders.jsonl" ]; then
    export RTIME_REMINDERS_PATH="$HOME/OrangePi-Store/sync/brain/_system/reminders.jsonl"
  elif [ -f "/mnt/brain/_system/reminders.jsonl" ]; then
    export RTIME_REMINDERS_PATH="/mnt/brain/_system/reminders.jsonl"
  fi
fi

# Keep the derived BM25 index off the orangepi root partition (a small SD card).
# On the server the large NVMe disk is mounted at /var/lib/rtime-assistant, so default the
# index there when it exists and is writable; rebuilds can be large and the root
# partition has filled up before. The path stays *outside* the brain root so the
# indexer's brain-root containment check does not reject it. Other clients (e.g.
# Mac) leave BRAIN_LIBRARY_INDEX unset and fall back to default_index()
# (~/.local/state/...). An explicit BRAIN_LIBRARY_INDEX always wins.
if [ -z "${BRAIN_LIBRARY_INDEX:-}" ] && [ -d /var/lib/rtime-assistant ] && [ -w /var/lib/rtime-assistant ]; then
  export BRAIN_LIBRARY_INDEX="/var/lib/rtime-assistant/brain-library/brain-library.sqlite"
fi

# Stdin idle guard (RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT): the server self-exits
# after this many idle seconds to bound leaked mcp_server processes from
# half-open ssh connections. The original 30min default also killed *live but
# idle* sessions — an open Claude Code that simply hadn't queried the brain for
# 30min — surfacing as a spurious "Server disconnected" toast (the MCP client
# does not ping inside that window). Truly-dead TCP is already reaped by sshd
# ClientAlive (300s x 2 ≈ 10min), so we only need this as a long-horizon
# backstop for live-but-idle orphans. Bump the default to 8h: no realistic work
# session has 8h of continuous brain silence with the laptop awake (walking away
# longer sleeps the laptop -> sshd reaps the dead tunnel instead). An explicit
# external value still wins.
export RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT="${RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT:-28800}"

exec "${PYTHON:-python3}" -m rtime_library_gateway.mcp_server
