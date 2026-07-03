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
REQUESTED_TOOLS=""
INSTALL_CLI=1
SYNC_CODEX_SKILL=1
SYNC_CLAUDE_SKILL=1
SYNC_CODEX_PLUGIN=1
WRITE_CODEX_MARKETPLACE=0
PRINT_MCP_SNIPPETS=1
WRITE_MCP_CONFIG=""
CHECK_INSTALLED=0

DEFAULT_TOOLS="brain-docpack brain-library brain-citation rtime-assistant-runtime rtime-hub-connector rtime-context rtime-profile rtime-automation rtime-review rtime-agent-control rtime-library-gateway"

usage() {
  cat <<'EOF'
Usage: scripts/install-rtime-tooling.sh [options]

Safely install or sync repository-owned rtime assistant tool packages, skills,
plugins, and MCP stdio wrappers for Mac or orangepi clients.

Default mode is dry-run. Pass --apply to write user-level files.

Options:
  --apply                      perform writes; default only prints actions
  --profile mac|orangepi|auto  choose path hints; default auto
  --repo-root PATH             rtime-assistant repository root
  --python PATH                Python executable; default python3
  --tool NAME                  install one tool; repeatable; default all
                                names: brain-docpack, brain-library,
                                brain-citation,
                                rtime-assistant-runtime, rtime-hub-connector,
                                rtime-context, rtime-profile,
                                rtime-automation, rtime-review,
                                rtime-agent-control, rtime-library-gateway
  --codex-home PATH            Codex home; default $CODEX_HOME or ~/.codex
  --claude-home PATH           Claude Code home; default $CLAUDE_HOME or ~/.claude
  --plugin-home PATH           Codex personal plugin source root; default ~/plugins
  --marketplace PATH           Codex marketplace JSON; default ~/.agents/plugins/marketplace.json
  --bin-dir PATH               CLI wrapper directory; default $RTIME_TOOLING_BIN_DIR or ~/.local/bin
  --write-codex-marketplace    add/update selected plugins in the marketplace JSON
  --write-mcp-config PATH      write a combined standalone MCP config JSON
  --check-installed            read-only JSON health check of installed tool surfaces
  --skip-cli                   do not run pip editable installs
  --skip-codex-skill           do not sync to Codex skills
  --skip-claude-skill          do not sync to Claude Code skills
  --skip-codex-plugin          do not sync Codex plugin sources
  --no-mcp-snippets            do not print standalone MCP config snippets
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

default_brain_root() {
  if [ "$PROFILE" = "orangepi" ]; then
    printf '%s\n' "${BRAIN_ROOT:-/mnt/brain}"
  else
    printf '%s\n' "${BRAIN_ROOT:-$HOME/OrangePi-Store/sync/brain}"
  fi
}

default_hub_root() {
  if [ "$PROFILE" = "orangepi" ]; then
    printf '%s\n' "${RTIME_HUB_ROOT:-/srv/rtime-hub}"
  else
    printf '%s\n' "${RTIME_HUB_ROOT:-$HOME/rtime-hub}"
  fi
}

default_reminders_path() {
  if [ "$PROFILE" = "orangepi" ]; then
    printf '%s\n' "${RTIME_REMINDERS_PATH:-/mnt/brain/_system/reminders.jsonl}"
  else
    printf '%s\n' "${RTIME_REMINDERS_PATH:-$HOME/OrangePi-Store/sync/brain/_system/reminders.jsonl}"
  fi
}

normalize_requested_tools() {
  if [ -z "$REQUESTED_TOOLS" ]; then
    printf '%s\n' "$DEFAULT_TOOLS"
    return
  fi
  printf '%s\n' "$REQUESTED_TOOLS" | tr ',' ' '
}

tool_package_dir() {
  case "$1" in
    brain-docpack) printf '%s\n' "packages/brain-docpack" ;;
    brain-library) printf '%s\n' "packages/brain-library" ;;
    brain-citation) printf '%s\n' "packages/brain-citation" ;;
    rtime-assistant-runtime) printf '%s\n' "packages/rtime-assistant-runtime" ;;
    rtime-hub-connector) printf '%s\n' "packages/rtime-hub-connector" ;;
    rtime-context) printf '%s\n' "packages/rtime-context" ;;
    rtime-profile) printf '%s\n' "packages/rtime-profile" ;;
    rtime-automation) printf '%s\n' "packages/rtime-automation" ;;
    rtime-review) printf '%s\n' "packages/rtime-review" ;;
    rtime-agent-control) printf '%s\n' "packages/rtime-agent-control" ;;
    rtime-library-gateway) printf '%s\n' "packages/rtime-library-gateway" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_cli_command() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain-docpack doctor" ;;
    brain-library) printf '%s\n' "brain-library doctor $(default_brain_root)" ;;
    brain-citation) printf '%s\n' "brain-citation doctor $(default_brain_root)" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime-runtime doctor" ;;
    rtime-hub-connector) printf '%s\n' "rtime-hub-connector doctor $(default_hub_root)" ;;
    rtime-context) printf '%s\n' "rtime-context doctor" ;;
    rtime-profile) printf '%s\n' "rtime-profile doctor" ;;
    rtime-automation) printf '%s\n' "rtime-automation doctor" ;;
    rtime-review) printf '%s\n' "rtime-review doctor" ;;
    rtime-agent-control) printf '%s\n' "rtime-agent-control doctor" ;;
    rtime-library-gateway) printf '%s\n' "rtime-library-gateway doctor" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_cli_name() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain-docpack" ;;
    brain-library) printf '%s\n' "brain-library" ;;
    brain-citation) printf '%s\n' "brain-citation" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime-runtime" ;;
    rtime-hub-connector) printf '%s\n' "rtime-hub-connector" ;;
    rtime-context) printf '%s\n' "rtime-context" ;;
    rtime-profile) printf '%s\n' "rtime-profile" ;;
    rtime-automation) printf '%s\n' "rtime-automation" ;;
    rtime-review) printf '%s\n' "rtime-review" ;;
    rtime-agent-control) printf '%s\n' "rtime-agent-control" ;;
    rtime-library-gateway) printf '%s\n' "rtime-library-gateway" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_cli_module() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain_docpack.cli" ;;
    brain-library) printf '%s\n' "brain_library.cli" ;;
    brain-citation) printf '%s\n' "brain_citation.cli" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime_assistant_runtime.cli" ;;
    rtime-hub-connector) printf '%s\n' "rtime_hub_connector.cli" ;;
    rtime-context) printf '%s\n' "rtime_context.cli" ;;
    rtime-profile) printf '%s\n' "rtime_profile.cli" ;;
    rtime-automation) printf '%s\n' "rtime_automation.cli" ;;
    rtime-review) printf '%s\n' "rtime_review.cli" ;;
    rtime-agent-control) printf '%s\n' "rtime_agent_control.cli" ;;
    rtime-library-gateway) printf '%s\n' "rtime_library_gateway.cli" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_mcp_module() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain_docpack.mcp_server" ;;
    brain-library) printf '%s\n' "brain_library.mcp_server" ;;
    brain-citation) printf '%s\n' "brain_citation.mcp_server" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime_assistant_runtime.mcp_server" ;;
    rtime-hub-connector) printf '%s\n' "rtime_hub_connector.mcp_server" ;;
    rtime-context) printf '%s\n' "rtime_context.mcp_server" ;;
    rtime-profile) printf '%s\n' "rtime_profile.mcp_server" ;;
    rtime-automation) printf '%s\n' "rtime_automation.mcp_server" ;;
    rtime-review) printf '%s\n' "rtime_review.mcp_server" ;;
    rtime-agent-control) printf '%s\n' "rtime_agent_control.mcp_server" ;;
    rtime-library-gateway) printf '%s\n' "rtime_library_gateway.mcp_server" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_mcp_cli_name() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain-docpack-mcp" ;;
    brain-library) printf '%s\n' "brain-library-mcp" ;;
    brain-citation) printf '%s\n' "brain-citation-mcp" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime-runtime-mcp" ;;
    rtime-hub-connector) printf '%s\n' "rtime-hub-mcp" ;;
    rtime-context) printf '%s\n' "rtime-context-mcp" ;;
    rtime-profile) printf '%s\n' "rtime-profile-mcp" ;;
    rtime-automation) printf '%s\n' "rtime-automation-mcp" ;;
    rtime-review) printf '%s\n' "rtime-review-mcp" ;;
    rtime-agent-control) printf '%s\n' "rtime-agent-control-mcp" ;;
    rtime-library-gateway) printf '%s\n' "rtime-library-gateway-mcp" ;;
    *) die "unknown tool: $1" ;;
  esac
}

