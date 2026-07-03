#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -euo pipefail

APPLY=0
PROFILE="auto"
REPO_ROOT=""
PYTHON_BIN="${PYTHON:-python3}"
CODEX_HOME="${CODEX_HOME:-$HOME/.codex}"
CLAUDE_HOME="${CLAUDE_HOME:-$HOME/.claude}"
PLUGIN_HOME="${PLUGIN_HOME:-$HOME/plugins}"
MARKETPLACE_PATH="${CODEX_MARKETPLACE:-$HOME/.agents/plugins/marketplace.json}"
USER_BIN="${RTIME_TOOLING_BIN_DIR:-$HOME/.local/bin}"
INSTALL_CLI=1
SYNC_CODEX_SKILL=1
SYNC_CLAUDE_SKILL=1
SYNC_CODEX_PLUGIN=1
WRITE_CODEX_MARKETPLACE=0
PRINT_MCP_SNIPPET=1

usage() {
  cat <<'EOF'
Usage: scripts/install-brain-docpack-tooling.sh [options]

Safely install or sync the repository-owned brain-docpack CLI, skills, plugin,
and MCP stdio wrapper for Mac or orangepi clients.

Default mode is dry-run. Pass --apply to write user-level files.

Options:
  --apply                      perform writes; default only prints actions
  --profile mac|orangepi|auto  choose path hints; default auto
  --repo-root PATH             rtime-assistant repository root
  --python PATH                Python executable; default python3
  --codex-home PATH            Codex home; default $CODEX_HOME or ~/.codex
  --claude-home PATH           Claude Code home; default $CLAUDE_HOME or ~/.claude
  --plugin-home PATH           Codex personal plugin source root; default ~/plugins
  --marketplace PATH           Codex marketplace JSON; default ~/.agents/plugins/marketplace.json
  --bin-dir PATH               CLI wrapper directory; default $RTIME_TOOLING_BIN_DIR or ~/.local/bin
  --write-codex-marketplace    add/update brain-docpack in the marketplace JSON
  --skip-cli                   do not run pip editable install
  --skip-codex-skill           do not sync to Codex skills
  --skip-claude-skill          do not sync to Claude Code skills
  --skip-codex-plugin          do not sync Codex plugin source
  --no-mcp-snippet             do not print standalone MCP config snippet
  -h, --help                   show this help
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'error: %s\n' "$*" >&2
  exit 2
}

resolve_path() {
  "$PYTHON_BIN" - "$1" <<'PY'
from pathlib import Path
import sys
print(Path(sys.argv[1]).expanduser().resolve())
PY
}

script_repo_root() {
  local script_dir
  script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  cd "$script_dir/.."
  pwd
}

detect_profile() {
  if [ "$PROFILE" != "auto" ]; then
    return
  fi
  if [ "$(uname -s)" = "Darwin" ]; then
    PROFILE="mac"
  elif [ -d /mnt/brain ] || [ -n "${RTIME_RUNTIME_HOST:-}" ]; then
    PROFILE="orangepi"
  else
    PROFILE="mac"
  fi
}

run_or_print() {
  if [ "$APPLY" -eq 1 ]; then
    "$@"
  else
    printf 'dry-run:'
    printf ' %q' "$@"
    printf '\n'
  fi
}

install_python_package() {
  local package_path="$1"
  if [ "$APPLY" -eq 0 ]; then
    printf 'dry-run:'
    printf ' %q' "$PYTHON_BIN" -m pip install -e "$package_path"
    printf ' ||'
    printf ' %q' "$PYTHON_BIN" -m pip install "$package_path"
    printf '\n'
    return
  fi
  "$PYTHON_BIN" -m pip install -e "$package_path" || "$PYTHON_BIN" -m pip install "$package_path"
}

write_python_wrapper() {
  local target="$1"
  local module="$2"
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: write Python wrapper $target -> $module"
    return
  fi
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/packages/brain-docpack/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PYTHON_BIN" -m "$module" "\$@"
EOF
  chmod +x "$target"
}

write_cli_wrappers() {
  write_python_wrapper "$USER_BIN/brain-docpack" "brain_docpack.cli"
  write_python_wrapper "$USER_BIN/brain-docpack-mcp" "brain_docpack.mcp_server"
}

copy_dir() {
  local src="$1"
  local dst="$2"
  [ -d "$src" ] || die "source directory missing: $src"
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: sync $src -> $dst"
    return
  fi
  "$PYTHON_BIN" - "$src" "$dst" <<'PY'
from pathlib import Path
import shutil
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
dst.parent.mkdir(parents=True, exist_ok=True)
if dst.exists():
    shutil.rmtree(dst)
shutil.copytree(src, dst)
PY
}

