#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# Read-only: report whether the live assistant-gateway is actually running the
# current git HEAD. Catches the stale-staged-copy / forgot-to-restart deploy
# drift class. Exit 0 == in sync, 2 == drift, 1 == can't tell (pre-version build).
# Safe to run anytime on orangepi; add to runbook Daily Checks.
set -uo pipefail

ROOT="${RTIME_ROOT:-$HOME/rtime-assistant}"
URL="${GATEWAY_URL:-http://127.0.0.1:8765}"
UNIT="assistant-gateway.service"

head_rev="$(git -C "$ROOT" rev-parse --short HEAD 2>/dev/null || echo unknown)"
active="$(systemctl --user is-active "$UNIT" 2>/dev/null || echo inactive)"
execstart="$(systemctl --user show -p ExecStart --value "$UNIT" 2>/dev/null | head -1)"
pid="$(systemctl --user show -p MainPID --value "$UNIT" 2>/dev/null)"
cmdline=""
if [ -n "${pid:-}" ] && [ "$pid" != "0" ] && [ -r "/proc/$pid/cmdline" ]; then
  cmdline="$(tr '\0' ' ' < "/proc/$pid/cmdline" 2>/dev/null)"
fi
ver_json="$(curl -sS -m 8 --noproxy '*' "$URL/version" 2>/dev/null || true)"
live_rev="$(printf '%s' "$ver_json" | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("revision",""))
except Exception:
    print("")' 2>/dev/null)"

echo "unit          : $UNIT ($active)"
echo "ExecStart     : $execstart"
echo "live cmdline  : ${cmdline:-<unknown>}"
echo "git HEAD      : $head_rev"
echo "live /version : ${live_rev:-<no /version>}"

if [ "$active" != "active" ]; then
  echo "DRIFT: service not active"; exit 2
fi
case "$execstart" in
  *"/.local/state/"*) echo "DRIFT: ExecStart points at a staged copy, not the git tree"; exit 2;;
esac
if [ -z "$live_rev" ]; then
  echo "WARN: gateway has no /version yet (pre-version-stamp build) — restart after deploy"; exit 1
fi
if [ "$live_rev" != "$head_rev" ]; then
  echo "DRIFT: live revision ($live_rev) != git HEAD ($head_rev) — restart the service"; exit 2
fi
echo "OK: live gateway == git HEAD ($head_rev)"
