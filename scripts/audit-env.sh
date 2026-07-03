#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

usage() {
  cat <<'EOF'
Usage: scripts/audit-env.sh [--profile auto|mac|orangepi|generic] [--full-git]

Read-only environment audit for rtime-assistant development/runtime hosts.

Options:
  --profile VALUE  Select path/service expectations. Default: auto.
  --full-git       Print the complete git status path list.
  -h, --help       Show this help.
EOF
}

die() {
  echo "audit-env.sh: $*" >&2
  exit 2
}

profile="auto"
full_git=0

while [ "$#" -gt 0 ]; do
  case "$1" in
    --profile)
      [ "$#" -ge 2 ] || die "--profile requires a value"
      profile="$2"
      shift 2
      ;;
    --profile=*)
      profile="${1#--profile=}"
      shift
      ;;
    --full-git)
      full_git=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown argument: $1"
      ;;
  esac
done

case "$profile" in
  auto|mac|orangepi|generic)
    ;;
  *)
    die "unsupported profile: $profile"
    ;;
esac

script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo_root="$(cd "$script_dir/.." && pwd)"
host_os="$(uname -s 2>/dev/null || echo unknown)"
selected_profile="$profile"

if [ "$selected_profile" = "auto" ]; then
  if [ -d "/mnt/brain" ] || [ -n "${RTIME_RUNTIME_HOST:-}" ]; then
    selected_profile="orangepi"
  elif [ "$host_os" = "Darwin" ] || [ -d "$HOME/OrangePi-Store/sync/brain" ]; then
    selected_profile="mac"
  else
    selected_profile="generic"
  fi
fi

path_line() {
  state="$1"
  label="$2"
  path="$3"
  printf "%-14s %-24s %s\n" "$state" "$label" "$path"
}

check_required() {
  label="$1"
  path="$2"
  if [ -e "$path" ]; then
    path_line "ok" "$label" "$path"
  else
    path_line "miss" "$label" "$path"
  fi
}

check_optional() {
  label="$1"
  path="$2"
  if [ -e "$path" ]; then
    path_line "ok" "$label" "$path"
  else
    path_line "optional" "$label" "$path"
  fi
}

skip_path() {
  label="$1"
  path="$2"
  path_line "skip" "$label" "$path"
}

print_common_paths() {
  check_required "repo root" "$repo_root"
  check_required "module manifest" "$repo_root/module-submit.json"
  check_required "module checker" "$repo_root/scripts/module-submit-check.py"
  check_optional "maintenance entry" "$repo_root/scripts/maintenance.sh"
}

print_mac_paths() {
  check_required "mac brain root" "$HOME/OrangePi-Store/sync/brain"
  check_optional "mac vault view" "$HOME/Desktop/brain-notes"
  check_optional "feishu config" "$HOME/.config/rtime-assistant/feishu.json"
  check_optional "kimi key" "$HOME/.config/claude-kimi/key"
  check_optional "lark state" "$HOME/.lark-channel"
  check_optional "claude-kimi bin" "$HOME/.local/bin/claude-kimi"
  check_optional "kb bin" "$HOME/.local/bin/kb"
  check_optional "reminder sender" "$HOME/.local/bin/reminder-sender.js"
  check_optional "lark bridge bin" "$HOME/.npm-global/bin/lark-channel-bridge"
  skip_path "runtime repo" "${RTIME_ASSISTANT_ROOT:-$HOME/rtime-assistant}"
  skip_path "runtime brain" "${BRAIN_ROOT:-/mnt/brain}"
  skip_path "runtime reminders" "${RTIME_REMINDERS_PATH:-/mnt/brain/_system/reminders.jsonl}"
}

