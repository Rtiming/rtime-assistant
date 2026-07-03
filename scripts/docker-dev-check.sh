#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

COMPOSE_FILE="compose.dev.yml"
TIMEOUT_SECONDS="${DOCKER_TEST_TIMEOUT:-120}"
PYTHON_BIN="${PYTHON:-python3}"
SERVICES=""
SKIP_BUILD=0
BUILD_ONLY=0
DRY_RUN=0
CLEANUP_ONLY=0
USE_HOST_PROXY=0

DEFAULT_SERVICES="feishu-bridge-tests docpack-tests docpack-office-tests"

export DOCKER_BUILDKIT="${DOCKER_BUILDKIT:-1}"
export COMPOSE_DOCKER_CLI_BUILD="${COMPOSE_DOCKER_CLI_BUILD:-1}"

usage() {
  cat <<'EOF'
Usage: scripts/docker-dev-check.sh [options]

Build and run rtime-assistant Docker test targets with timeout and cleanup.

Options:
  --service NAME       service to check; repeatable; default all test services
  --timeout SECONDS    per-service docker run timeout; default $DOCKER_TEST_TIMEOUT or 120
  --skip-build         do not run docker compose build before docker run
  --build-only         build selected services, then stop before docker run
  --use-host-proxy     forward host proxy env to Docker builds without printing values
  --dry-run            print commands without executing them
  --cleanup-only       remove residual rtime-assistant-dev test containers and exit
  -h, --help           show this help
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

services() {
  if [ -n "$SERVICES" ]; then
    printf '%s\n' "$SERVICES"
  else
    printf '%s\n' "$DEFAULT_SERVICES"
  fi
}

image_for_service() {
  case "$1" in
    feishu-bridge-tests) printf '%s\n' "rtime-assistant-dev-feishu-bridge-tests:latest" ;;
    docpack-tests) printf '%s\n' "rtime-assistant-dev-docpack-tests:latest" ;;
    docpack-office-tests) printf '%s\n' "rtime-assistant-dev-docpack-office-tests:latest" ;;
    *) die "unknown service: $1" ;;
  esac
}

validate_service() {
  image_for_service "$1" >/dev/null
}

cleanup_containers() {
  local ids
  ids="$(
    docker ps -aq --filter 'name=rtime-assistant-dev' 2>/dev/null || true
    docker ps -aq --filter 'label=com.docker.compose.project=rtime-assistant-dev' 2>/dev/null || true
    for service in $(services); do
      docker ps -aq --filter "ancestor=$(image_for_service "$service")" 2>/dev/null || true
    done
  )"
  ids="$(printf '%s\n' "$ids" | awk 'NF && !seen[$0]++')"
  if [ -z "$ids" ]; then
    return
  fi
  docker rm -f $ids >/dev/null 2>&1 || true
}

print_command() {
  printf 'dry-run:'
  printf ' %q' "$@"
  printf '\n'
}

run_or_print() {
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command "$@"
  else
    "$@"
  fi
}

run_with_timeout() {
  local service="$1"
  shift
  "$PYTHON_BIN" - "$TIMEOUT_SECONDS" "$service" "$@" <<'PY'
import subprocess
import sys

timeout = int(sys.argv[1])
service = sys.argv[2]
command = sys.argv[3:]
container = f"rtime-assistant-dev-{service}-check"

print(f"run: {' '.join(command)}", flush=True)
try:
    completed = subprocess.run(command, text=True, timeout=timeout)
except subprocess.TimeoutExpired:
    print(f"error: docker run timed out after {timeout}s for {service}", file=sys.stderr)
    subprocess.run(["docker", "rm", "-f", container], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    raise SystemExit(124)
raise SystemExit(completed.returncode)
PY
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

while [ "$#" -gt 0 ]; do
  case "$1" in
    --service)
      shift
      SERVICES="${SERVICES:+$SERVICES }${1:-}"
      ;;
    --timeout)
      shift
      TIMEOUT_SECONDS="${1:-}"
      ;;
    --skip-build)
      SKIP_BUILD=1
      ;;
    --build-only)
      BUILD_ONLY=1
      ;;
    --use-host-proxy)
      USE_HOST_PROXY=1
      ;;
    --dry-run)
      DRY_RUN=1
      ;;
    --cleanup-only)
      CLEANUP_ONLY=1
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

[ -n "$TIMEOUT_SECONDS" ] || die "--timeout must not be empty"
case "$TIMEOUT_SECONDS" in
  ''|*[!0-9]*) die "--timeout must be an integer" ;;
esac

for service in $(services); do
  validate_service "$service"
done

if [ "$CLEANUP_ONLY" -eq 1 ]; then
  if [ "$DRY_RUN" -eq 1 ]; then
    log "dry-run: cleanup residual rtime-assistant-dev containers"
  else
    cleanup_containers
  fi
  exit 0
fi

log "docker dev check"
log "- compose: $COMPOSE_FILE"
log "- services: $(services)"
log "- timeout_seconds: $TIMEOUT_SECONDS"
log "- buildkit: $DOCKER_BUILDKIT"
log "- host_proxy_build_args: $USE_HOST_PROXY"

for service in $(services); do
  image="$(image_for_service "$service")"
  log ""
  log "== $service =="
  if [ "$SKIP_BUILD" -eq 0 ]; then
    build_args=()
    while IFS= read -r arg; do
      build_args+=("$arg")
    done < <(docker_build_args)
    run_or_print docker compose -f "$COMPOSE_FILE" build "${build_args[@]}" "$service"
  fi
  if [ "$BUILD_ONLY" -eq 1 ]; then
    continue
  fi

  container="rtime-assistant-dev-${service}-check"
  if [ "$DRY_RUN" -eq 1 ]; then
    print_command docker run --rm --name "$container" "$image"
    continue
  fi

  cleanup_containers
  run_with_timeout "$service" docker run --rm --name "$container" "$image"
done
