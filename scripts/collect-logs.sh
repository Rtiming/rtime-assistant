#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

LINES="${1:-120}"

echo "== lark-bridge =="
journalctl --user -u lark-bridge -n "$LINES" --no-pager || true

echo
echo "== reminder.service =="
journalctl --user -u reminder.service -n "$LINES" --no-pager || true
