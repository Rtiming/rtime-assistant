#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

COMPOSE_FILE="compose.prod.yml"
ENV_FILE=""
SERVICE="feishu-bridge"
DO_CONFIG=0
DO_BUILD=0
DO_SMOKE=0
DO_UP=0
DO_PS=0
DO_LOGS=0
DO_DOWN=0
ACTION_SELECTED=0
DRY_RUN=0
USE_HOST_PROXY=0

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
export COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"

usage() {
  cat <<'EOF'
Usage: scripts/docker-prod-check.sh [options]

Validate or operate the production Docker Compose stack.

Options:
  --env-file PATH   production env file, for example /etc/rtime-assistant/docker.env
  --config          run docker compose config; default action
  --build           build the production service image
  --smoke           run a one-shot container smoke check for mounts, Feishu config, and Claude CLI
  --up              start the production stack with docker compose up -d
  --ps              show production stack status
  --logs            tail recent production service logs
  --down            stop the production stack
  --service NAME    service name; default feishu-bridge
  --use-host-proxy  forward host proxy env to Docker builds without printing values
  --dry-run         print commands without executing them
  -h, --help        show this help
EOF
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

compose_base() {
  local cmd=(docker compose)
  if [ -n "$ENV_FILE" ]; then
    cmd+=(--env-file "$ENV_FILE")
  fi
  cmd+=(-f "$COMPOSE_FILE")
  printf '%s\0' "${cmd[@]}"
}

print_command() {
  printf 'run:'
  printf ' %q' "$@"
  printf '\n'
}

run_or_print() {
  print_command "$@"
  if [ "$DRY_RUN" -eq 0 ]; then
    "$@"
  fi
}

run_compose() {
  local cmd=()
  while IFS= read -r -d '' part; do
    cmd+=("$part")
  done < <(compose_base)
  cmd+=("$@")
  run_or_print "${cmd[@]}"
}

docker_build_args() {
  local key
  for key in HTTP_PROXY HTTPS_PROXY FTP_PROXY NO_PROXY ALL_PROXY http_proxy https_proxy ftp_proxy no_proxy all_proxy; do
    if [ "$USE_HOST_PROXY" -eq 1 ]; then
      printf '%s\n' --build-arg "$key"
    else
      printf '%s\n' --build-arg "$key="
    fi
  done
}

run_smoke() {
  local cmd=()
  while IFS= read -r -d '' part; do
    cmd+=("$part")
  done < <(compose_base)
  cmd+=(run --rm --no-deps "$SERVICE" python -)
  print_command "${cmd[@]}"
  if [ "$DRY_RUN" -eq 1 ]; then
    return
  fi
  "${cmd[@]}" <<'PY'
from pathlib import Path
import json
import os
import shutil
import sys

checks = []


def add(name: str, ok: bool, detail: str) -> None:
    checks.append({"name": name, "ok": ok, "detail": detail})


brain = Path(os.environ.get("DEFAULT_CWD", "/mnt/brain"))
add("default_cwd_exists", brain.exists(), str(brain))

feishu_config = Path(os.environ.get("FEISHU_CONFIG_JSON", ""))
add("feishu_config_exists", feishu_config.is_file(), str(feishu_config))

state = Path(os.environ.get("HOME", "/var/lib/rtime-assistant"))
add("state_dir_writable", state.exists() and os.access(state, os.W_OK), str(state))

log_path = Path(os.environ.get("RTIME_ASSISTANT_RUN_LOG", ""))
log_parent = log_path.parent if str(log_path) else state
add("run_log_parent_writable", log_parent.exists() and os.access(log_parent, os.W_OK), str(log_parent))

claude_cli = os.environ.get("CLAUDE_CLI_PATH", "claude")
resolved = shutil.which(claude_cli) or (claude_cli if Path(claude_cli).exists() else "")
add("claude_cli_exists", bool(resolved), resolved or claude_cli)
fallback_cli = os.environ.get("RTIME_CLAUDE_FALLBACK", "")
if "claude-rtime" in claude_cli:
    fallback_resolved = shutil.which(fallback_cli) or (
        fallback_cli if fallback_cli and Path(fallback_cli).exists() else ""
    )
    add("rtime_fallback_cli_exists", bool(fallback_resolved), fallback_resolved or fallback_cli)
    deepseek_wrapper = os.environ.get("RTIME_DEEPSEEK_CLAUDE_WRAPPER", "/usr/local/bin/claude-deepseek")
    deepseek_resolved = shutil.which(deepseek_wrapper) or (
        deepseek_wrapper if Path(deepseek_wrapper).exists() else ""
    )
    add("deepseek_code_wrapper_exists", bool(deepseek_resolved), deepseek_resolved or deepseek_wrapper)
    qwen_wrapper = os.environ.get("RTIME_QWEN_CLAUDE_WRAPPER", "/usr/local/bin/claude-qwen")
    qwen_resolved = shutil.which(qwen_wrapper) or (
        qwen_wrapper if Path(qwen_wrapper).exists() else ""
    )
    add("qwen_code_wrapper_exists", bool(qwen_resolved), qwen_resolved or qwen_wrapper)

claude_state = state / ".claude"
claude_config = state / ".claude.json"
provider_token = bool(os.environ.get("ANTHROPIC_AUTH_TOKEN"))
kimi_keyfile_raw = os.environ.get("CLAUDE_KIMI_KEYFILE", "")
kimi_keyfile = Path(kimi_keyfile_raw) if kimi_keyfile_raw else None
uses_kimi_wrapper = "claude-kimi" in claude_cli
kimi_key_ok = bool(kimi_keyfile and kimi_keyfile.is_file() and kimi_keyfile.stat().st_size > 0)
add(
    "claude_kimi_key_exists_when_configured",
    (not uses_kimi_wrapper) or kimi_key_ok,
    str(kimi_keyfile) if kimi_keyfile else "not configured",
)
provider_ready = provider_token or kimi_key_ok
provider_detail = (
    "ANTHROPIC_AUTH_TOKEN set"
    if provider_token
    else ("CLAUDE_KIMI_KEYFILE set" if kimi_key_ok else "")
)
add(
    "claude_state_or_provider_token_exists",
    provider_ready or claude_state.exists(),
    provider_detail or str(claude_state),
)
add(
    "claude_config_or_provider_token_exists",
    provider_ready or claude_config.exists(),
    provider_detail or str(claude_config),
)
ustc_keyfile_raw = os.environ.get("RTIME_USTC_API_KEY_FILE", "")
ustc_keyfile = Path(ustc_keyfile_raw) if ustc_keyfile_raw else None
ustc_env_key = bool(os.environ.get("RTIME_USTC_API_KEY"))
ustc_file_ok = bool(
    ustc_keyfile
    and ustc_keyfile.is_file()
    and ustc_keyfile.stat().st_size > 0
    and str(ustc_keyfile) != "/dev/null"
)
add(
    "ustc_key_optional",
    True,
    "configured" if (ustc_env_key or ustc_file_ok) else "not configured",
)

deepseek_keyfile_raw = os.environ.get("RTIME_DEEPSEEK_API_KEY_FILE", "")
deepseek_keyfile = Path(deepseek_keyfile_raw) if deepseek_keyfile_raw else None
deepseek_env_key = bool(os.environ.get("RTIME_DEEPSEEK_API_KEY"))
deepseek_file_ok = bool(
    deepseek_keyfile
    and deepseek_keyfile.is_file()
    and deepseek_keyfile.stat().st_size > 0
    and str(deepseek_keyfile) != "/dev/null"
)
add(
    "deepseek_code_key_optional",
    True,
    "configured" if (deepseek_env_key or deepseek_file_ok) else "not configured",
)

qwen_keyfile_raw = os.environ.get("RTIME_QWEN_API_KEY_FILE", "")
qwen_keyfile = Path(qwen_keyfile_raw) if qwen_keyfile_raw else None
qwen_env_key = bool(os.environ.get("RTIME_QWEN_API_KEY") or os.environ.get("QWEN_API_KEY") or os.environ.get("DASHSCOPE_API_KEY"))
qwen_file_ok = bool(
    qwen_keyfile
    and qwen_keyfile.is_file()
    and qwen_keyfile.stat().st_size > 0
    and str(qwen_keyfile) != "/dev/null"
)
add(
    "qwen_code_key_optional",
    True,
    "configured" if (qwen_env_key or qwen_file_ok) else "not configured",
)

proxy_keys = ("HTTP_PROXY", "HTTPS_PROXY", "http_proxy", "https_proxy", "ALL_PROXY", "all_proxy")
proxy_values = {key: os.environ.get(key, "") for key in proxy_keys if os.environ.get(key, "")}
add("proxy_env_optional", True, ",".join(sorted(proxy_values)) or "empty")

rtime_web_fetch = shutil.which("rtime-web-fetch")
add("rtime_web_fetch_exists", bool(rtime_web_fetch), rtime_web_fetch or "not found")

ok = all(item["ok"] for item in checks)
print(json.dumps({"ok": ok, "checks": checks}, ensure_ascii=False, indent=2))
sys.exit(0 if ok else 1)
PY
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --env-file)
      shift
      ENV_FILE="${1:-}"
      ;;
    --config)
      DO_CONFIG=1
      ACTION_SELECTED=1
      ;;
    --build)
      DO_BUILD=1
      ACTION_SELECTED=1
      ;;
    --smoke)
      DO_SMOKE=1
      ACTION_SELECTED=1
      ;;
    --up)
      DO_UP=1
      ACTION_SELECTED=1
      ;;
    --ps)
      DO_PS=1
      ACTION_SELECTED=1
      ;;
    --logs)
      DO_LOGS=1
      ACTION_SELECTED=1
      ;;
    --down)
      DO_DOWN=1
      ACTION_SELECTED=1
      ;;
    --service)
      shift
      SERVICE="${1:-}"
      ;;
    --use-host-proxy)
      USE_HOST_PROXY=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      die "unknown option: $1"
      ;;
  esac
  shift
done

[ -n "$SERVICE" ] || die "--service must not be empty"
if [ -n "$ENV_FILE" ] && [ ! -f "$ENV_FILE" ]; then
  die "env file not found: $ENV_FILE"
fi

if [ "$ACTION_SELECTED" -eq 0 ]; then
  DO_CONFIG=1
fi

if [ "$DO_CONFIG" -eq 1 ]; then
  run_compose config
fi
if [ "$DO_BUILD" -eq 1 ]; then
  build_args=()
  while IFS= read -r arg; do
    build_args+=("$arg")
  done < <(docker_build_args)
  run_compose build "${build_args[@]}" "$SERVICE"
fi
if [ "$DO_SMOKE" -eq 1 ]; then
  run_smoke
fi
if [ "$DO_UP" -eq 1 ]; then
  run_compose up -d --remove-orphans "$SERVICE"
fi
if [ "$DO_PS" -eq 1 ]; then
  run_compose ps
fi
if [ "$DO_LOGS" -eq 1 ]; then
  run_compose logs --tail 120 "$SERVICE"
fi
if [ "$DO_DOWN" -eq 1 ]; then
  run_compose down
fi
