# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only dynamic context unlock planner CLI."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_ENTRY = "cli"

SIGNAL_RULES: dict[str, tuple[str, ...]] = {
    "literature": (
        "paper",
        "papers",
        "pdf",
        "citation",
        "citations",
        "zotero",
        "obsidian",
        "docpack",
        "文献",
        "论文",
        "引用",
        "资料",
    ),
    "runtime": (
        "feishu",
        "lark",
        "bridge",
        "systemd",
        "runtime",
        "run log",
        "飞书",
        "桥",
        "运行",
        "日志",
    ),
    "hub": (
        "rtime-hub",
        "project",
        "projects",
        "device",
        "devices",
        "contact",
        "contacts",
        "status",
        "项目",
        "设备",
        "通讯录",
        "联络",
        "状态",
    ),
    "profile": (
        "profile",
        "persona",
        "prompt",
        "model policy",
        "permission policy",
        "assistant",
        "助手",
        "人格",
        "提示词",
        "模型策略",
        "权限策略",
        "输出策略",
    ),
    "automation": (
        "automation",
        "workflow",
        "reminder",
        "remind",
        "scheduler",
        "schedule",
        "timer",
        "notification",
        "notify",
        "feishu",
        "lark",
        "cron",
        "自动化",
        "流程",
        "提醒",
        "定时",
        "定时任务",
        "通知",
        "飞书",
        "推送",
    ),
    "code": (
        "code",
        "refactor",
        "pytest",
        "test",
        "tests",
        "commit",
        "代码",
        "重构",
        "测试",
        "实现",
    ),
    "ops": (
        "docker",
        "compose",
        "deploy",
        "restart",
        "service",
        "ssh",
        "orangepi",
        "部署",
        "重启",
        "服务",
        "香橙派",
    ),
    "memory": (
        "memory",
        "remember",
        "forget",
        "context",
        "why-context",
        "上下文",
        "记忆",
        "回忆",
    ),
    "sensitive": (
        "api key",
        "apikey",
        "secret",
        "token",
        "password",
        "credential",
        "id card",
        "identity",
        "address",
        "密钥",
        "密码",
        "身份证",
        "地址",
        "凭证",
    ),
}

ACTION_TERMS = (
    "write",
    "edit",
    "commit",
    "deploy",
    "restart",
    "push",
    "修改",
    "写入",
    "提交",
    "部署",
    "重启",
)
EVIDENCE_TERMS = (
    "verify",
    "audit",
    "review",
    "debug",
    "error",
    "test",
    "citation",
    "查",
    "审计",
    "复核",
    "验证",
    "测试",
    "错误",
    "引用",
)
PATH_RE = re.compile(r"(?:(?:\./|\.\./|/|~/?)[^\s'\"，。；;]+|[\w.-]+/[\w./-]+)")

JsonObject = dict[str, Any]


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
            (root / "docs" / "context-unlocking.md").is_file()
            and (root / "packages" / "rtime-context").is_dir()
            and (root / "skills" / "rtime-context").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


