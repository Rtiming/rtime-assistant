#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# Deploy-verify for the host assistant-gateway: restart the service and BLOCK
# until the live /version revision == git HEAD, then a health smoke. Run on
# orangepi after `git pull`. Makes "did my change actually go live" automatic
# instead of a manual curl. Idempotent; safe to re-run. Exit 0 == verified.
set -uo pipefail

ROOT="${RTIME_ROOT:-$HOME/rtime-assistant}"
URL="${GATEWAY_URL:-http://127.0.0.1:8765}"
UNIT="assistant-gateway.service"

head_rev="$(git -C "$ROOT" rev-parse --short HEAD)"
echo "restarting $UNIT -> target rev $head_rev"
systemctl --user restart "$UNIT"

for _ in $(seq 1 20); do
  live="$(curl -sS -m 5 --noproxy '*' "$URL/version" 2>/dev/null | python3 -c 'import sys,json
try:
    print(json.load(sys.stdin).get("revision",""))
except Exception:
    print("")' 2>/dev/null)"
  if [ "$live" = "$head_rev" ]; then
    echo "OK: gateway now serving rev $head_rev"
    echo "healthz: $(curl -sS -m 8 --noproxy '*' "$URL/healthz" 2>/dev/null)"
    exit 0
  fi
  sleep 0.5
done

echo "FAIL: gateway did not report rev $head_rev within timeout (live='${live:-none}')"
echo "  -> check the unit ExecStart points at $ROOT: scripts/gateway-live-audit.sh"
exit 2
