#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

ACTION=${1:-plan}

BRAIN_ROOT=${BRAIN_ROOT:-/mnt/brain}
BRAIN_KNOWLEDGE_DIR=${BRAIN_KNOWLEDGE_DIR:-$BRAIN_ROOT/knowledge}
BRAIN_INBOX_DIR=${BRAIN_INBOX_DIR:-$BRAIN_ROOT/_inbox/webdav-upload}
ZOTERO_WEBDAV_DIR=${ZOTERO_WEBDAV_DIR:-/var/lib/rtime-assistant/zotero-webdav}
CONFIG_DIR=${CONFIG_DIR:-$HOME/.config/rtime-assistant/brain-webdav}
CREDENTIAL_ENV=${CREDENTIAL_ENV:-$CONFIG_DIR/credentials.env}
HTPASSWD_FILE=${HTPASSWD_FILE:-$CONFIG_DIR/htpasswd}
RCLONE_IMAGE=${RCLONE_IMAGE:-rclone/rclone:latest}
BIND_ADDR=${BRAIN_WEBDAV_BIND_ADDR:-127.0.0.1}
BRAIN_PORT=${BRAIN_WEBDAV_BRAIN_PORT:-18081}
INBOX_PORT=${BRAIN_WEBDAV_INBOX_PORT:-18082}
ZOTERO_PORT=${BRAIN_WEBDAV_ZOTERO_PORT:-18083}
WEBDAV_USER_DEFAULT=${WEBDAV_USER_DEFAULT:-rtime}
CURL_CONNECT_TIMEOUT=${CURL_CONNECT_TIMEOUT:-5}
CURL_MAX_TIME=${CURL_MAX_TIME:-20}

APPLY=0
case "$ACTION" in
  plan|apply|status|verify|stop|help|-h|--help) ;;
  *)
    printf 'Unknown action: %s\n\n' "$ACTION" >&2
    ACTION=help
    ;;
esac

if [ "$ACTION" = "apply" ] || [ "$ACTION" = "stop" ]; then
  APPLY=1
fi

usage() {
  cat <<EOF
Usage: scripts/brain-zotero-webdav.sh [plan|apply|status|verify|stop]

Repository-owned helper for the brain/Zotero single-canonical-PDF WebDAV stack.

Actions:
  plan     Show the effective configuration and the operations apply would run.
  apply    Create directories/credentials if needed and recreate Docker services.
  status   Show Docker container status and local path existence.
  verify   Run HTTP/WebDAV smoke tests with credentials from CREDENTIAL_ENV.
  stop     Stop and remove the three WebDAV containers.

Environment overrides:
  BRAIN_ROOT                 Default: /mnt/brain
  ZOTERO_WEBDAV_DIR          Default: /var/lib/rtime-assistant/zotero-webdav
  CONFIG_DIR                 Default: ~/.config/rtime-assistant/brain-webdav
  CREDENTIAL_ENV             Default: \$CONFIG_DIR/credentials.env
  BRAIN_WEBDAV_BIND_ADDR     Default: 127.0.0.1
  BRAIN_WEBDAV_BRAIN_PORT    Default: 18081
  BRAIN_WEBDAV_INBOX_PORT    Default: 18082
  BRAIN_WEBDAV_ZOTERO_PORT   Default: 18083

Secrets:
  apply creates CREDENTIAL_ENV and HTPASSWD_FILE with mode 600 if they do not
  exist. It never prints the generated password.
EOF
}

print_config() {
  cat <<EOF
Effective configuration:
  brain knowledge:     $BRAIN_KNOWLEDGE_DIR
  brain inbox:         $BRAIN_INBOX_DIR
  zotero webdav cache: $ZOTERO_WEBDAV_DIR
  config dir:          $CONFIG_DIR
  credential env:      $CREDENTIAL_ENV
  htpasswd file:       $HTPASSWD_FILE
  bind address:        $BIND_ADDR
  ports:               brain=$BRAIN_PORT inbox=$INBOX_PORT zotero=$ZOTERO_PORT
  rclone image:        $RCLONE_IMAGE
EOF
}

quote_cmd() {
  printf '%q ' "$@"
  printf '\n'
}

run_cmd() {
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  else
    quote_cmd "$@"
  fi
}

ensure_dirs() {
  run_cmd mkdir -p "$BRAIN_KNOWLEDGE_DIR" "$BRAIN_INBOX_DIR" "$ZOTERO_WEBDAV_DIR" "$CONFIG_DIR"
  if [ "$APPLY" -eq 1 ]; then
    chmod 700 "$CONFIG_DIR"
  else
    quote_cmd chmod 700 "$CONFIG_DIR"
  fi
}