def candidate_brain_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("BRAIN_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend([Path.home() / "brain", Path("/srv/brain"), Path("/mnt/brain")])
    return _unique_paths(roots)


def candidate_hub_roots() -> list[Path]:
    roots: list[Path] = []
    env_root = os.environ.get("RTIME_HUB_ROOT")
    if env_root:
        roots.append(Path(env_root).expanduser())
    roots.extend(
        [
            Path.home() / "rtime-hub",
            Path("/srv/rtime-hub"),
            Path("~/rtime-hub").expanduser(),
        ]
    )
    return _unique_paths(roots)


def _unique_paths(paths: list[Path]) -> list[Path]:
    unique: list[Path] = []
    seen: set[str] = set()
    for path in paths:
        resolved = path.resolve() if path.exists() else path.expanduser()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def _default_existing(candidates: list[Path]) -> Path | None:
    for path in candidates:
        if path.is_dir():
            return path.resolve()
    return None


def _request_hash(request: str) -> str:
    return hashlib.sha256(request.encode("utf-8")).hexdigest()[:16]


def _request_preview(request: str) -> str:
    compact = " ".join(request.split())
    return compact[:120]


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _signal_matches(request: str) -> dict[str, list[str]]:
    lowered = request.lower()
    matches: dict[str, list[str]] = {}
    for group, terms in SIGNAL_RULES.items():
        found = [term for term in terms if term.lower() in lowered]
        if found:
            matches[group] = found
    return matches


def _mentioned_paths(request: str) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()
    for match in PATH_RE.findall(request):
        cleaned = match.rstrip(".,;:，。；：")
        if cleaned and cleaned not in seen:
            seen.add(cleaned)
            paths.append(cleaned)
    return paths[:20]


def _local_rules(workspace: Path) -> list[JsonObject]:
    candidates = [
        "AGENTS.md",
        "CLAUDE.md",
        "README.md",
        "docs/overview.md",
        "docs/architecture.md",
        "docs/workflows.md",
        ".context/manifest.yaml",
        ".context/context-map.md",
    ]
    rules: list[JsonObject] = []
    for rel in candidates:
        path = workspace / rel
        if path.is_file():
            rules.append({"path": rel, "kind": _rule_kind(rel)})
    return rules


def _rule_kind(path: str) -> str:
    if path.endswith("AGENTS.md") or path.endswith("CLAUDE.md"):
        return "agent_rules"
    if path.startswith(".context/"):
        return "context_manifest"
    if path.startswith("docs/"):
        return "project_docs"
    return "project_overview"


def _levels(matches: dict[str, list[str]], request: str) -> list[str]:
    levels = ["L0"]
    if matches or _mentioned_paths(request):
        levels.append("L1")
    if _contains_any(request, EVIDENCE_TERMS) or any(
        key in matches for key in ("literature", "runtime", "profile", "automation", "code")
    ):
        levels.append("L2")
    if any(key in matches for key in ("code", "ops", "automation")) or _contains_any(request, ACTION_TERMS):
        levels.append("L3")
    if _contains_any(request, ("deploy", "restart", "push", "ssh", "部署", "重启")):
        levels.append("L4")
    if "memory" in matches:
        levels.append("L5")
    return levels


def _risk(matches: dict[str, list[str]], request: str) -> str:
    if "sensitive" in matches:
        return "sensitive_context_requested"
    if _contains_any(request, ("deploy", "restart", "push", "ssh", "部署", "重启")):
        return "runtime_action"
    if "code" in matches:
        return "code_change_or_review"
    if "literature" in matches:
        return "literature_or_citation"
    if "profile" in matches:
        return "assistant_profile_policy"
    if "automation" in matches:
        return "automation_or_reminder"
    if "runtime" in matches or "ops" in matches:
        return "runtime_evidence"
    if "hub" in matches:
        return "project_device_context"
    if "memory" in matches:
        return "memory_context"
    return "general_context"


def _lane(name: str, level: str, reason: str, query: str, *, tools: list[str] | None = None) -> JsonObject:
    data: JsonObject = {
        "lane": name,
        "level": level,
        "reason": reason,
        "query": query,
    }
    if tools:
        data["recommended_tools"] = tools
    return data


def build_plan(
    request: str,
    *,
    workspace: Path | None = None,
    brain_root: Path | None = None,
    hub_root: Path | None = None,
    entry: str = DEFAULT_ENTRY,
    allow_sensitive: bool = False,
) -> JsonObject:
    workspace = (workspace or Path.cwd()).expanduser().resolve()
    brain_root = brain_root.expanduser().resolve() if brain_root else _default_existing(candidate_brain_roots())
    hub_root = hub_root.expanduser().resolve() if hub_root else _default_existing(candidate_hub_roots())
    matches = _signal_matches(request)
    paths = _mentioned_paths(request)
    levels = _levels(matches, request)
    risk = _risk(matches, request)
    rules = _local_rules(workspace) if workspace.is_dir() else []

    lanes: list[JsonObject] = [
        _lane(
            "Seed",
            "L0",
            "every task starts from stable local rules and minimal profile context",
            "global rules, current workspace, and short profile summary",
        )
    ]
    if workspace.is_dir():
        lanes.append(
            _lane(
                "Workspace",
                "L1",
                "active workspace is the first authority for this task",
                str(workspace),
                tools=["rg --files", "read local README/AGENTS/docs"],
            )
        )
    if "profile" in matches:
        lanes.append(
            _lane(
                "Assistant Profile & Policy",
                "L2",
                "request mentions assistant persona, prompt, model, permission, or output policy",
                "profile and policy sources in brain plus rtime-assistant docs",
                tools=["rtime-profile panel", "rtime-profile plan"],
            )
        )
    if "automation" in matches:
        lanes.append(
            _lane(
                "Workflow Automation",
                "L2",
                "request mentions reminders, schedulers, notifications, or workflow automation",
                "automation/reminder surfaces, timer templates, notification wiring, and run-log policy",
                tools=["rtime-automation panel", "rtime-automation plan"],
            )
        )
    if "hub" in matches:
        lanes.append(
            _lane(
                "Project Workspace / rtime-hub",
                "L1",
                "request mentions project, device, status, contact, or hub context",
                str(hub_root) if hub_root else "rtime-hub root not found; set RTIME_HUB_ROOT or --hub-root",
                tools=["rtime-hub-connector panel", "rtime-hub-connector scan"],
            )
        )
    if "literature" in matches:
        lanes.append(
            _lane(
                "Brain / Knowledge Store",
                "L1",
                "request mentions library, literature, Obsidian, Zotero, DocPack, or citation work",
                str(brain_root) if brain_root else "brain root not found; set BRAIN_ROOT or --brain-root",
                tools=[
                    "brain-library scan",
                    "brain-library docpacks",
                    "brain-citation panel",
                    "brain-docpack audit",
                ],
            )
        )
    if "runtime" in matches or "ops" in matches:
        lanes.append(
            _lane(
                "Runtime Evidence",
                "L2",
                "request mentions runtime, bridge, logs, Docker, deployment, or services",
                "runtime templates, run logs, Docker/Compose state",
                tools=["rtime-runtime doctor", "rtime-runtime templates check"],
            )
        )
    if paths:
        lanes.append(
            _lane(
                "Evidence",
                "L2",
                "request contains explicit paths or path-like anchors",
                " ".join(paths),
                tools=["read mentioned files", "rg exact anchors"],
            )
        )
    if "memory" in matches:
        lanes.append(
            _lane(
                "History / Reflection",
                "L5",
                "request mentions memory, context, recall, or why-context behavior",
                "prior worklogs, memory candidates, and context policy docs",
                tools=["rtime-context explain", "review memory candidates"],
            )
        )

    excluded: list[JsonObject] = []
    if "sensitive" in matches and not allow_sensitive:
        excluded.append(
            {
                "lane": "Sensitive",
                "reason": "sensitive-looking terms were detected but allow_sensitive is false",
                "policy": "requires explicit task scope; do not read secrets or private identity data",
            }
        )
    elif "sensitive" in matches:
        lanes.append(
            _lane(
                "Sensitive",
                "L2",
                "explicitly allowed sensitive metadata planning; still do not read secret bodies by default",
                "metadata-only sensitive context plan",
                tools=["confirm scope before any sensitive file read"],
            )
        )
    else:
        excluded.append(
            {
                "lane": "Sensitive",
                "reason": "no explicit sensitive-context need detected",
                "policy": "do not unlock credentials, identity data, addresses, or session stores",
            }
        )

    return {
        "ok": True,
        "request_hash": _request_hash(request),
        "request_preview": _request_preview(request),
        "entry": entry,
        "workspace": str(workspace),
        "workspace_exists": workspace.is_dir(),
        "brain_root": str(brain_root) if brain_root else None,
        "hub_root": str(hub_root) if hub_root else None,
        "task_signals": {
            "groups": matches,
            "mentioned_paths": paths,
            "local_rules": rules,
        },
        "risk": risk,
        "levels": levels,
        "lanes": lanes,
        "excluded": excluded,
        "permissions": {
            "default": "read_only",
            "sensitive_unlocked": bool("sensitive" in matches and allow_sensitive),
            "actions_require_confirmation": "L4" in levels,
        },
    }


def build_pack(
    request: str,
    *,
    workspace: Path | None = None,
    brain_root: Path | None = None,
    hub_root: Path | None = None,
    entry: str = DEFAULT_ENTRY,
    allow_sensitive: bool = False,
) -> JsonObject:
    plan = build_plan(
        request,
        workspace=workspace,
        brain_root=brain_root,
        hub_root=hub_root,
        entry=entry,
        allow_sensitive=allow_sensitive,
    )
    sources = []
    for lane in plan["lanes"]:
        source = {
            "lane": lane["lane"],
            "level": lane["level"],
            "query": lane["query"],
            "reason": lane["reason"],
        }
        if "recommended_tools" in lane:
            source["recommended_tools"] = lane["recommended_tools"]
        sources.append(source)
    return {
        "ok": True,
        "kind": "context_pack_skeleton",
        "active_task": plan["request_preview"],
        "request_hash": plan["request_hash"],
        "entry": plan["entry"],
        "workspace": plan["workspace"],
        "local_rules": plan["task_signals"]["local_rules"],
        "unlock_plan": {
            "risk": plan["risk"],
            "levels": plan["levels"],
            "lanes": [lane["lane"] for lane in plan["lanes"]],
            "excluded": plan["excluded"],
            "permissions": plan["permissions"],
        },
        "sources_to_load": sources,
        "must_not_assume": [
            "retrieved memories are supporting evidence, not authority",
            "sensitive data remains locked unless explicit scope and permission are present",
            "current files, command output, and user corrections override older memory",
        ],
    }


def explain_plan(plan: JsonObject) -> JsonObject:
    explanations = []
    for lane in plan.get("lanes", []):
        explanations.append(
            {
                "lane": lane.get("lane"),
                "level": lane.get("level"),
                "why": lane.get("reason"),
                "query": lane.get("query"),
                "recommended_tools": lane.get("recommended_tools", []),
            }
        )
    return {
        "ok": True,
        "request_hash": plan.get("request_hash"),
        "risk": plan.get("risk"),
        "levels": plan.get("levels", []),
        "explanations": explanations,
        "excluded": plan.get("excluded", []),
    }


def doctor(repo: Path | None = None) -> JsonObject:
    repo_root: Path | None = repo
    repo_error = ""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)
    checks: JsonObject = {
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
        "repo_docs": "missing",
        "brain_root": "missing",
        "hub_root": "missing",
    }
    if repo_root:
        checks["repo_package"] = (
            "ok"
            if (repo_root / "packages" / "rtime-context" / "src" / "rtime_context" / "cli.py").is_file()
            else "missing"
        )
        checks["repo_skill"] = "ok" if (repo_root / "skills" / "rtime-context").is_dir() else "missing"
        checks["repo_plugin"] = "ok" if (repo_root / "plugins" / "rtime-context").is_dir() else "missing"
        checks["repo_docs"] = "ok" if (repo_root / "docs" / "context-orchestrator.md").is_file() else "missing"
    checks["brain_root"] = "ok" if _default_existing(candidate_brain_roots()) else "missing"
    checks["hub_root"] = "ok" if _default_existing(candidate_hub_roots()) else "missing"
    risks = [name for name, value in checks.items() if value != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": checks["repo_package"] == "ok" and checks["repo_skill"] == "ok",
        "repo_root": str(repo_root) if repo_root else None,
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
        "candidate_brain_roots": [str(path) for path in candidate_brain_roots()],
        "candidate_hub_roots": [str(path) for path in candidate_hub_roots()],
    }


