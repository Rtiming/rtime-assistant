# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only agent control-plane CLI for rtime-assistant tooling."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_PROFILE = "mac"
DEFAULT_TOOLS = (
    "brain-docpack",
    "brain-library",
    "brain-citation",
    "rtime-assistant-runtime",
    "rtime-hub-connector",
    "rtime-context",
    "rtime-profile",
    "rtime-automation",
    "rtime-review",
    "rtime-agent-control",
    "rtime-library-gateway",
)

JsonObject = dict[str, Any]


@dataclass(frozen=True)
class ToolSpec:
    name: str
    package: str
    module: str
    cli_name: str
    mcp_cli_name: str
    mcp_module: str
    server_name: str


TOOL_SPECS: dict[str, ToolSpec] = {
    "brain-docpack": ToolSpec(
        "brain-docpack",
        "packages/brain-docpack",
        "brain_docpack",
        "brain-docpack",
        "brain-docpack-mcp",
        "brain_docpack.mcp_server",
        "brain-docpack",
    ),
    "brain-library": ToolSpec(
        "brain-library",
        "packages/brain-library",
        "brain_library",
        "brain-library",
        "brain-library-mcp",
        "brain_library.mcp_server",
        "brain-library",
    ),
    "brain-citation": ToolSpec(
        "brain-citation",
        "packages/brain-citation",
        "brain_citation",
        "brain-citation",
        "brain-citation-mcp",
        "brain_citation.mcp_server",
        "brain-citation",
    ),
    "rtime-assistant-runtime": ToolSpec(
        "rtime-assistant-runtime",
        "packages/rtime-assistant-runtime",
        "rtime_assistant_runtime",
        "rtime-runtime",
        "rtime-runtime-mcp",
        "rtime_assistant_runtime.mcp_server",
        "rtime-assistant-runtime",
    ),
    "rtime-hub-connector": ToolSpec(
        "rtime-hub-connector",
        "packages/rtime-hub-connector",
        "rtime_hub_connector",
        "rtime-hub-connector",
        "rtime-hub-mcp",
        "rtime_hub_connector.mcp_server",
        "rtime-hub-connector",
    ),
    "rtime-context": ToolSpec(
        "rtime-context",
        "packages/rtime-context",
        "rtime_context",
        "rtime-context",
        "rtime-context-mcp",
        "rtime_context.mcp_server",
        "rtime-context",
    ),
    "rtime-profile": ToolSpec(
        "rtime-profile",
        "packages/rtime-profile",
        "rtime_profile",
        "rtime-profile",
        "rtime-profile-mcp",
        "rtime_profile.mcp_server",
        "rtime-profile",
    ),
    "rtime-automation": ToolSpec(
        "rtime-automation",
        "packages/rtime-automation",
        "rtime_automation",
        "rtime-automation",
        "rtime-automation-mcp",
        "rtime_automation.mcp_server",
        "rtime-automation",
    ),
    "rtime-review": ToolSpec(
        "rtime-review",
        "packages/rtime-review",
        "rtime_review",
        "rtime-review",
        "rtime-review-mcp",
        "rtime_review.mcp_server",
        "rtime-review",
    ),
    "rtime-agent-control": ToolSpec(
        "rtime-agent-control",
        "packages/rtime-agent-control",
        "rtime_agent_control",
        "rtime-agent-control",
        "rtime-agent-control-mcp",
        "rtime_agent_control.mcp_server",
        "rtime-agent-control",
    ),
    "rtime-library-gateway": ToolSpec(
        "rtime-library-gateway",
        "packages/rtime-library-gateway",
        "rtime_library_gateway",
        "rtime-library-gateway",
        "rtime-library-gateway-mcp",
        "rtime_library_gateway.mcp_server",
        "rtime-library-gateway",
    ),
}


def _json_print(data: JsonObject) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _candidate_repo_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_ASSISTANT_ROOT")
    if env_root:
        roots.append(Path(env_root))
    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    roots.extend([PACKAGE_ROOT, *PACKAGE_ROOT.parents])
    return roots


