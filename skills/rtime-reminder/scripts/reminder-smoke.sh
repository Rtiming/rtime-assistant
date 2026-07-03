#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

ROOT="${RTIME_ASSISTANT_ROOT:-}"
if [ -z "$ROOT" ]; then
  if [ -d "$HOME/Desktop/rtime-assistant" ]; then
    ROOT="$HOME/Desktop/rtime-assistant"
  elif [ -d "$HOME/rtime-assistant" ]; then
    ROOT="$HOME/rtime-assistant"
  else
    ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
  fi
fi

TOOL="${RTIME_REMINDER_REGISTER:-$ROOT/deploy/bin/rtime-reminder-register}"
tmp="$(mktemp)"
trap 'rm -f "$tmp"' EXIT

export RTIME_REMINDER_DEFAULT_TARGET="${RTIME_REMINDER_DEFAULT_TARGET:-ou_test_user}"

"$TOOL" --path "$tmp" add \
  --mode notify \
  --due "2099-01-01T09:30:00+08:00" \
  --message "smoke reminder" \
  --id "smoke-reminder" >/dev/null

"$TOOL" --path "$tmp" add \
  --mode wake \
  --due "2099-01-01T09:35:00+08:00" \
  --message "wake smoke" \
  --prompt "reply wake smoke ok" \
  --id "wake-smoke-reminder" >/dev/null

"$TOOL" --path "$tmp" list --status pending >/dev/null
"$TOOL" --path "$tmp" cancel --id "smoke-reminder" >/dev/null

printf '{"prompt":"reply wake smoke ok"}' |
  RTIME_REMINDER_WAKE_DIRECT=1 RTIME_REMINDER_WAKE_ECHO=1 \
  "${PYTHON:-python3}" "$ROOT/deploy/bin/rtime-reminder-wake-runner" --inside >/dev/null

python3 - "$tmp" <<'PY'
import json
import pathlib
import sys

records = [json.loads(line) for line in pathlib.Path(sys.argv[1]).read_text().splitlines()]
assert len(records) == 2
assert records[0]["id"] == "smoke-reminder"
assert records[0]["status"] == "cancelled"
assert records[1]["id"] == "wake-smoke-reminder"
assert records[1]["mode"] == "wake"
assert records[1]["status"] == "pending"
print("rtime-reminder smoke ok")
PY