load_credentials() {
  if [ ! -f "$CREDENTIAL_ENV" ]; then
    printf 'Credential env not found: %s\n' "$CREDENTIAL_ENV" >&2
    return 1
  fi
  # shellcheck disable=SC1090
  . "$CREDENTIAL_ENV"
  if [ -z "${WEBDAV_USER:-}" ] || [ -z "${WEBDAV_PASS:-}" ]; then
    printf 'Credential env must define WEBDAV_USER and WEBDAV_PASS\n' >&2
    return 1
  fi
}

write_credentials_if_needed() {
  if [ -f "$CREDENTIAL_ENV" ] && [ -f "$HTPASSWD_FILE" ]; then
    return 0
  fi

  if [ "$APPLY" -ne 1 ]; then
    quote_cmd install -m 600 /dev/null "$CREDENTIAL_ENV"
    printf '# generate WEBDAV_USER/WEBDAV_PASS and htpasswd without printing secrets\n'
    return 0
  fi

  mkdir -p "$CONFIG_DIR"
  chmod 700 "$CONFIG_DIR"

  if [ ! -f "$CREDENTIAL_ENV" ]; then
    local user pass
    user=${WEBDAV_USER:-$WEBDAV_USER_DEFAULT}
    if [ -n "${WEBDAV_PASS:-}" ]; then
      pass=$WEBDAV_PASS
    elif command -v openssl >/dev/null 2>&1; then
      pass=$(openssl rand -hex 24)
    else
      pass=$(LC_ALL=C dd if=/dev/urandom bs=24 count=1 2>/dev/null | od -An -tx1 | tr -d ' \n')
    fi
    umask 077
    {
      printf 'WEBDAV_USER=%s\n' "$user"
      printf 'WEBDAV_PASS=%s\n' "$pass"
    } > "$CREDENTIAL_ENV"
    chmod 600 "$CREDENTIAL_ENV"
  fi

  load_credentials
  if [ ! -f "$HTPASSWD_FILE" ]; then
    umask 077
    if command -v htpasswd >/dev/null 2>&1; then
      htpasswd -Bbn "$WEBDAV_USER" "$WEBDAV_PASS" > "$HTPASSWD_FILE"
    elif command -v openssl >/dev/null 2>&1; then
      printf '%s:%s\n' "$WEBDAV_USER" "$(openssl passwd -apr1 "$WEBDAV_PASS")" > "$HTPASSWD_FILE"
    else
      printf 'Need htpasswd or openssl to create %s\n' "$HTPASSWD_FILE" >&2
      return 1
    fi
    chmod 600 "$HTPASSWD_FILE"
  fi
}

docker_pull() {
  run_cmd docker pull "$RCLONE_IMAGE"
}

remove_container() {
  local name=$1
  if [ "$APPLY" -eq 1 ]; then
    docker rm -f "$name" >/dev/null 2>&1 || true
  else
    printf 'docker rm -f %q || true\n' "$name"
  fi
}

start_container() {
  local name=$1
  local port=$2
  local host_dir=$3
  local volume_mode=$4
  local baseurl=$5
  local read_only=$6

  remove_container "$name"
  local args=(
    docker run -d
    --name "$name"
    --restart unless-stopped
    -p "$BIND_ADDR:$port:8080"
    -v "$host_dir:/data:$volume_mode"
    -v "$HTPASSWD_FILE:/config/htpasswd:ro"
    "$RCLONE_IMAGE"
    serve webdav /data
    --addr :8080
    --baseurl "$baseurl"
    --htpasswd /config/htpasswd
  )
  if [ "$read_only" = "yes" ]; then
    args+=(--read-only)
  fi
  run_cmd "${args[@]}"
}

apply_stack() {
  ensure_dirs
  write_credentials_if_needed
  docker_pull
  start_container brain-webdav-ro "$BRAIN_PORT" "$BRAIN_KNOWLEDGE_DIR" ro /brain yes
  start_container brain-webdav-inbox "$INBOX_PORT" "$BRAIN_INBOX_DIR" rw /brain-inbox no
  start_container zotero-webdav-sync "$ZOTERO_PORT" "$ZOTERO_WEBDAV_DIR" rw /zotero-sync no
}

status_stack() {
  printf 'Containers:\n'
  docker ps --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' \
    | awk 'NR == 1 || $1 == "brain-webdav-ro" || $1 == "brain-webdav-inbox" || $1 == "zotero-webdav-sync"'
  printf '\nPaths:\n'
  for path in "$BRAIN_KNOWLEDGE_DIR" "$BRAIN_INBOX_DIR" "$ZOTERO_WEBDAV_DIR" "$CREDENTIAL_ENV" "$HTPASSWD_FILE"; do
    if [ -e "$path" ]; then
      printf 'ok   %s\n' "$path"
    else
      printf 'miss %s\n' "$path"
    fi
  done
}

