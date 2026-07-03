#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# Create a reproducible test venv (.venv-test) with the repo's dev/test deps so
# the designated verify host (orangepi) and CI can run `pytest tests/` without
# ad-hoc steps. Idempotent. See docs/development-workflow.md §4.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VENV="${RTIME_TEST_VENV:-${ROOT}/.venv-test}"
PY="${PYTHON:-python3}"

if [ ! -x "${VENV}/bin/python" ]; then
  "${PY}" -m venv "${VENV}"
fi
"${VENV}/bin/python" -m pip install --quiet --upgrade pip
"${VENV}/bin/python" -m pip install --quiet -r "${ROOT}/requirements-dev.txt"

echo "test venv ready: ${VENV}"
echo "run: ${VENV}/bin/python -m pytest tests -q"