def find_repo_root() -> Path:
    for root in _candidate_repo_roots():
        if (
            (root / "AGENTS.md").is_file()
            and (root / "module-submit.json").is_file()
            and (root / "docs" / "tooling-packaging.md").is_file()
        ):
            return root.resolve()
    raise RuntimeError("cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT")


def _default_repo_root(raw: Path | None = None) -> Path:
    if raw:
        return raw.expanduser().resolve()
    return find_repo_root()


def _default_brain_root(profile: str) -> str:
    raw = os.environ.get("BRAIN_ROOT") or os.environ.get("RTIME_BRAIN_ROOT")
    if raw:
        return str(Path(raw).expanduser())
    return "/mnt/brain"


def _default_hub_root(profile: str) -> str:
    raw = os.environ.get("RTIME_HUB_ROOT")
    if raw:
        return str(Path(raw).expanduser())
    if profile == "orangepi":
        return str(Path.home() / "rtime-hub")
    return str(Path.home() / "rtime-hub")


def _default_reminders_path(profile: str) -> str:
    raw = os.environ.get("RTIME_REMINDERS_PATH")
    if raw:
        return str(Path(raw).expanduser())
    return "/mnt/brain/_system/reminders.jsonl"


def _default_run_log_path() -> Path:
    raw = os.environ.get("RTIME_ASSISTANT_RUN_LOG")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.local/state/rtime-assistant/run-log.jsonl").expanduser().resolve()


def _read_json_object(path: Path) -> tuple[JsonObject | None, str | None]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, "file does not exist"
    except json.JSONDecodeError as exc:
        return None, f"invalid JSON: {exc.msg}"
    if not isinstance(loaded, dict):
        return None, "top-level JSON value must be an object"
    return loaded, None


def _file_status(path: Path) -> JsonObject:
    return {
        "path": str(path),
        "exists": path.exists(),
        "is_file": path.is_file() if path.exists() else False,
        "is_dir": path.is_dir() if path.exists() else False,
    }


