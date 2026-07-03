# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only rtime-assistant runtime diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
SENSITIVE_KEY_PARTS = (
    "secret",
    "token",
    "password",
    "credential",
    "authorization",
    "api_key",
    "apikey",
    "app_secret",
    "identity",
    "id_card",
    "address",
)


def _candidate_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_ASSISTANT_ROOT")
    if env_root:
        roots.append(Path(env_root))
    cwd = Path.cwd()
    roots.extend([cwd, *cwd.parents])
    roots.extend([PACKAGE_ROOT, *PACKAGE_ROOT.parents])
    return roots


def find_repo_root() -> Path:
    for root in _candidate_roots():
        if (
            (root / "apps" / "feishu-bridge" / "main.py").is_file()
            and (root / "docs" / "logging-and-audit.md").is_file()
            and (root / "deploy" / "systemd" / "user").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def redact(value: Any) -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            result[key_text] = "[REDACTED]" if _is_sensitive_key(key_text) else redact(item)
        return result
    if isinstance(value, list):
        return [redact(item) for item in value]
    if isinstance(value, tuple):
        return [redact(item) for item in value]
    return value


def _json_print(data: dict[str, Any]) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _default_run_log_path() -> Path:
    raw = os.environ.get("RTIME_ASSISTANT_RUN_LOG")
    if raw:
        return Path(raw).expanduser().resolve()
    return Path("~/.local/state/rtime-assistant/run-log.jsonl").expanduser().resolve()


def _read_jsonl(path: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    records: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    if not path.exists():
        return records, [{"line": None, "error": "file does not exist"}]
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                loaded = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append({"line": line_no, "error": exc.msg})
                continue
            if not isinstance(loaded, dict):
                errors.append({"line": line_no, "error": "record must be an object"})
                continue
            records.append(redact(loaded))
    return records, errors


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def _read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    return path.read_text(encoding="utf-8")


def _env_keys(path: Path) -> set[str]:
    keys: set[str] = set()
    if not path.exists() or not path.is_file():
        return keys
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").strip()
        key = line.split("=", 1)[0].strip()
        if key:
            keys.add(key)
    return keys


def _file_metadata(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "is_file": path.is_file() if exists else False,
        "is_dir": path.is_dir() if exists else False,
    }


def _append_missing_check(
    risks: list[str],
    prefix: str,
    checks: dict[str, bool],
    *,
    required: Iterable[str] | None = None,
) -> None:
    names = required or checks.keys()
    for name in names:
        if not checks.get(name, False):
            risks.append(f"{prefix}_{name}")


def check_docker_prod(repo: Path, env_file: Path | None = None) -> dict[str, Any]:
    """Inspect production Docker deployment files without running Docker."""

    compose_path = repo / "compose.prod.yml"
    dockerignore_path = repo / ".dockerignore"
    dockerfile_path = repo / "docker" / "feishu-bridge.Dockerfile"
    helper_path = repo / "scripts" / "docker-prod-check.sh"
    bridge_main_path = repo / "apps" / "feishu-bridge" / "main.py"
    simulation_path = repo / "apps" / "feishu-bridge" / "simulate_message_burst.py"
    env_template_path = repo / "deploy" / "env" / "feishu-bridge.prod.env.example"
    systemd_path = repo / "deploy" / "systemd" / "user" / "rtime-assistant-docker.service"
    docker_docs_path = repo / "docs" / "docker-production.md"
    project_map_path = repo / "docs" / "project-map.zh-CN.md"

    compose_text = _read_text_if_exists(compose_path)
    dockerignore_text = _read_text_if_exists(dockerignore_path)
    dockerfile_text = _read_text_if_exists(dockerfile_path)
    helper_text = _read_text_if_exists(helper_path)
    bridge_main_text = _read_text_if_exists(bridge_main_path)
    simulation_text = _read_text_if_exists(simulation_path)
    env_template_text = _read_text_if_exists(env_template_path)
    systemd_text = _read_text_if_exists(systemd_path)
    docker_docs_text = _read_text_if_exists(docker_docs_path)
    project_map_text = _read_text_if_exists(project_map_path)

    required_env_keys = {
        "ALLOWED_USERS",
        "BRAIN_ROOT",
        "CALLBACK_BIND",
        "CLAUDE_CLI_PATH",
        "CLAUDE_CONFIG_JSON",
        "CLAUDE_KIMI_KEYFILE",
        "CLAUDE_STATE_ROOT",
        "FEISHU_CONFIG_JSON",
        "INSTALL_CLAUDE_CODE",
        "MESSAGE_DEBOUNCE_MAX_CHARS",
        "MESSAGE_DEBOUNCE_MAX_MESSAGES",
        "MESSAGE_DEBOUNCE_SECONDS",
        "RTIME_ASSISTANT_ROOT",
        "RTIME_ASSISTANT_STATE_DIR",
    }
    env_template_keys = _env_keys(env_template_path)

    compose_checks = {
        "has_healthcheck": "healthcheck:" in compose_text,
        "has_healthz": "/healthz" in compose_text,
        "has_arm64_platform_default": "linux/arm64" in compose_text,
        "has_allowed_users_gate": "ALLOWED_USERS:" in compose_text and "ALLOWED_USERS" in env_template_keys,
        "mounts_brain_root": "BRAIN_ROOT" in compose_text and "/mnt/brain" in compose_text,
        "mounts_feishu_config_read_only": "FEISHU_CONFIG_JSON" in compose_text and "read_only: true" in compose_text,
        "mounts_claude_state": "CLAUDE_STATE_ROOT" in compose_text and "/var/lib/rtime-assistant/.claude" in compose_text,
        "mounts_claude_config": "CLAUDE_CONFIG_JSON" in compose_text and "/var/lib/rtime-assistant/.claude.json" in compose_text,
        "mounts_claude_kimi_key": "CLAUDE_KIMI_KEYFILE" in compose_text
        and "/run/secrets/rtime-assistant/claude-kimi-key" in compose_text,
        "uses_claude_kimi_default": "/usr/local/bin/claude-kimi" in compose_text,
        "has_message_debounce_config": "MESSAGE_DEBOUNCE_SECONDS" in compose_text
        and "MESSAGE_DEBOUNCE_MAX_MESSAGES" in compose_text
        and "MESSAGE_DEBOUNCE_MAX_CHARS" in compose_text,
        "uses_external_run_log": "RTIME_ASSISTANT_RUN_LOG" in compose_text,
        "uses_image_tag": "RTIME_ASSISTANT_IMAGE_TAG" in compose_text,
        "uses_runtime_target": "target: runtime" in compose_text,
        "has_optional_container_proxy": "RTIME_ASSISTANT_HTTP_PROXY" in compose_text
        and "RTIME_ASSISTANT_HTTPS_PROXY" in compose_text
        and "RTIME_ASSISTANT_NO_PROXY" in compose_text,
    }
    dockerignore_checks = {
        "excludes_git": ".git" in dockerignore_text,
        "excludes_env_files": ".env" in dockerignore_text and ".env.*" in dockerignore_text,
        "allows_env_example": "!.env.example" in dockerignore_text,
        "excludes_secret_dirs": "secrets/" in dockerignore_text and "credentials/" in dockerignore_text,
        "excludes_runtime_state": "work/" in dockerignore_text
        and "logs/" in dockerignore_text
        and "state/" in dockerignore_text
        and "sessions/" in dockerignore_text,
        "excludes_python_caches": "__pycache__/" in dockerignore_text
        and "*.pyc" in dockerignore_text
        and ".pytest_cache/" in dockerignore_text,
        "excludes_node_artifacts": "node_modules/" in dockerignore_text
        and "dist/" in dockerignore_text
        and "build/" in dockerignore_text,
    }
    dockerfile_checks = {
        "has_runtime_stage": "FROM bridge-base AS runtime" in dockerfile_text,
        "installs_claude_code": "@anthropic-ai/claude-code" in dockerfile_text,
        "installs_ripgrep": "ripgrep" in dockerfile_text,
        "installs_procps": "procps" in dockerfile_text,
        "defines_claude_cli_path": "CLAUDE_CLI_PATH" in dockerfile_text,
        "copies_claude_kimi_wrapper": "deploy/bin/claude-kimi" in dockerfile_text
        and "/usr/local/bin/claude-kimi" in dockerfile_text,
        "copies_rtime_web_fetch": "deploy/bin/rtime-web-fetch" in dockerfile_text
        and "/usr/local/bin/rtime-web-fetch" in dockerfile_text,
        "copies_rtime_qq_code": "deploy/bin/rtime-qq-code" in dockerfile_text
        and "/usr/local/bin/rtime-qq-code" in dockerfile_text,
        "copies_bridge_source": "COPY apps/feishu-bridge/ ./" in dockerfile_text,
        "does_not_copy_repo_root_into_runtime": "COPY . ." not in dockerfile_text.split("FROM bridge-base AS runtime", 1)[-1],
    }
    helper_clears_proxy_build_args = (
        "--build-arg HTTP_PROXY=" in helper_text
        and "--build-arg http_proxy=" in helper_text
    ) or (
        "USE_HOST_PROXY=0" in helper_text
        and "docker_build_args()" in helper_text
        and '"$key="' in helper_text
    )
    helper_checks = {
        "is_executable": helper_path.exists() and os.access(helper_path, os.X_OK),
        "has_config_action": "--config" in helper_text,
        "has_build_action": "--build" in helper_text,
        "has_smoke_action": "--smoke" in helper_text,
        "has_up_action": "--up" in helper_text,
        "has_ps_action": "--ps" in helper_text,
        "has_logs_action": "--logs" in helper_text,
        "has_down_action": "--down" in helper_text,
        "has_dry_run": "--dry-run" in helper_text,
        "has_one_shot_smoke": "run --rm --no-deps" in helper_text,
        "clears_proxy_build_args": helper_clears_proxy_build_args,
        "supports_host_proxy_build_args": "--use-host-proxy" in helper_text
        and "USE_HOST_PROXY=1" in helper_text,
    }
    bridge_checks = {
        "healthz_reports_debounce_counts": "debounce_queue_count" in bridge_main_text
        and "debounce_pending_count" in bridge_main_text
        and "debounce_active_count" in bridge_main_text,
        "has_simulation_entry": simulation_path.exists(),
        "simulation_uses_runner_monkeypatch": "FakeModelRunner" in simulation_text
        and "main.run_claude = runner" in simulation_text
        and "main.handle_message_async(event)" in simulation_text,
        "simulation_reports_process_count": "process_count" in simulation_text,
        "simulation_has_access_override_flag": "--respect-access" in simulation_text,
        "simulation_access_override_is_local": "main.config.ALLOWED_USERS" in simulation_text
        and "main.config.ALLOWED_CHATS" in simulation_text,
    }
    env_template_checks = {
        "has_required_keys": required_env_keys <= env_template_keys,
        "pins_local_callback_bind": "CALLBACK_BIND=127.0.0.1" in env_template_text,
        "keeps_permission_default": "PERMISSION_MODE=default" in env_template_text,
        "documents_message_debounce": "MESSAGE_DEBOUNCE_SECONDS" in env_template_text
        and "MESSAGE_DEBOUNCE_MAX_MESSAGES" in env_template_text
        and "MESSAGE_DEBOUNCE_MAX_CHARS" in env_template_text,
        "documents_no_commit": "Do not commit" in env_template_text,
    }
    systemd_checks = {
        "uses_helper": "scripts/docker-prod-check.sh" in systemd_text,
        "has_up": "--up" in systemd_text,
        "has_down": "--down" in systemd_text,
        "has_env_file": "--env-file" in systemd_text,
        "is_user_service": "WantedBy=default.target" in systemd_text,
    }
    docs_checks = {
        "has_cutover": "## Cutover" in docker_docs_text,
        "has_rollback": "## Rollback" in docker_docs_text,
        "has_security_rules": "## Security Rules" in docker_docs_text,
        "has_healthz": "/healthz" in docker_docs_text,
        "has_mac_to_server_loop": "Mac-To-Server" in docker_docs_text
        or "Mac-to-server" in docker_docs_text,
        "has_simulation_entry": "simulate_message_burst.py" in docker_docs_text
        or "simulation entry" in docker_docs_text,
        "has_env_backup": "backup" in docker_docs_text and "chmod 600" in docker_docs_text,
        "has_project_map_docker": "Docker" in project_map_text,
    }

    risks: list[str] = []
    for name, path in {
        "compose_prod_missing": compose_path,
        "dockerignore_missing": dockerignore_path,
        "dockerfile_missing": dockerfile_path,
        "helper_missing": helper_path,
        "bridge_main_missing": bridge_main_path,
        "simulation_entry_missing": simulation_path,
        "env_template_missing": env_template_path,
        "systemd_template_missing": systemd_path,
        "docker_docs_missing": docker_docs_path,
        "project_map_missing": project_map_path,
    }.items():
        if not path.exists():
            risks.append(name)

    _append_missing_check(risks, "compose", compose_checks)
    _append_missing_check(risks, "dockerignore", dockerignore_checks)
    _append_missing_check(risks, "dockerfile", dockerfile_checks)
    _append_missing_check(risks, "helper", helper_checks)
    _append_missing_check(risks, "bridge", bridge_checks)
    _append_missing_check(risks, "env_template", env_template_checks)
    _append_missing_check(risks, "systemd", systemd_checks)
    _append_missing_check(risks, "docs", docs_checks)

    env_file_result: dict[str, Any] = {"requested": env_file is not None}
    if env_file is not None:
        env_file_exists = env_file.exists() and env_file.is_file()
        is_template_env_file = env_file_exists and env_file.resolve() == env_template_path.resolve()
        keys = _env_keys(env_file)
        missing_keys = sorted(required_env_keys - keys)
        mode_octal = None
        permissions_too_open = False
        if env_file_exists:
            mode = env_file.stat().st_mode & 0o777
            mode_octal = oct(mode)
            permissions_too_open = bool(mode & 0o077) and not is_template_env_file
        env_file_result.update(
            {
                "path": str(env_file),
                "exists": env_file_exists,
                "is_template": is_template_env_file,
                "keys_present": sorted(keys),
                "missing_keys": missing_keys,
                "mode_octal": mode_octal,
                "permissions_too_open": permissions_too_open,
            }
        )
        if not env_file_exists:
            risks.append("env_file_missing")
        for key in missing_keys:
            risks.append(f"env_file_missing_{key}")
        if permissions_too_open:
            risks.append("env_file_permissions_too_open")

    return {
        "ok": not risks,
        "repo_root": str(repo),
        "compose": {**_file_metadata(compose_path), "checks": compose_checks},
        "dockerignore": {**_file_metadata(dockerignore_path), "checks": dockerignore_checks},
        "dockerfile": {**_file_metadata(dockerfile_path), "checks": dockerfile_checks},
        "helper": {**_file_metadata(helper_path), "checks": helper_checks},
        "bridge": {
            "main": _file_metadata(bridge_main_path),
            "simulation": _file_metadata(simulation_path),
            "checks": bridge_checks,
        },
        "env_template": {
            **_file_metadata(env_template_path),
            "keys_present": sorted(env_template_keys),
            "missing_keys": sorted(required_env_keys - env_template_keys),
            "checks": env_template_checks,
        },
        "systemd": {**_file_metadata(systemd_path), "checks": systemd_checks},
        "docs": {
            "docker_production": _file_metadata(docker_docs_path),
            "project_map": _file_metadata(project_map_path),
            "checks": docs_checks,
        },
        "env_file": env_file_result,
        "risks": sorted(risks),
    }


def summarize_run_log(path: Path) -> dict[str, Any]:
    records, errors = _read_jsonl(path)
    timestamps = [record.get("timestamp") for record in records if record.get("timestamp")]
    run_ids = {record.get("run_id") for record in records if record.get("run_id")}
    return {
        "ok": path.exists() and not errors,
        "path": str(path),
        "exists": path.exists(),
        "record_count": len(records),
        "run_count": len(run_ids),
        "malformed_count": len(errors),
        "errors": errors[:20],
        "events": _counter_dict(record.get("event") for record in records),
        "entries": _counter_dict(record.get("entry") for record in records),
        "statuses": _counter_dict(record.get("status") for record in records),
        "latest_timestamp": max(timestamps) if timestamps else None,
    }


def tail_run_log(path: Path, limit: int) -> dict[str, Any]:
    if limit < 1:
        return {
            "ok": False,
            "path": str(path),
            "records": [],
            "errors": [{"line": None, "error": "--limit must be >= 1"}],
        }
    records, errors = _read_jsonl(path)
    return {
        "ok": path.exists() and not errors,
        "path": str(path),
        "limit": limit,
        "record_count": len(records),
        "malformed_count": len(errors),
        "errors": errors[:20],
        "records": records[-limit:],
    }


def check_templates(repo: Path) -> dict[str, Any]:
    template_dir = repo / "deploy" / "systemd" / "user"
    templates = []
    forbidden_env_keys = []
    for path in sorted(template_dir.glob("*.service")) + sorted(template_dir.glob("*.timer")):
        text = path.read_text(encoding="utf-8")
        has_exec_start = "ExecStart=" in text if path.suffix == ".service" else True
        has_install = "[Install]" in text
        requires_install = path.suffix == ".timer"
        secret_keys = []
        for line in text.splitlines():
            if not line.startswith("Environment="):
                continue
            key = line.removeprefix("Environment=").split("=", 1)[0].strip().strip('"')
            if _is_sensitive_key(key):
                secret_keys.append(key)
        if secret_keys:
            forbidden_env_keys.extend(f"{path.name}:{key}" for key in secret_keys)
        templates.append(
            {
                "path": str(path),
                "name": path.name,
                "exists": path.exists(),
                "has_exec_start": has_exec_start,
                "has_install": has_install,
                "requires_install": requires_install,
                "forbidden_env_keys": secret_keys,
            }
        )
    expected = {
        "lark-bridge.service",
        "feishu-bridge-python.service",
        "reminder.service",
        "reminder.timer",
    }
    found = {Path(item["path"]).name for item in templates}
    missing = sorted(expected - found)
    return {
        "ok": not missing and not forbidden_env_keys and all(
            item["has_exec_start"] and (item["has_install"] or not item["requires_install"])
            for item in templates
        ),
        "template_dir": str(template_dir),
        "missing": missing,
        "forbidden_env_keys": forbidden_env_keys,
        "templates": templates,
    }


def doctor(repo: Path) -> dict[str, Any]:
    files = {
        "feishu_bridge_main": repo / "apps" / "feishu-bridge" / "main.py",
        # KEEP IN SYNC: packages/rtime-chat-runtime/src/rtime_chat_runtime/run_log.py
        # —— 该遥测原语 P5 已从 apps/feishu-bridge 抽到 rtime-chat-runtime 共用层;
        # 若再移动该文件,须同改此结构健康检查路径(见 docs/maintainability-standards.zh-CN.md §1.3)。
        "chat_runtime_run_log": repo / "packages" / "rtime-chat-runtime" / "src" / "rtime_chat_runtime" / "run_log.py",
        "runtime_docs": repo / "docs" / "logging-and-audit.md",
        "runtime_assets_docs": repo / "docs" / "runtime-assets.md",
        "deployment_docs": repo / "docs" / "deployment.md",
        "docker_prod_compose": repo / "compose.prod.yml",
        "docker_prod_helper": repo / "scripts" / "docker-prod-check.sh",
        "docker_prod_docs": repo / "docs" / "docker-production.md",
        "systemd_templates": repo / "deploy" / "systemd" / "user",
    }
    statuses = {
        name: "ok" if path.exists() else "missing"
        for name, path in files.items()
    }
    run_log_path = _default_run_log_path()
    template_result = check_templates(repo)
    docker_prod_result = check_docker_prod(repo)
    risks = [
        name for name, status in statuses.items() if status != "ok"
    ]
    if not template_result["ok"]:
        risks.append("template_check_failed")
    if not docker_prod_result["ok"]:
        risks.append("docker_prod_check_failed")
    if not run_log_path.exists():
        risks.append("run_log_not_found")
    return {
        "ok": not [risk for risk in risks if risk != "run_log_not_found"],
        "repo_root": str(repo),
        "checks": statuses,
        "run_log": {
            "path": str(run_log_path),
            "exists": run_log_path.exists(),
        },
        "templates": {
            "ok": template_result["ok"],
            "missing": template_result["missing"],
            "forbidden_env_keys": template_result["forbidden_env_keys"],
        },
        "docker_prod": {
            "ok": docker_prod_result["ok"],
            "risks": docker_prod_result["risks"],
        },
        "risks": risks,
    }


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-runtime",
        description="Read-only diagnostics for rtime-assistant runtime logs and templates.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        help="rtime-assistant repository root; defaults to auto-detect or RTIME_ASSISTANT_ROOT",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="Check runtime docs, bridge files, templates, and log path.")

    run_log = subparsers.add_parser("run-log", help="Inspect external runtime run logs.")
    run_log_sub = run_log.add_subparsers(dest="run_log_command", required=True)
    summary = run_log_sub.add_parser("summary", help="Summarize a JSONL run log.")
    summary.add_argument("path", nargs="?", type=Path, help="run-log JSONL path")
    tail = run_log_sub.add_parser("tail", help="Print the last redacted JSONL run-log records.")
    tail.add_argument("path", nargs="?", type=Path, help="run-log JSONL path")
    tail.add_argument("--limit", type=int, default=5)

    templates = subparsers.add_parser("templates", help="Check runtime service templates.")
    templates_sub = templates.add_subparsers(dest="templates_command", required=True)
    templates_sub.add_parser("check", help="Check service/timer templates without deploying.")

    docker_prod = subparsers.add_parser(
        "docker-prod",
        help="Inspect production Docker Compose deployment files without running Docker.",
    )
    docker_prod_sub = docker_prod.add_subparsers(dest="docker_prod_command", required=True)
    docker_prod_check = docker_prod_sub.add_parser(
        "check",
        help="Run a read-only production Docker deployment checklist.",
    )
    docker_prod_check.add_argument(
        "--env-file",
        type=Path,
        help="Optional production env file; only key names and file mode are inspected.",
    )

    subparsers.add_parser("mcp", help="Run the read-only runtime MCP stdio server.")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)

    try:
        repo = args.repo_root.resolve() if args.repo_root else find_repo_root()
    except RuntimeError as exc:
        _json_print({"ok": False, "error": str(exc)})
        return 2

    if args.command == "doctor":
        _json_print(doctor(repo))
        return 0

    if args.command == "run-log":
        path = args.path.expanduser().resolve() if args.path else _default_run_log_path()
        if args.run_log_command == "summary":
            result = summarize_run_log(path)
            _json_print(result)
            return 0 if result["ok"] or not result["exists"] else 1
        if args.run_log_command == "tail":
            result = tail_run_log(path, args.limit)
            _json_print(result)
            return 0 if result["ok"] or not result.get("exists", path.exists()) else 1

    if args.command == "templates" and args.templates_command == "check":
        result = check_templates(repo)
        _json_print(result)
        return 0 if result["ok"] else 1

    if args.command == "docker-prod" and args.docker_prod_command == "check":
        env_file = args.env_file.expanduser().resolve() if args.env_file else None
        result = check_docker_prod(repo, env_file)
        _json_print(result)
        return 0 if result["ok"] else 1

    if args.command == "mcp":
        from .mcp_server import main as mcp_main

        return mcp_main([])

    parser.error("unsupported command")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