tool_mcp_name() {
  case "$1" in
    brain-docpack) printf '%s\n' "brain-docpack" ;;
    brain-library) printf '%s\n' "brain-library" ;;
    brain-citation) printf '%s\n' "brain-citation" ;;
    rtime-assistant-runtime) printf '%s\n' "rtime-assistant-runtime" ;;
    rtime-hub-connector) printf '%s\n' "rtime-hub-connector" ;;
    rtime-context) printf '%s\n' "rtime-context" ;;
    rtime-profile) printf '%s\n' "rtime-profile" ;;
    rtime-automation) printf '%s\n' "rtime-automation" ;;
    rtime-review) printf '%s\n' "rtime-review" ;;
    rtime-agent-control) printf '%s\n' "rtime-agent-control" ;;
    rtime-library-gateway) printf '%s\n' "rtime-library-gateway" ;;
    *) die "unknown tool: $1" ;;
  esac
}

validate_tool_source() {
  local tool="$1"
  local package_dir
  package_dir="$(tool_package_dir "$tool")"
  [ -f "$REPO_ROOT/$package_dir/pyproject.toml" ] \
    || die "$tool package not found under $REPO_ROOT"
  [ -d "$REPO_ROOT/skills/$tool" ] \
    || die "$tool skill not found under $REPO_ROOT"
  [ -d "$REPO_ROOT/plugins/$tool" ] \
    || die "$tool plugin not found under $REPO_ROOT"
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
  local package_dir="$2"
  local module="$3"
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: write Python wrapper $target -> $module"
    return
  fi
  mkdir -p "$(dirname "$target")"
  cat > "$target" <<EOF
#!/usr/bin/env bash
set -euo pipefail
export RTIME_ASSISTANT_ROOT="$REPO_ROOT"
export PYTHONPATH="$REPO_ROOT/$package_dir/src\${PYTHONPATH:+:\$PYTHONPATH}"
exec "$PYTHON_BIN" -m "$module" "\$@"
EOF
  chmod +x "$target"
}