def _short_hash(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _tool_names(raw: Iterable[str] | None) -> list[str]:
    names = list(raw or ["rtime-agent-control"])
    unknown = [name for name in names if name not in TOOL_SPECS]
    if unknown:
        raise ValueError(f"unknown tool(s): {', '.join(sorted(unknown))}")
    return names


def tooling_status(repo: Path) -> JsonObject:
    tools: list[JsonObject] = []
    tests_root = repo / "tests"
    for name in DEFAULT_TOOLS:
        spec = TOOL_SPECS[name]
        package = repo / spec.package
        skill = repo / "skills" / name
        plugin = repo / "plugins" / name
        tests = sorted(tests_root.glob(f"test_{spec.module}*.py"))
        tools.append(
            {
                "tool": name,
                "package_exists": (package / "pyproject.toml").is_file(),
                "skill_exists": (skill / "SKILL.md").is_file(),
                "plugin_exists": (plugin / ".codex-plugin" / "plugin.json").is_file(),
                "mcp_config_exists": (plugin / ".mcp.json").is_file(),
                "mcp_wrapper_exists": (plugin / "scripts" / f"{spec.mcp_cli_name}.sh").is_file(),
                "test_file_count": len(tests),
            }
        )
    missing = [
        item["tool"]
        for item in tools
        if not (
            item["package_exists"]
            and item["skill_exists"]
            and item["plugin_exists"]
            and item["mcp_config_exists"]
            and item["mcp_wrapper_exists"]
        )
    ]
    return {"ok": not missing, "repo_root": str(repo), "tools": tools, "missing": missing}


def _module_entries(repo: Path) -> tuple[list[JsonObject], list[str]]:
    payload, error = _read_json_object(repo / "module-submit.json")
    if error:
        return [], [error]
    modules = payload.get("modules", []) if payload else []
    if not isinstance(modules, list):
        return [], ["module-submit.json field modules must be a list"]
    valid = [item for item in modules if isinstance(item, dict)]
    return valid, []


def _module_by_id(repo: Path, module_id: str) -> tuple[JsonObject | None, list[str]]:
    modules, errors = _module_entries(repo)
    for item in modules:
        if item.get("id") == module_id:
            return item, errors
    return None, [*errors, f"module not found: {module_id}"]


def _git_changed_paths(repo: Path) -> tuple[list[str], list[str]]:
    command = ["git", "status", "--short", "--untracked-files=all"]
    completed = subprocess.run(
        command,
        cwd=repo,
        text=True,
        capture_output=True,
        check=False,
        timeout=20,
    )
    if completed.returncode != 0:
        return [], [completed.stderr.strip() or "git status failed"]
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        text = line[3:].strip()
        if " -> " in text:
            text = text.split(" -> ", 1)[1].strip()
        if text:
            paths.append(text)
    return paths, []


def _path_matches_module(path: str, module_paths: Iterable[Any]) -> bool:
    normalized = path.strip("/")
    for raw in module_paths:
        if not isinstance(raw, str) or not raw:
            continue
        prefix = raw.strip("/")
        if normalized == prefix or normalized.startswith(prefix + "/"):
            return True
    return False


def validation_plan(repo: Path, *, module_id: str = "agent-control", changed: bool = False) -> JsonObject:
    modules, manifest_errors = _module_entries(repo)
    selected_ids: list[str] = []
    changed_paths: list[str] = []
    git_errors: list[str] = []
    if changed:
        changed_paths, git_errors = _git_changed_paths(repo)
        for item in modules:
            paths = item.get("paths", [])
            if isinstance(paths, list) and any(_path_matches_module(path, paths) for path in changed_paths):
                item_id = item.get("id")
                if isinstance(item_id, str):
                    selected_ids.append(item_id)
    else:
        selected_ids = [module_id]

    module_plans: list[JsonObject] = []
    missing_modules: list[str] = []
    for item_id in selected_ids:
        module, errors = _module_by_id(repo, item_id)
        if module is None:
            missing_modules.extend(errors)
            continue
        checks = module.get("checks", {})
        quick = checks.get("quick", []) if isinstance(checks, dict) else []
        module_plans.append(
            {
                "module_id": item_id,
                "title": module.get("title"),
                "kind": module.get("kind"),
                "paths": module.get("paths", []),
                "quick_checks": quick if isinstance(quick, list) else [],
            }
        )

    root_checks = [
        {"command": "git status --short"},
        {"command": "git diff --check"},
        {"command": "scripts/module-submit-check.py --changed --dry-run"},
        {"command": "scripts/audit-env.sh"},
    ]
    errors = [*manifest_errors, *git_errors, *missing_modules]
    return {
        "ok": not errors and bool(module_plans),
        "repo_root": str(repo),
        "mode": "changed" if changed else "module",
        "changed_paths": changed_paths,
        "modules": module_plans,
        "root_checks": root_checks,
        "executed": False,
        "write_enabled": False,
        "errors": errors,
    }


def _env_for_tool(repo: Path, spec: ToolSpec, profile: str) -> dict[str, str]:
    env = {
        "RTIME_ASSISTANT_ROOT": str(repo),
        "PYTHONPATH": str(repo / spec.package / "src"),
    }
    if spec.name in {"brain-library", "brain-citation", "rtime-profile"}:
        env["BRAIN_ROOT"] = _default_brain_root(profile)
    if spec.name == "rtime-hub-connector":
        env["RTIME_HUB_ROOT"] = _default_hub_root(profile)
    if spec.name == "rtime-context":
        env["BRAIN_ROOT"] = _default_brain_root(profile)
        env["RTIME_HUB_ROOT"] = _default_hub_root(profile)
    if spec.name == "rtime-automation":
        env["RTIME_REMINDERS_PATH"] = _default_reminders_path(profile)
    if spec.name == "rtime-agent-control":
        env["BRAIN_ROOT"] = _default_brain_root(profile)
        env["RTIME_HUB_ROOT"] = _default_hub_root(profile)
        env["RTIME_REMINDERS_PATH"] = _default_reminders_path(profile)
    if spec.name == "rtime-library-gateway":
        env["BRAIN_ROOT"] = _default_brain_root(profile)
        env["RTIME_HUB_ROOT"] = _default_hub_root(profile)
        env["RTIME_REMINDERS_PATH"] = _default_reminders_path(profile)
    return env


def render_mcp_config(
    repo: Path,
    *,
    profile: str = DEFAULT_PROFILE,
    python_bin: str | None = None,
    tools: Iterable[str] | None = None,
) -> JsonObject:
    selected = _tool_names(tools)
    python = python_bin or sys.executable
    config = {
        "mcpServers": {
            TOOL_SPECS[name].server_name: {
                "command": python,
                "args": ["-m", TOOL_SPECS[name].mcp_module],
                "env": _env_for_tool(repo, TOOL_SPECS[name], profile),
            }
            for name in selected
        }
    }
    return {
        "ok": True,
        "repo_root": str(repo),
        "profile": profile,
        "tools": selected,
        "mcp_config": config,
        "write_enabled": False,
        "privacy": {
            "contains_secret_values": False,
            "contains_secret_paths": False,
        },
    }


def context_plan(request: str, repo: Path) -> JsonObject:
    lowered = request.lower()
    groups: dict[str, list[str]] = {}
    keywords = {
        "runtime": ("feishu", "bridge", "runtime", "docker", "deploy", "log", "服务"),
        "tooling": ("mcp", "plugin", "skill", "tool", "agent", "工具", "配置"),
        "profile": ("persona", "model", "permission", "prompt", "assistant", "权限"),
        "validation": ("test", "check", "验证", "调试"),
        "docs": ("doc", "文档", "日志"),
    }
    for group, words in keywords.items():
        hits = [word for word in words if word in lowered or word in request]
        if hits:
            groups[group] = hits
    lanes = [
        {
            "lane": "Workspace",
            "level": "L1",
            "reason": "active repository is the authority for package, plugin, and module boundaries",
            "recommended_tools": ["agent.tooling", "agent.validation_plan"],
        },
        {
            "lane": "Runtime Evidence",
            "level": "L1",
            "reason": "request may require bridge/runtime diagnostics without service mutation",
            "recommended_tools": ["agent.runtime_snapshot", "runtime.doctor"],
        },
        {
            "lane": "Assistant Profile & Policy",
            "level": "L2",
            "reason": "MCP and agent control surfaces affect permission and tooling policy",
            "recommended_tools": ["profile.plan", "agent.config_render"],
        },
    ]
    return {
        "ok": True,
        "repo_root": str(repo),
        "request_hash": _short_hash(request),
        "request_length": len(request),
        "request_preview": request[:120],
        "task_signals": groups,
        "levels": ["L0", "L1", "L2"],
        "lanes": lanes,
        "excluded": [
            {
                "lane": "Sensitive",
                "reason": "agent-control planning does not require secret values or session stores",
            }
        ],
        "permissions": {
            "default": "read_only",
            "actions_require_confirmation": True,
            "write_enabled": False,
        },
    }


def runtime_snapshot(repo: Path, *, run_log: Path | None = None) -> JsonObject:
    run_log = run_log or _default_run_log_path()
    files = {
        "feishu_bridge_main": repo / "apps" / "feishu-bridge" / "main.py",
        "compose_prod": repo / "compose.prod.yml",
        "docker_prod_docs": repo / "docs" / "docker-production.md",
        "deployment_docs": repo / "docs" / "deployment.md",
        "runtime_assets_docs": repo / "docs" / "runtime-assets.md",
        "runtime_package": repo / "packages" / "rtime-assistant-runtime" / "pyproject.toml",
    }
    statuses = {name: _file_status(path) for name, path in files.items()}
    missing = [name for name, status in statuses.items() if not status["exists"]]
    return {
        "ok": not missing,
        "repo_root": str(repo),
        "checks": statuses,
        "run_log": {
            "path": str(run_log),
            "exists": run_log.exists(),
            "read": False,
        },
        "live_service_state_checked": False,
        "mutations_performed": False,
        "risks": [f"missing_{name}" for name in missing],
    }


def doctor(repo: Path | None = None) -> JsonObject:
    repo_error = ""
    try:
        repo_root = _default_repo_root(repo)
    except RuntimeError as exc:
        repo_root = None
        repo_error = str(exc)

    checks: JsonObject = {}
    if repo_root is not None:
        checks = {
            "package": _file_status(repo_root / "packages" / "rtime-agent-control" / "pyproject.toml"),
            "cli": _file_status(
                repo_root / "packages" / "rtime-agent-control" / "src" / "rtime_agent_control" / "cli.py"
            ),
            "mcp_server": _file_status(
                repo_root / "packages" / "rtime-agent-control" / "src" / "rtime_agent_control" / "mcp_server.py"
            ),
            "skill": _file_status(repo_root / "skills" / "rtime-agent-control" / "SKILL.md"),
            "plugin": _file_status(repo_root / "plugins" / "rtime-agent-control" / ".codex-plugin" / "plugin.json"),
            "plugin_mcp": _file_status(repo_root / "plugins" / "rtime-agent-control" / ".mcp.json"),
            "docs": _file_status(repo_root / "docs" / "agent-control-mcp.md"),
        }
        modules, module_errors = _module_entries(repo_root)
        module_ids = {item.get("id") for item in modules}
        checks["module_submit_entry"] = {
            "path": str(repo_root / "module-submit.json"),
            "exists": "agent-control" in module_ids,
            "errors": module_errors,
        }
        installer_text = (repo_root / "scripts" / "install-rtime-tooling.sh").read_text(
            encoding="utf-8",
            errors="ignore",
        )
        checks["installer_entry"] = {
            "path": str(repo_root / "scripts" / "install-rtime-tooling.sh"),
            "exists": "rtime-agent-control" in installer_text,
        }

    risks = [name for name, status in checks.items() if not bool(status.get("exists"))]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": bool(repo_root) and not risks,
        "repo_root": str(repo_root) if repo_root else None,
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
        "policy": {
            "permission_tier": "read_only",
            "writes_enabled": False,
            "reads_secret_values": False,
        },
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, help="Optional rtime-assistant repository root.")
    sub = parser.add_subparsers(dest="command", required=True)

    doctor_parser = sub.add_parser("doctor", help="Check agent-control source surfaces.")
    doctor_parser.add_argument("--repo-root", type=Path)

    tooling_parser = sub.add_parser("tooling", help="Summarize repository-owned tool surfaces.")
    tooling_parser.add_argument("--repo-root", type=Path)

    config_parser = sub.add_parser("config-render", help="Render standalone MCP config JSON without writing it.")
    config_parser.add_argument("--repo-root", type=Path)
    config_parser.add_argument("--profile", choices=["mac", "orangepi"], default=DEFAULT_PROFILE)
    config_parser.add_argument("--python", dest="python_bin", default=None)
    config_parser.add_argument("--tool", action="append", dest="tools")
    config_parser.add_argument("--all-tools", action="store_true")

    validation_parser = sub.add_parser("validation-plan", help="Plan module validation checks without running them.")
    validation_parser.add_argument("--repo-root", type=Path)
    validation_parser.add_argument("--module", default="agent-control")
    validation_parser.add_argument("--changed", action="store_true")

    context_parser = sub.add_parser("context-plan", help="Plan context lanes for an agent-control request.")
    context_parser.add_argument("request")
    context_parser.add_argument("--repo-root", type=Path)

    runtime_parser = sub.add_parser("runtime-snapshot", help="Read-only runtime source snapshot.")
    runtime_parser.add_argument("--repo-root", type=Path)
    runtime_parser.add_argument("--run-log", type=Path)

    return parser


def _repo_from_args(args: argparse.Namespace) -> Path:
    return _default_repo_root(args.repo_root)


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    command = args.command
    if command == "doctor":
        result = doctor(args.repo_root)
    elif command == "tooling":
        result = tooling_status(_repo_from_args(args))
    elif command == "config-render":
        tools = DEFAULT_TOOLS if args.all_tools else args.tools
        result = render_mcp_config(
            _repo_from_args(args),
            profile=args.profile,
            python_bin=args.python_bin,
            tools=tools,
        )
    elif command == "validation-plan":
        result = validation_plan(_repo_from_args(args), module_id=args.module, changed=args.changed)
    elif command == "context-plan":
        result = context_plan(args.request, _repo_from_args(args))
    elif command == "runtime-snapshot":
        result = runtime_snapshot(_repo_from_args(args), run_log=args.run_log)
    else:  # pragma: no cover - argparse prevents this.
        raise AssertionError(command)
    _json_print(result)
    return 0 if result.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
