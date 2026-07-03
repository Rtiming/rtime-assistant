# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only assistant profile and policy diagnostics CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
MAX_TEXT_BYTES = 2_000_000
HEADING_RE = re.compile(r"^(#{1,3})\s+(.+)$", re.MULTILINE)

PROFILE_SOURCES = (
    ("global", "brain", "CLAUDE.md", "global assistant identity and stable preferences"),
    ("global", "brain", "AGENTS.md", "optional brain-side agent rules"),
    ("profile", "brain", "_meta/about-me.md", "optional personal profile summary"),
    ("profile", "brain", "profile/index.md", "optional profile index"),
    ("project", "repo", "README.md", "project overview and boundaries"),
    ("project", "repo", "AGENTS.md", "project agent rules"),
    ("project", "repo", "docs/prompt-layering.md", "prompt layer contract"),
    ("policy", "repo", "docs/context-unlocking.md", "context and sensitivity policy"),
    ("policy", "repo", "docs/bridge-requirements.md", "bridge automation and model policy"),
    ("policy", "repo", "docs/logging-and-audit.md", "logging and redaction policy"),
    ("runtime", "repo", "docs/component-deep-dive.md", "live runtime profile facts"),
    ("runtime", "repo", "docs/ui-guide.md", "user-facing UI behavior"),
    ("runtime", "repo", "docs/runbook.md", "operational profile checks"),
    ("area", "repo", "apps/feishu-bridge/AGENTS.md", "bridge-local agent rules"),
)

CATEGORY_TERMS: dict[str, tuple[str, ...]] = {
    "persona": ("persona", "personality", "tone", "style", "助手", "人格", "语气", "风格"),
    "prompt": ("prompt", "instruction", "layer", "context pack", "提示词", "分层", "上下文"),
    "model": ("model", "claude", "kimi", "deepseek", "qwen", "模型", "适配器"),
    "permission": ("permission", "automation", "write", "deploy", "restart", "权限", "自动执行", "部署", "重启"),
    "sensitive": ("secret", "api key", "token", "identity", "address", "敏感", "密钥", "身份证", "地址"),
    "memory": ("memory", "remember", "context", "recall", "记忆", "回忆", "长期"),
    "output": ("output", "feishu", "lark", "showtoolcalls", "segmented", "输出", "飞书", "分段"),
    "tooling": ("tool", "mcp", "skill", "plugin", "工具", "插件"),
}

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
            (root / "docs" / "prompt-layering.md").is_file()
            and (root / "packages" / "rtime-profile").is_dir()
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
    roots.extend([Path("/mnt/brain"), Path.home() / "brain", Path.home() / "OrangePi-Store" / "sync" / "brain"])
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


def default_brain_root() -> Path | None:
    for root in candidate_brain_roots():
        if root.is_dir():
            return root.resolve()
    return None