def _path_arg(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-context",
        description="Read-only context unlock planning for rtime assistant workflows.",
    )
    parser.add_argument("--repo-root", type=Path, default=None, help="optional repository root")
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("doctor", help="check context tool surfaces")

    for name, help_text in (
        ("plan", "build a ContextUnlockPlan"),
        ("pack", "build a Context Pack skeleton"),
        ("explain", "explain why lanes would be unlocked"),
    ):
        command = subparsers.add_parser(name, help=help_text)
        command.add_argument("request", help="user request or task text")
        command.add_argument("--workspace", help="active workspace path")
        command.add_argument("--brain-root", help="brain root path")
        command.add_argument("--hub-root", help="rtime-hub root path")
        command.add_argument("--entry", default=DEFAULT_ENTRY, help="entry adapter name")
        command.add_argument(
            "--allow-sensitive",
            action="store_true",
            help="plan sensitive metadata lanes; still does not read secret bodies",
        )

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve() if args.repo_root else None

    if args.command == "doctor":
        data = doctor(repo_root)
        _json_print(data)
        return 0 if data["ok"] else 1

    common = {
        "workspace": _path_arg(args.workspace),
        "brain_root": _path_arg(args.brain_root),
        "hub_root": _path_arg(args.hub_root),
        "entry": args.entry,
        "allow_sensitive": args.allow_sensitive,
    }
    if args.command == "plan":
        data = build_plan(args.request, **common)
    elif args.command == "pack":
        data = build_pack(args.request, **common)
    elif args.command == "explain":
        data = explain_plan(build_plan(args.request, **common))
    else:  # pragma: no cover - argparse enforces commands
        raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