write_cli_wrappers() {
  local tool="$1"
  local package_dir="$2"
  write_python_wrapper "$USER_BIN/$(tool_cli_name "$tool")" "$package_dir" "$(tool_cli_module "$tool")"
  write_python_wrapper "$USER_BIN/$(tool_mcp_cli_name "$tool")" "$package_dir" "$(tool_mcp_module "$tool")"
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
  local marketplace="$1"
  shift
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: add/update selected tools in $marketplace"
    return
  fi
  "$PYTHON_BIN" - "$PLUGIN_HOME" "$marketplace" "$@" <<'PY'
from pathlib import Path
import json
import sys

plugin_home = Path(sys.argv[1]).expanduser().resolve()
marketplace = Path(sys.argv[2]).expanduser().resolve()
tools = sys.argv[3:]

if marketplace.exists():
    data = json.loads(marketplace.read_text(encoding="utf-8"))
else:
    data = {
        "name": "personal",
        "interface": {"displayName": "Personal"},
        "plugins": [],
    }

plugins = data.setdefault("plugins", [])
home_plugin_root = Path.home() / "plugins"

for tool in tools:
    plugin_dest = (plugin_home / tool).resolve()
    if plugin_dest == (home_plugin_root / tool):
        source_path = f"./plugins/{tool}"
    else:
        source_path = str(plugin_dest)
    entry = {
        "name": tool,
        "source": {"source": "local", "path": source_path},
        "policy": {"installation": "AVAILABLE", "authentication": "ON_INSTALL"},
        "category": "Productivity",
    }
    for index, item in enumerate(plugins):
        if item.get("name") == tool:
            plugins[index] = entry
            break
    else:
        plugins.append(entry)

marketplace.parent.mkdir(parents=True, exist_ok=True)
marketplace.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

write_mcp_config() {
  local config_path="$1"
  shift
  if [ "$APPLY" -eq 0 ]; then
    log "dry-run: write combined MCP config $config_path"
    return
  fi
  "$PYTHON_BIN" - "$config_path" "$REPO_ROOT" "$PYTHON_BIN" "$(default_brain_root)" "$(default_hub_root)" "$(default_reminders_path)" "$@" <<'PY'
from pathlib import Path
import json
import sys

config_path = Path(sys.argv[1]).expanduser().resolve()
repo_root = Path(sys.argv[2]).expanduser().resolve()
python_bin = sys.argv[3]
brain_root = sys.argv[4]
hub_root = sys.argv[5]
reminders_path = sys.argv[6]
tools = sys.argv[7:]

packages = {
    "brain-docpack": "packages/brain-docpack",
    "brain-library": "packages/brain-library",
    "brain-citation": "packages/brain-citation",
    "rtime-assistant-runtime": "packages/rtime-assistant-runtime",
    "rtime-hub-connector": "packages/rtime-hub-connector",
    "rtime-context": "packages/rtime-context",
    "rtime-profile": "packages/rtime-profile",
    "rtime-automation": "packages/rtime-automation",
    "rtime-review": "packages/rtime-review",
    "rtime-agent-control": "packages/rtime-agent-control",
    "rtime-library-gateway": "packages/rtime-library-gateway",
}
modules = {
    "brain-docpack": "brain_docpack.mcp_server",
    "brain-library": "brain_library.mcp_server",
    "brain-citation": "brain_citation.mcp_server",
    "rtime-assistant-runtime": "rtime_assistant_runtime.mcp_server",
    "rtime-hub-connector": "rtime_hub_connector.mcp_server",
    "rtime-context": "rtime_context.mcp_server",
    "rtime-profile": "rtime_profile.mcp_server",
    "rtime-automation": "rtime_automation.mcp_server",
    "rtime-review": "rtime_review.mcp_server",
    "rtime-agent-control": "rtime_agent_control.mcp_server",
    "rtime-library-gateway": "rtime_library_gateway.mcp_server",
}
server_names = {
    "brain-docpack": "brain-docpack",
    "brain-library": "brain-library",
    "brain-citation": "brain-citation",
    "rtime-assistant-runtime": "rtime-assistant-runtime",
    "rtime-hub-connector": "rtime-hub-connector",
    "rtime-context": "rtime-context",
    "rtime-profile": "rtime-profile",
    "rtime-automation": "rtime-automation",
    "rtime-review": "rtime-review",
    "rtime-agent-control": "rtime-agent-control",
    "rtime-library-gateway": "rtime-library-gateway",
}


def env_for(tool: str) -> dict[str, str]:
    env = {
        "RTIME_ASSISTANT_ROOT": str(repo_root),
        "PYTHONPATH": str(repo_root / packages[tool] / "src"),
    }
    if tool in {"brain-library", "brain-citation", "rtime-profile"}:
        env["BRAIN_ROOT"] = brain_root
    if tool == "rtime-hub-connector":
        env["RTIME_HUB_ROOT"] = hub_root
    if tool == "rtime-context":
        env["BRAIN_ROOT"] = brain_root
        env["RTIME_HUB_ROOT"] = hub_root
    if tool == "rtime-automation":
        env["RTIME_REMINDERS_PATH"] = reminders_path
    if tool == "rtime-agent-control":
        env["BRAIN_ROOT"] = brain_root
        env["RTIME_HUB_ROOT"] = hub_root
        env["RTIME_REMINDERS_PATH"] = reminders_path
    if tool == "rtime-library-gateway":
        env["BRAIN_ROOT"] = brain_root
        env["RTIME_HUB_ROOT"] = hub_root
        env["RTIME_REMINDERS_PATH"] = reminders_path
    return env


config = {
    "mcpServers": {
        server_names[tool]: {
            "command": python_bin,
            "args": ["-m", modules[tool]],
            "env": env_for(tool),
        }
        for tool in tools
    }
}
config_path.parent.mkdir(parents=True, exist_ok=True)
config_path.write_text(json.dumps(config, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY
}

check_installed() {
  "$PYTHON_BIN" - \
    "$PROFILE" \
    "$REPO_ROOT" \
    "$PYTHON_BIN" \
    "$USER_BIN" \
    "$CODEX_HOME" \
    "$CLAUDE_HOME" \
    "$PLUGIN_HOME" \
    "$MARKETPLACE_PATH" \
    "$WRITE_MCP_CONFIG" \
    "$INSTALL_CLI" \
    "$SYNC_CODEX_SKILL" \
    "$SYNC_CLAUDE_SKILL" \
    "$SYNC_CODEX_PLUGIN" \
    "$WRITE_CODEX_MARKETPLACE" \
    "$@" <<'PY'
from pathlib import Path
import json
import os
import subprocess
import sys

profile = sys.argv[1]
repo_root = Path(sys.argv[2]).expanduser().resolve()
python_bin = sys.argv[3]
user_bin = Path(sys.argv[4]).expanduser().resolve()
codex_home = Path(sys.argv[5]).expanduser().resolve()
claude_home = Path(sys.argv[6]).expanduser().resolve()
plugin_home = Path(sys.argv[7]).expanduser().resolve()
marketplace_path = Path(sys.argv[8]).expanduser().resolve()
mcp_config_arg = sys.argv[9]
install_cli = sys.argv[10] == "1"
sync_codex_skill = sys.argv[11] == "1"
sync_claude_skill = sys.argv[12] == "1"
sync_codex_plugin = sys.argv[13] == "1"
write_codex_marketplace = sys.argv[14] == "1"
tools = sys.argv[15:]

packages = {
    "brain-docpack": "packages/brain-docpack",
    "brain-library": "packages/brain-library",
    "brain-citation": "packages/brain-citation",
    "rtime-assistant-runtime": "packages/rtime-assistant-runtime",
    "rtime-hub-connector": "packages/rtime-hub-connector",
    "rtime-context": "packages/rtime-context",
    "rtime-profile": "packages/rtime-profile",
    "rtime-automation": "packages/rtime-automation",
    "rtime-review": "packages/rtime-review",
    "rtime-agent-control": "packages/rtime-agent-control",
    "rtime-library-gateway": "packages/rtime-library-gateway",
}
module_roots = {
    "brain-docpack": "brain_docpack",
    "brain-library": "brain_library",
    "brain-citation": "brain_citation",
    "rtime-assistant-runtime": "rtime_assistant_runtime",
    "rtime-hub-connector": "rtime_hub_connector",
    "rtime-context": "rtime_context",
    "rtime-profile": "rtime_profile",
    "rtime-automation": "rtime_automation",
    "rtime-review": "rtime_review",
    "rtime-agent-control": "rtime_agent_control",
    "rtime-library-gateway": "rtime_library_gateway",
}
cli_names = {
    "brain-docpack": "brain-docpack",
    "brain-library": "brain-library",
    "brain-citation": "brain-citation",
    "rtime-assistant-runtime": "rtime-runtime",
    "rtime-hub-connector": "rtime-hub-connector",
    "rtime-context": "rtime-context",
    "rtime-profile": "rtime-profile",
    "rtime-automation": "rtime-automation",
    "rtime-review": "rtime-review",
    "rtime-agent-control": "rtime-agent-control",
    "rtime-library-gateway": "rtime-library-gateway",
}
mcp_cli_names = {
    "brain-docpack": "brain-docpack-mcp",
    "brain-library": "brain-library-mcp",
    "brain-citation": "brain-citation-mcp",
    "rtime-assistant-runtime": "rtime-runtime-mcp",
    "rtime-hub-connector": "rtime-hub-mcp",
    "rtime-context": "rtime-context-mcp",
    "rtime-profile": "rtime-profile-mcp",
    "rtime-automation": "rtime-automation-mcp",
    "rtime-review": "rtime-review-mcp",
    "rtime-agent-control": "rtime-agent-control-mcp",
    "rtime-library-gateway": "rtime-library-gateway-mcp",
}
server_names = {
    "brain-docpack": "brain-docpack",
    "brain-library": "brain-library",
    "brain-citation": "brain-citation",
    "rtime-assistant-runtime": "rtime-assistant-runtime",
    "rtime-hub-connector": "rtime-hub-connector",
    "rtime-context": "rtime-context",
    "rtime-profile": "rtime-profile",
    "rtime-automation": "rtime-automation",
    "rtime-review": "rtime-review",
    "rtime-agent-control": "rtime-agent-control",
    "rtime-library-gateway": "rtime-library-gateway",
}


def path_check(path: Path, requested: bool = True) -> dict[str, object]:
    exists = path.exists()
    return {
        "requested": requested,
        "path": str(path),
        "exists": exists,
        "ok": (not requested) or exists,
    }


def cli_import_check(tool: str) -> dict[str, object]:
    module = module_roots[tool]
    result: dict[str, object] = {
        "requested": install_cli,
        "python": python_bin,
        "module": module,
        "ok": True,
    }
    if not install_cli:
        return result
    command = [
        python_bin,
        "-c",
        (
            "import importlib.util, sys; "
            "sys.exit(0 if importlib.util.find_spec(sys.argv[1]) else 1)"
        ),
        module,
    ]
    env = dict(os.environ)
    package_src = str(repo_root / packages[tool] / "src")
    env["PYTHONPATH"] = (
        package_src
        if not env.get("PYTHONPATH")
        else package_src + os.pathsep + env["PYTHONPATH"]
    )
    try:
        completed = subprocess.run(
            command,
            text=True,
            capture_output=True,
            check=False,
            timeout=15,
            env=env,
        )
    except Exception as exc:  # pragma: no cover - defensive for missing Python.
        result["ok"] = False
        result["error"] = str(exc)
        return result
    result["returncode"] = completed.returncode
    result["ok"] = completed.returncode == 0
    if completed.stderr:
        result["stderr_tail"] = completed.stderr[-400:]
    return result


def load_marketplace() -> tuple[set[str], str | None]:
    if not marketplace_path.exists():
        return set(), None
    try:
        data = json.loads(marketplace_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return set(), str(exc)
    names = {
        item.get("name")
        for item in data.get("plugins", [])
        if isinstance(item, dict) and item.get("name")
    }
    return names, None


def load_mcp_servers() -> tuple[set[str], str | None]:
    if not mcp_config_arg:
        return set(), None
    mcp_config_path = Path(mcp_config_arg).expanduser().resolve()
    if not mcp_config_path.exists():
        return set(), None
    try:
        data = json.loads(mcp_config_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return set(), str(exc)
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return set(), "mcpServers must be an object"
    return set(servers), None


marketplace_names, marketplace_error = load_marketplace()
mcp_server_names, mcp_error = load_mcp_servers()
mcp_requested = bool(mcp_config_arg)
mcp_config_path = Path(mcp_config_arg).expanduser().resolve() if mcp_config_arg else None

missing: list[dict[str, object]] = []
tool_results: list[dict[str, object]] = []


def record_missing(tool: str, surface: str, check: dict[str, object]) -> None:
    if check.get("ok", False):
        return
    if check.get("requested") is False:
        return
    missing.append(
        {
            "tool": tool,
            "surface": surface,
            "path": check.get("path"),
            "reason": check.get("error") or "missing",
        }
    )


for tool in tools:
    checks: dict[str, dict[str, object]] = {}
    checks["source_package"] = path_check(repo_root / packages[tool] / "pyproject.toml")
    checks["source_skill"] = path_check(repo_root / "skills" / tool / "SKILL.md")
    checks["source_plugin"] = path_check(
        repo_root / "plugins" / tool / ".codex-plugin" / "plugin.json"
    )
    checks["cli_import"] = cli_import_check(tool)
    checks["cli_wrapper"] = path_check(
        user_bin / cli_names[tool],
        requested=install_cli,
    )
    checks["mcp_wrapper"] = path_check(
        user_bin / mcp_cli_names[tool],
        requested=install_cli,
    )
    checks["codex_skill"] = path_check(
        codex_home / "skills" / tool / "SKILL.md",
        requested=sync_codex_skill,
    )
    checks["claude_skill"] = path_check(
        claude_home / "skills" / tool / "SKILL.md",
        requested=sync_claude_skill,
    )
    checks["plugin_source"] = path_check(
        plugin_home / tool / ".codex-plugin" / "plugin.json",
        requested=sync_codex_plugin,
    )
    checks["plugin_mcp"] = path_check(
        plugin_home / tool / ".mcp.json",
        requested=sync_codex_plugin,
    )

    marketplace_check: dict[str, object] = {
        "requested": write_codex_marketplace,
        "path": str(marketplace_path),
        "exists": marketplace_path.exists(),
        "entry_exists": None,
        "ok": True,
    }
    if write_codex_marketplace:
        marketplace_check["entry_exists"] = tool in marketplace_names
        marketplace_check["ok"] = (
            marketplace_path.exists()
            and marketplace_error is None
            and bool(marketplace_check["entry_exists"])
        )
        if marketplace_error:
            marketplace_check["error"] = marketplace_error
    checks["marketplace_entry"] = marketplace_check

    mcp_check: dict[str, object] = {
        "requested": mcp_requested,
        "path": str(mcp_config_path) if mcp_config_path else None,
        "exists": bool(mcp_config_path and mcp_config_path.exists()),
        "server": server_names[tool],
        "server_exists": None,
        "ok": True,
    }
    if mcp_requested:
        mcp_check["server_exists"] = server_names[tool] in mcp_server_names
        mcp_check["ok"] = (
            bool(mcp_config_path and mcp_config_path.exists())
            and mcp_error is None
            and bool(mcp_check["server_exists"])
        )
        if mcp_error:
            mcp_check["error"] = mcp_error
    checks["mcp_config"] = mcp_check

    for surface, check in checks.items():
        record_missing(tool, surface, check)
    tool_results.append(
        {
            "tool": tool,
            "ok": all(check.get("ok", False) for check in checks.values()),
            "checks": checks,
        }
    )

summary = {
    "ok": not missing,
    "mode": "check-installed",
    "profile": profile,
    "repo_root": str(repo_root),
    "python": python_bin,
    "bin_dir": str(user_bin),
    "surfaces": {
        "cli": install_cli,
        "codex_skill": sync_codex_skill,
        "claude_skill": sync_claude_skill,
        "codex_plugin": sync_codex_plugin,
        "codex_marketplace": write_codex_marketplace,
        "mcp_config": mcp_requested,
    },
    "tools": tool_results,
    "missing": missing,
}
print(json.dumps(summary, ensure_ascii=False, indent=2))
sys.exit(0 if summary["ok"] else 1)
PY
}

print_mcp_snippet() {
  local tool="$1"
  local package_dir
  local module
  local server_name
  package_dir="$(tool_package_dir "$tool")"
  module="$(tool_mcp_module "$tool")"
  server_name="$(tool_mcp_name "$tool")"

  log ""
  log "Standalone MCP client snippet for $tool:"
  if [ "$tool" = "brain-library" ] || [ "$tool" = "brain-citation" ] || [ "$tool" = "rtime-profile" ]; then
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src",
        "BRAIN_ROOT": "$(default_brain_root)"
      }
    }
  }
}
EOF
  elif [ "$tool" = "rtime-hub-connector" ]; then
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src",
        "RTIME_HUB_ROOT": "$(default_hub_root)"
      }
    }
  }
}
EOF
  elif [ "$tool" = "rtime-context" ]; then
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src",
        "BRAIN_ROOT": "$(default_brain_root)",
        "RTIME_HUB_ROOT": "$(default_hub_root)"
      }
    }
  }
}
EOF
  elif [ "$tool" = "rtime-automation" ]; then
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src",
        "RTIME_REMINDERS_PATH": "$(default_reminders_path)"
      }
    }
  }
}
EOF
  elif [ "$tool" = "rtime-agent-control" ] || [ "$tool" = "rtime-library-gateway" ]; then
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src",
        "BRAIN_ROOT": "$(default_brain_root)",
        "RTIME_HUB_ROOT": "$(default_hub_root)",
        "RTIME_REMINDERS_PATH": "$(default_reminders_path)"
      }
    }
  }
}
EOF
  else
    cat <<EOF
{
  "mcpServers": {
    "$server_name": {
      "command": "$PYTHON_BIN",
      "args": ["-m", "$module"],
      "env": {
        "RTIME_ASSISTANT_ROOT": "$REPO_ROOT",
        "PYTHONPATH": "$REPO_ROOT/$package_dir/src"
      }
    }
  }
}
EOF
  fi
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
    --tool)
      shift
      REQUESTED_TOOLS="${REQUESTED_TOOLS:+$REQUESTED_TOOLS }${1:-}"
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
    --write-mcp-config)
      shift
      WRITE_MCP_CONFIG="${1:-}"
      ;;
    --check-installed)
      CHECK_INSTALLED=1
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
    --no-mcp-snippets)
      PRINT_MCP_SNIPPETS=0
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

