#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

cd "${RTIME_ASSISTANT_ROOT:-$HOME/rtime-assistant}"
git pull --ff-only
scripts/audit-env.sh
systemctl --user daemon-reload

echo "Deployment updated. Restart affected services explicitly."
echo "Reminder live helper and systemd units may still point outside this git tree; check docs/deployment.md before changing them."