http_code() {
  curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
    -sS -o /dev/null -w '%{http_code}' "$@"
}

expect_code() {
  local label=$1
  local got=$2
  shift 2
  local wanted
  for wanted in "$@"; do
    if [ "$got" = "$wanted" ]; then
      printf 'ok   %s -> %s\n' "$label" "$got"
      return 0
    fi
  done
  printf 'fail %s -> %s, expected one of: %s\n' "$label" "$got" "$*" >&2
  return 1
}

verify_stack() {
  load_credentials
  local brain_url="http://$BIND_ADDR:$BRAIN_PORT/brain/"
  local inbox_url="http://$BIND_ADDR:$INBOX_PORT/brain-inbox/"
  local zotero_url="http://$BIND_ADDR:$ZOTERO_PORT/zotero-sync/"
  local auth=(-u "$WEBDAV_USER:$WEBDAV_PASS")
  local stamp tmp code body
  stamp=$(date +%Y%m%d-%H%M%S)
  tmp=$(mktemp)
  trap 'rm -f "${tmp:-}"' EXIT
  printf 'rtime webdav smoke %s\n' "$stamp" > "$tmp"

  code=$(http_code "$brain_url")
  expect_code 'unauthenticated /brain/' "$code" 401
  code=$(http_code "$inbox_url")
  expect_code 'unauthenticated /brain-inbox/' "$code" 401
  code=$(http_code "$zotero_url")
  expect_code 'unauthenticated /zotero-sync/' "$code" 401

  code=$(http_code "${auth[@]}" -X PROPFIND -H 'Depth: 0' "$brain_url")
  expect_code 'PROPFIND /brain/' "$code" 207

  code=$(http_code "${auth[@]}" -T "$tmp" "$brain_url.rtime-ro-test-$stamp.txt")
  case "$code" in
    200|201|204)
      curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
        -sS "${auth[@]}" -X DELETE "$brain_url.rtime-ro-test-$stamp.txt" >/dev/null || true
      printf 'fail read-only /brain/ accepted PUT -> %s\n' "$code" >&2
      return 1
      ;;
    *)
      printf 'ok   read-only /brain/ rejected PUT -> %s\n' "$code"
      ;;
  esac

  code=$(http_code "${auth[@]}" -T "$tmp" "$inbox_url.rtime-inbox-test-$stamp.txt")
  expect_code 'PUT /brain-inbox/' "$code" 200 201 204
  body=$(curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
    -sS "${auth[@]}" "$inbox_url.rtime-inbox-test-$stamp.txt")
  if [ "$body" != "$(cat "$tmp")" ]; then
    printf 'fail GET /brain-inbox/ returned unexpected body\n' >&2
    return 1
  fi
  printf 'ok   GET /brain-inbox/ content\n'
  code=$(http_code "${auth[@]}" -X DELETE "$inbox_url.rtime-inbox-test-$stamp.txt")
  expect_code 'DELETE /brain-inbox/' "$code" 200 202 204

  code=$(http_code "${auth[@]}" -X MKCOL "${zotero_url}zotero/")
  expect_code 'MKCOL /zotero-sync/zotero/' "$code" 201 405
  code=$(http_code "${auth[@]}" -T "$tmp" "${zotero_url}zotero/rtime-zotero-test-$stamp.txt")
  expect_code 'PUT /zotero-sync/zotero/' "$code" 200 201 204
  body=$(curl --connect-timeout "$CURL_CONNECT_TIMEOUT" --max-time "$CURL_MAX_TIME" \
    -sS "${auth[@]}" "${zotero_url}zotero/rtime-zotero-test-$stamp.txt")
  if [ "$body" != "$(cat "$tmp")" ]; then
    printf 'fail GET /zotero-sync/zotero/ returned unexpected body\n' >&2
    return 1
  fi
  printf 'ok   GET /zotero-sync/zotero/ content\n'
  code=$(http_code "${auth[@]}" -X DELETE "${zotero_url}zotero/rtime-zotero-test-$stamp.txt")
  expect_code 'DELETE /zotero-sync/zotero/' "$code" 200 202 204
}

stop_stack() {
  remove_container brain-webdav-ro
  remove_container brain-webdav-inbox
  remove_container zotero-webdav-sync
}

case "$ACTION" in
  help|-h|--help)
    usage
    ;;
  plan)
    print_config
    printf '\nPlanned operations:\n'
    apply_stack
    ;;
  apply)
    print_config
    apply_stack
    status_stack
    ;;
  status)
    print_config
    status_stack
    ;;
  verify)
    print_config
    verify_stack
    ;;
  stop)
    stop_stack
    ;;
esac