def resolve_brain_root(raw: Path | None) -> Path | None:
    if raw is not None:
        return raw.expanduser().resolve()
    return default_brain_root()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _read_text(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES:
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _headings(text: str, *, limit: int = 8) -> list[str]:
    headings: list[str] = []
    for match in HEADING_RE.finditer(text):
        title = match.group(2).strip()
        if title:
            headings.append(title[:120])
        if len(headings) >= limit:
            break
    return headings


def _contains_any(text: str, terms: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(term.lower() in lowered for term in terms)


def _policy_hits(text: str) -> dict[str, bool]:
    return {category: _contains_any(text, terms) for category, terms in CATEGORY_TERMS.items()}


def _source_item(repo: Path, brain: Path | None, layer: str, root_name: str, rel: str, purpose: str) -> JsonObject:
    root = brain if root_name == "brain" else repo
    path = (root / rel) if root else Path(rel)
    exists = bool(root and path.is_file())
    text = _read_text(path) if exists else ""
    return {
        "layer": layer,
        "root": root_name,
        "path": rel,
        "exists": exists,
        "purpose": purpose,
        "size_bytes": path.stat().st_size if exists else 0,
        "heading_count": len(HEADING_RE.findall(text)) if text else 0,
        "headings": _headings(text),
        "policy_hits": _policy_hits(text),
        "body_returned": False,
    }


def doctor(repo: Path | None = None, brain: Path | None = None) -> JsonObject:
    repo_error = ""
    if repo is None:
        try:
            repo = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)
    brain_root = resolve_brain_root(brain)
    checks: JsonObject = {
        "repo_root": "ok" if repo and repo.is_dir() else "missing",
        "brain_root": "ok" if brain_root and brain_root.is_dir() else "missing",
        "global_claude": "missing",
        "project_agents": "missing",
        "prompt_layering_doc": "missing",
        "context_policy_doc": "missing",
        "bridge_requirements_doc": "missing",
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
    }
    if brain_root:
        checks["global_claude"] = "ok" if (brain_root / "CLAUDE.md").is_file() else "missing"
    if repo:
        checks["project_agents"] = "ok" if (repo / "AGENTS.md").is_file() else "missing"
        checks["prompt_layering_doc"] = "ok" if (repo / "docs" / "prompt-layering.md").is_file() else "missing"
        checks["context_policy_doc"] = "ok" if (repo / "docs" / "context-unlocking.md").is_file() else "missing"
        checks["bridge_requirements_doc"] = "ok" if (repo / "docs" / "bridge-requirements.md").is_file() else "missing"
        checks["repo_package"] = (
            "ok"
            if (repo / "packages" / "rtime-profile" / "src" / "rtime_profile" / "cli.py").is_file()
            else "missing"
        )
        checks["repo_skill"] = "ok" if (repo / "skills" / "rtime-profile").is_dir() else "missing"
        checks["repo_plugin"] = "ok" if (repo / "plugins" / "rtime-profile").is_dir() else "missing"
    risks = [name for name, value in checks.items() if value != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    required = {
        "repo_root",
        "project_agents",
        "prompt_layering_doc",
        "context_policy_doc",
        "repo_package",
        "repo_skill",
        "repo_plugin",
    }
    return {
        "ok": not any(risk in risks for risk in required),
        "repo_root": str(repo) if repo else None,
        "brain_root": str(brain_root) if brain_root else None,
        "candidate_brain_roots": [str(path) for path in candidate_brain_roots()],
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
    }


def scan_profile(repo: Path, brain: Path | None = None) -> JsonObject:
    brain_root = resolve_brain_root(brain)
    sources = [
        _source_item(repo, brain_root, layer, root_name, rel, purpose)
        for layer, root_name, rel, purpose in PROFILE_SOURCES
    ]
    layer_counts = Counter(item["layer"] for item in sources if item["exists"])
    coverage: dict[str, bool] = {category: False for category in CATEGORY_TERMS}
    for item in sources:
        if not item["exists"]:
            continue
        for category, hit in item["policy_hits"].items():
            coverage[category] = coverage[category] or bool(hit)
    required_sources = {
        ("brain", "CLAUDE.md"),
        ("repo", "AGENTS.md"),
        ("repo", "docs/prompt-layering.md"),
        ("repo", "docs/context-unlocking.md"),
    }
    missing_required = [
        f"{item['root']}:{item['path']}"
        for item in sources
        if (item["root"], item["path"]) in required_sources and not item["exists"]
    ]
    risks: list[str] = []
    if missing_required:
        risks.append("required_profile_sources_missing")
    for category in ("sensitive", "permission", "model", "output"):
        if not coverage[category]:
            risks.append(f"{category}_policy_not_detected")
    return {
        "ok": not any(risk == "required_profile_sources_missing" for risk in risks),
        "repo_root": str(repo),
        "brain_root": str(brain_root) if brain_root else None,
        "sources": sources,
        "layer_counts": dict(sorted(layer_counts.items())),
        "policy_coverage": coverage,
        "risks": risks,
        "privacy": {
            "source_bodies_returned": False,
            "secrets_read": False,
        },
    }


def panel(repo: Path, brain: Path | None = None) -> JsonObject:
    scan = scan_profile(repo, brain)
    sources_by_layer: dict[str, list[JsonObject]] = {}
    for item in scan["sources"]:
        sources_by_layer.setdefault(item["layer"], []).append(
            {
                "path": item["path"],
                "root": item["root"],
                "exists": item["exists"],
                "heading_count": item["heading_count"],
                "headings": item["headings"],
            }
        )
    review_risks = list(scan["risks"])
    return {
        "ok": not review_risks,
        "repo_root": scan["repo_root"],
        "brain_root": scan["brain_root"],
        "panels": {
            "sources_by_layer": sources_by_layer,
            "policy_coverage": scan["policy_coverage"],
            "adjustment_lanes": _adjustment_lanes(),
            "privacy": scan["privacy"],
        },
        "risks": review_risks,
    }


def _adjustment_lanes() -> list[JsonObject]:
    return [
        {
            "lane": "persona",
            "source": "brain/CLAUDE.md",
            "write_policy": "proposal_first",
            "risk": "normal_profile_change",
        },
        {
            "lane": "project_rules",
            "source": "repo AGENTS/README/docs",
            "write_policy": "commit_with_tests",
            "risk": "project_behavior_change",
        },
        {
            "lane": "model_policy",
            "source": "docs/bridge-requirements.md and runtime config",
            "write_policy": "confirm_before_runtime_change",
            "risk": "runtime_behavior_change",
        },
        {
            "lane": "permission_policy",
            "source": "docs/context-unlocking.md and bridge config",
            "write_policy": "explicit_confirmation_required",
            "risk": "high_impact_policy_change",
        },
        {
            "lane": "sensitive_policy",
            "source": "docs/context-unlocking.md and logging policy",
            "write_policy": "explicit_scope_required",
            "risk": "privacy_sensitive_change",
        },
    ]


def _matches(request: str) -> dict[str, list[str]]:
    lowered = request.lower()
    matches: dict[str, list[str]] = {}
    for category, terms in CATEGORY_TERMS.items():
        found = [term for term in terms if term.lower() in lowered]
        if found:
            matches[category] = found
    return matches


def plan_adjustment(request: str, repo: Path, brain: Path | None = None) -> JsonObject:
    brain_root = resolve_brain_root(brain)
    matches = _matches(request)
    recommended: list[JsonObject] = []
    if "persona" in matches:
        recommended.append(
            {
                "category": "persona",
                "read_first": ["brain/CLAUDE.md", "docs/prompt-layering.md"],
                "write_target": "brain/CLAUDE.md",
                "permission": "proposal_first",
            }
        )
    if "model" in matches:
        recommended.append(
            {
                "category": "model",
                "read_first": ["docs/bridge-requirements.md", "docs/component-deep-dive.md"],
                "write_target": "runtime config or bridge config after confirmation",
                "permission": "confirm_before_runtime_change",
            }
        )
    if "permission" in matches or "sensitive" in matches:
        recommended.append(
            {
                "category": "permission_sensitive",
                "read_first": ["docs/context-unlocking.md", "docs/logging-and-audit.md"],
                "write_target": "policy docs first, runtime config only after explicit scope",
                "permission": "explicit_confirmation_required",
            }
        )
    if "memory" in matches or "prompt" in matches:
        recommended.append(
            {
                "category": "context_memory",
                "read_first": ["docs/context-unlocking.md", "docs/prompt-layering.md"],
                "write_target": "context policy or memory candidate workflow",
                "permission": "review_before_write",
            }
        )
    if "output" in matches:
        recommended.append(
            {
                "category": "output",
                "read_first": ["docs/ui-guide.md", "docs/runbook.md", "apps/feishu-bridge/README.md"],
                "write_target": "output policy/runtime config after tests",
                "permission": "test_before_runtime_change",
            }
        )
    if "tooling" in matches:
        recommended.append(
            {
                "category": "tooling",
                "read_first": ["docs/tooling-packaging.md", "docs/tooling-installation.md"],
                "write_target": "package/skill/plugin source",
                "permission": "validate_before_install",
            }
        )
    if not recommended:
        recommended.append(
            {
                "category": "general_profile_review",
                "read_first": ["docs/prompt-layering.md", "docs/context-unlocking.md"],
                "write_target": "proposal only until category is clear",
                "permission": "proposal_first",
            }
        )
    high_risk_categories = {"permission_sensitive", "model"}
    return {
        "ok": True,
        "repo_root": str(repo),
        "brain_root": str(brain_root) if brain_root else None,
        "request_length": len(request),
        "matched_categories": sorted(matches),
        "recommended_changes": recommended,
        "write_enabled": False,
        "requires_confirmation": any(item["category"] in high_risk_categories for item in recommended),
        "next_step": "draft a change proposal and validate before editing profile or runtime config",
        "privacy": {
            "request_body_logged": False,
            "secret_values_required": False,
        },
    }


def _path_arg(raw: str | None) -> Path | None:
    return Path(raw).expanduser().resolve() if raw else None


def _repo_arg(raw: Path | None) -> Path:
    if raw:
        return raw.expanduser().resolve()
    return find_repo_root()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-profile",
        description="Read-only assistant profile and policy diagnostics.",
    )
    parser.add_argument("--repo-root", dest="global_repo_root", type=Path, default=None)
    parser.add_argument("--brain-root", dest="global_brain_root", type=Path, default=None)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_roots(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--repo-root", type=Path, default=None)
        subparser.add_argument("--brain-root", type=Path, default=None)

    doctor_parser = subparsers.add_parser("doctor", help="check profile surfaces")
    add_roots(doctor_parser)

    scan_parser = subparsers.add_parser("scan", help="scan profile and policy sources")
    add_roots(scan_parser)

    panel_parser = subparsers.add_parser("panel", help="build profile policy panel")
    add_roots(panel_parser)

    plan_parser = subparsers.add_parser("plan", help="plan a profile or policy adjustment")
    plan_parser.add_argument("request", help="adjustment request")
    add_roots(plan_parser)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    raw_repo = getattr(args, "repo_root", None) or args.global_repo_root
    raw_brain = getattr(args, "brain_root", None) or args.global_brain_root
    repo: Path | None = None
    if args.command != "doctor":
        try:
            repo = _repo_arg(raw_repo)
        except RuntimeError as exc:
            _json_print({"ok": False, "errors": [str(exc)]})
            return 1
    else:
        repo = raw_repo.expanduser().resolve() if raw_repo else None
    brain = raw_brain.expanduser().resolve() if raw_brain else None

    if args.command == "doctor":
        data = doctor(repo, brain)
    elif args.command == "scan":
        assert repo is not None
        data = scan_profile(repo, brain)
    elif args.command == "panel":
        assert repo is not None
        data = panel(repo, brain)
    elif args.command == "plan":
        assert repo is not None
        data = plan_adjustment(args.request, repo, brain)
    else:  # pragma: no cover - argparse enforces valid commands
        raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
