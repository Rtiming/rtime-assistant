#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEFAULT_REPO_ROOT="$(cd "$SCRIPT_DIR/../../.." && pwd)"
REPO_ROOT="${RTIME_ASSISTANT_ROOT:-$DEFAULT_REPO_ROOT}"

PYTHONPATH="$REPO_ROOT/packages/brain-docpack/src" \
  python -m brain_docpack --repo-root "$REPO_ROOT" doctor