TOOLS="$(normalize_requested_tools)"
for tool in $TOOLS; do
  validate_tool_source "$tool"
done

CODEX_HOME="$(resolve_path "$CODEX_HOME")"
CLAUDE_HOME="$(resolve_path "$CLAUDE_HOME")"
PLUGIN_HOME="$(resolve_path "$PLUGIN_HOME")"
MARKETPLACE_PATH="$(resolve_path "$MARKETPLACE_PATH")"
USER_BIN="$(resolve_path "$USER_BIN")"
if [ -n "$WRITE_MCP_CONFIG" ]; then
  WRITE_MCP_CONFIG="$(resolve_path "$WRITE_MCP_CONFIG")"
fi

if [ "$CHECK_INSTALLED" -eq 1 ]; then
  check_installed $TOOLS
  exit $?
fi

log "rtime tooling install"
log "- mode: $([ "$APPLY" -eq 1 ] && printf apply || printf dry-run)"
log "- profile: $PROFILE"
log "- repo_root: $REPO_ROOT"
log "- tools: $TOOLS"
log "- codex_home: $CODEX_HOME"
log "- claude_home: $CLAUDE_HOME"
log "- plugin_home: $PLUGIN_HOME"
log "- marketplace: $MARKETPLACE_PATH"
log "- bin_dir: $USER_BIN"
if [ -n "$WRITE_MCP_CONFIG" ]; then
  log "- mcp_config: $WRITE_MCP_CONFIG"