write_marketplace() {
  local plugin_dest="$1"
  local marketplace="$2"
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: add/update brain-docpack in $marketplace"
    return
  fi
  "$PYTHON_BIN" - "$plugin_dest" "$marketplace" <<'PY'
from pathlib import Path
import json
import os
import sys

plugin_dest = Path(sys.argv[1]).expanduser().resolve()
marketplace = Path(sys.argv[2]).expanduser().resolve()
home_plugin = Path.home() / "plugins" / "brain-docpack"
if plugin_dest == home_plugin:
    source_path = "./plugins/brain-docpack"
else:
    source_path = str(plugin_dest)

if marketplace.exists():
    data = json.loads(marketplace.read_text(encoding="utf-8"))
else:
    data = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

plugins = data.setdefault("plugins", [])
entry = {
    "name": "brain-docpack",
    "source": {"source": "local", "path": source_path},
    "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
    "category": "Productivity",
}
for index, item in enumerate(plugins):
    if item.get("name") == "brain-docpack":
        plugins[index] = entry
        break
else:
    plugins.append(entry)

marketplace.parent.mkdir(parents=True, exist_ok=True)
marketplace.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

print_mcp_snippet() {
  local repo="$1"
  cat <<EOF

Standalone MCP client snippet:
{
  "mcpServers": {
    "brain-docpack": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "brain_docpack.mcp_server"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$repo",
        "PYTHONPATH": "$repo/packages/brain-docpack/src"
      }
    }
  }
}
EOF
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --apply)
      APPLY=1
      ;;
    --profile)
      shift
      PROFILE="${1:-}"
      ;;
    --repo-root)
      shift
      REPO_ROOT="${1:-}"
      ;;
    --python)
      shift
      PYTHON_BIN="${1:-}"
      ;;
    --codex-home)
      shift
      CODEX_HOME="${1:-}"
      ;;
    --claude-home)
      shift
      CLAUDE_HOME="${1:-}"
      ;;
    --plugin-home)
      shift
      PLUGIN_HOME="${1:-}"
      ;;
    --marketplace)
      shift
      MARKETPLACE_PATH="${1:-}"
      ;;
    --bin-dir)
      shift
      USER_BIN="${1:-}"
      ;;
    --write-codex-marketplace)
      WRITE_CODEX_MARKETPLACE=1
      ;;
    --skip-cli)
      INSTALL_CLI=0
      ;;
    --skip-codex-skill)
      SYNC_CODEX_SKILL=0
      ;;
    --skip-claude-skill)
      SYNC_CLAUDE_SKILL=0
      ;;
    --skip-codex-plugin)
      SYNC_CODEX_PLUGIN=0
      ;;
    --no-mcp-snippet)
      PRINT_MCP_SNIPPET=0
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

[ -n "$PYTHON_BIN" ] || die "--python must not be empty"
if [ -z "$REPO_ROOT" ]; then
  REPO_ROOT="$(script_repo_root)"
else
  REPO_ROOT="$(resolve_path "$REPO_ROOT")"
fi
detect_profile

case "$PROFILE" in
  mac|orangepi|auto) ;;
  *) die "--profile must be mac, orangepi, or auto" ;;
esac

[ -f "$REPO_ROOT/packages/brain-docpack/src/brain_docpack/cli.py" ] \
  || die "brain-docpack package not found under $REPO_ROOT"
[ -d "$REPO_ROOT/skills/brain-docpack" ] \
  || die "brain-docpack skill not found under $REPO_ROOT"
[ -d "$REPO_ROOT/plugins/brain-docpack" ] \
  || die "brain-docpack plugin not found under $REPO_ROOT"

CODEX_HOME="$(resolve_path "$CODEX_HOME")"
CLAUDE_HOME="$(resolve_path "$CLAUDE_HOME")"
PLUGIN_HOME="$(resolve_path "$PLUGIN_HOME")"
MARKETPLACE_PATH="$(resolve_path "$MARKETPLACE_PATH")"
USER_BIN="$(resolve_path "$USER_BIN")"

CODEX_SKILL_DEST="$CODEX_HOME/skills/brain-docpack"
CLAUDE_SKILL_DEST="$CLAUDE_HOME/skills/brain-docpack"
CODEX_PLUGIN_DEST="$PLUGIN_HOME/brain-docpack"

log "brain-docpack tooling install"
log "- mode: $([ "$APPLY" -eq 1 ] && printf apply || printf dry-run)"
log "- profile: $PROFILE"
log "- repo_root: $REPO_ROOT"
log "- codex_skill: $CODEX_SKILL_DEST"
log "- claude_skill: $CLAUDE_SKILL_DEST"
log "- codex_plugin: $CODEX_PLUGIN_DEST"
log "- marketplace: $MARKETPLACE_PATH"
log "- bin_dir: $USER_BIN"

if [ "$INSTALL_CLI" -eq 1 ]; then
  install_python_package "$REPO_ROOT/packages/brain-docpack"
  write_cli_wrappers
fi

if [ "$SYNC_CODEX_SKILL" -eq 1 ]; then
  copy_dir "$REPO_ROOT/skills/brain-docpack" "$CODEX_SKILL_DEST"
fi

if [ "$SYNC_CLAUDE_SKILL" -eq 1 ]; then
  copy_dir "$REPO_ROOT/skills/brain-docpack" "$CLAUDE_SKILL_DEST"
fi

if [ "$SYNC_CODEX_PLUGIN" -eq 1 ]; then
  copy_dir "$REPO_ROOT/plugins/brain-docpack" "$CODEX_PLUGIN_DEST"
fi

if [ "$WRITE_CODEX_MARKETPLACE" -eq 1 ]; then
  write_marketplace "$CODEX_PLUGIN_DEST" "$MARKETPLACE_PATH"
fi

if [ "$PRINT_MCP_SNIPPET" -eq 1 ]; then
  print_mcp_snippet "$REPO_ROOT"
fi

log ""
log "Validation hints:"
if [ "$INSTALL_CLI" -eq 1 ]; then
  log "  $USER_BIN/brain-docpack doctor"
  log "  printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\\n' | $USER_BIN/brain-docpack-mcp"
  case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *) log "  note: $USER_BIN is not on the current PATH; use the absolute wrapper path in non-interactive runners." ;;
  esac
else
  log "  brain-docpack doctor"
  log "  printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\\n' | brain-docpack-mcp"
fi