print_orangepi_paths() {
  check_required "runtime repo" "${RTIME_ASSISTANT_ROOT:-$HOME/rtime-assistant}"
  check_required "brain root" "${BRAIN_ROOT:-/mnt/brain}"
  check_optional "reminders store" "${RTIME_REMINDERS_PATH:-/mnt/brain/_system/reminders.jsonl}"
  check_optional "feishu config" "$HOME/.config/rtime-assistant/feishu.json"
  check_optional "kimi key" "$HOME/.config/claude-kimi/key"
  check_optional "lark state" "$HOME/.lark-channel"
  check_optional "claude-kimi bin" "$HOME/.local/bin/claude-kimi"
  check_optional "kb bin" "$HOME/.local/bin/kb"
  check_optional "reminder sender" "$HOME/.local/bin/reminder-sender.js"
  check_optional "lark bridge bin" "$HOME/.npm-global/bin/lark-channel-bridge"
  skip_path "client brain root" "$HOME/OrangePi-Store/sync/brain"
  skip_path "client vault view" "$HOME/Desktop/brain-notes"
}

print_generic_paths() {
  check_optional "client brain root" "$HOME/OrangePi-Store/sync/brain"
  check_optional "runtime brain" "${BRAIN_ROOT:-/mnt/brain}"
  check_optional "feishu config" "$HOME/.config/rtime-assistant/feishu.json"
  check_optional "claude-kimi bin" "$HOME/.local/bin/claude-kimi"
  check_optional "kb bin" "$HOME/.local/bin/kb"
}

print_paths() {
  echo "-- paths ($selected_profile) --"
  print_common_paths
  case "$selected_profile" in
    mac)
      print_mac_paths
      ;;
    orangepi)
      print_orangepi_paths
      ;;
    generic)
      print_generic_paths
      ;;
  esac
}

print_git() {
  echo "-- git --"
  if ! git -C "$repo_root" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
    echo "not in a git worktree"
    return
  fi

  if [ "$full_git" -eq 1 ]; then
    git -C "$repo_root" status --short --branch
    return
  fi

  branch_line="$(git -C "$repo_root" status --short --branch | sed -n '1p')"
  porcelain="$(git -C "$repo_root" status --porcelain=v1 --untracked-files=all)"

  echo "$branch_line"
  if [ -z "$porcelain" ]; then
    echo "clean"
    return
  fi

  tracked_count="$(printf "%s\n" "$porcelain" | awk 'NF && substr($0,1,2)!="??" {count++} END {print count + 0}')"
  untracked_count="$(printf "%s\n" "$porcelain" | awk 'NF && substr($0,1,2)=="??" {count++} END {print count + 0}')"
  staged_count="$(printf "%s\n" "$porcelain" | awk 'NF && substr($0,1,1)!=" " && substr($0,1,1)!="?" {count++} END {print count + 0}')"
  unstaged_count="$(printf "%s\n" "$porcelain" | awk 'NF && substr($0,2,1)!=" " && substr($0,2,1)!="?" {count++} END {print count + 0}')"

  echo "summary: tracked=$tracked_count untracked=$untracked_count staged=$staged_count unstaged=$unstaged_count"
  echo "path list hidden; rerun with --full-git to inspect every dirty path"
}

print_services() {
  echo "-- services ($selected_profile) --"
  if [ "$selected_profile" = "mac" ]; then
    echo "systemctl skipped for mac profile"
    return
  fi

  if command -v systemctl >/dev/null 2>&1; then
    service_lines="$(systemctl --user --no-pager --type=service --type=timer 2>/dev/null | grep -Ei 'lark|feishu|reminder|assistant|brain|claude' || true)"
    if [ -n "$service_lines" ]; then
      printf "%s\n" "$service_lines"
    else
      echo "no matching user services or timers"
    fi
  else
    echo "systemctl unavailable"
  fi
}

echo "== rtime-assistant audit =="
date "+timestamp: %F %T %Z"
echo "repo: $repo_root"
echo "host_os: $host_os"
echo "profile: $selected_profile (requested: $profile)"
echo

print_paths
echo

print_git
echo

print_services