fi

for tool in $TOOLS; do
  package_dir="$(tool_package_dir "$tool")"
  log ""
  log "== $tool =="
  if [ "$INSTALL_CLI" -eq 1 ]; then
    install_python_package "$REPO_ROOT/$package_dir"
    write_cli_wrappers "$tool" "$package_dir"
  fi
  if [ "$SYNC_CODEX_SKILL" -eq 1 ]; then
    copy_dir "$REPO_ROOT/skills/$tool" "$CODEX_HOME/skills/$tool"
  fi
  if [ "$SYNC_CLAUDE_SKILL" -eq 1 ]; then
    copy_dir "$REPO_ROOT/skills/$tool" "$CLAUDE_HOME/skills/$tool"
  fi
  if [ "$SYNC_CODEX_PLUGIN" -eq 1 ]; then
    copy_dir "$REPO_ROOT/plugins/$tool" "$PLUGIN_HOME/$tool"
  fi
done

if [ "$WRITE_CODEX_MARKETPLACE" -eq 1 ]; then
  write_marketplace "$MARKETPLACE_PATH" $TOOLS
fi

if [ -n "$WRITE_MCP_CONFIG" ]; then
  write_mcp_config "$WRITE_MCP_CONFIG" $TOOLS
fi

if [ "$PRINT_MCP_SNIPPETS" -eq 1 ]; then
  for tool in $TOOLS; do
    print_mcp_snippet "$tool"
  done
fi

log ""
log "Validation hints:"
for tool in $TOOLS; do
  if [ "$INSTALL_CLI" -eq 1 ]; then
    cli_name="$(tool_cli_name "$tool")"
    command_hint="$(tool_cli_command "$tool")"
    command_args="${command_hint#"$cli_name"}"
    log "  $USER_BIN/$cli_name$command_args"
  else
    log "  $(tool_cli_command "$tool")"
  fi
done
if [ "$INSTALL_CLI" -eq 1 ]; then
  for tool in $TOOLS; do
    log "  printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\\n' | $USER_BIN/$(tool_mcp_cli_name "$tool")"
  done
  case ":$PATH:" in
    *":$USER_BIN:"*) ;;
    *) log "  note: $USER_BIN is not on the current PATH; use the absolute wrapper path in non-interactive runners." ;;
  esac
else
  log "  printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/list\"}\\n' | <tool>-mcp"
fi
