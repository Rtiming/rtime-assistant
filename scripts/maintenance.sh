#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/maintenance.sh <command> [args]

Repository maintenance entrypoint. This script delegates to the smaller
module-aware tools instead of replacing their contracts.

Commands:
  env [audit args]   Run scripts/audit-env.sh.
  changed           Run git whitespace check and changed-module dry-run.
  governance        Run governance script syntax and module checks.
  quick [audit args] Run env, git whitespace, and changed-module dry-run.
  help              Show this help.
EOF
}

die() {
  echo "maintenance.sh: $*" >&2
  exit 2
}

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
command="${1:-help}"

run_changed() {
  git -C "$repo_root" diff --check
  "$script_dir/module-submit-check.py" --changed --dry-run
}

run_governance() {
  bash -n "$script_dir/audit-env.sh"
  bash -n "$script_dir/maintenance.sh"
  (cd "$repo_root" && python -m pytest tests/test_module_submit_check.py -q)
  "$script_dir/module-submit-check.py" --list
  "$script_dir/module-submit-check.py" --changed --dry-run
}

case "$command" in
  env)
    shift
    "$script_dir/audit-env.sh" "$@"
    ;;
  changed)
    shift
    [ "$#" -eq 0 ] || die "changed does not accept extra arguments"
    run_changed
    ;;
  governance)
    shift
    [ "$#" -eq 0 ] || die "governance does not accept extra arguments"
    run_governance
    ;;
  quick)
    shift
    "$script_dir/audit-env.sh" "$@"
    run_changed
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    die "unknown command: $command"
    ;;
esac
