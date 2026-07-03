# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only rtime-hub connector CLI."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from pathlib import Path
from typing import Any, Iterable, Sequence


PACKAGE_ROOT = Path(__file__).resolve().parents[3]
DEFAULT_MAX_FILES = 50000
DEFAULT_SAMPLE_LIMIT = 20
MAX_TEXT_BYTES = 1_000_000

SKIP_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".pytest_cache",
    ".ruff_cache",
    "__pycache__",
    "node_modules",
    ".venv",
    "venv",
}
SKIP_FILE_NAMES = {".DS_Store"}
TEXT_SUFFIXES = {"md", "txt", "json", "jsonl", "yaml", "yml"}
SENSITIVE_FILE_NAMES = {
    ".env",
    "secrets.md",
    "secret.md",
    "credentials.json",
    "credential.json",
    "tokens.json",
}
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
    "身份证",
    "地址",
)
SECTION_RULES: dict[str, tuple[str, ...]] = {
    "projects": ("project", "projects", "项目"),
    "devices": ("device", "devices", "node", "nodes", "host", "hosts", "设备", "节点"),
    "contacts": ("contact", "contacts", "people", "person", "addressbook", "通讯录", "联系人", "联络"),
    "services": ("service", "services", "systemd", "docker", "服务"),
    "tasks": ("task", "tasks", "todo", "reminder", "任务", "待办", "提醒"),
    "deployments": ("deployment", "deploy", "runbook", "部署", "上线", "运行手册"),
}
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s+(.+?)\s*$", re.MULTILINE)
TAG_RE = re.compile(r"(?<!\w)#[A-Za-z0-9_/-]+")

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
            (root / "docs" / "tooling-packaging.md").is_file()
            and (root / "packages" / "rtime-hub-connector").is_dir()
            and (root / "skills" / "rtime-hub-connector").is_dir()
        ):
            return root.resolve()
    raise RuntimeError(
        "cannot find rtime-assistant repository root; set RTIME_ASSISTANT_ROOT"
    )


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
    unique: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        resolved = root.resolve() if root.exists() else root.expanduser()
        key = str(resolved)
        if key not in seen:
            seen.add(key)
            unique.append(resolved)
    return unique


def default_hub_root() -> Path | None:
    for root in candidate_hub_roots():
        if root.is_dir():
            return root.resolve()
    return None


def resolve_hub_root(raw: Path | None) -> Path | None:
    if raw is not None:
        return raw.expanduser().resolve()
    return default_hub_root()


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    return any(part in lowered for part in SENSITIVE_KEY_PARTS)


def _is_sensitive_file(path: Path) -> bool:
    lowered = path.name.lower()
    if lowered in SENSITIVE_FILE_NAMES:
        return True
    return any(part in lowered for part in ("secret", "credential", "token", "apikey", "api-key"))


def _walk_files(root: Path, *, max_files: int) -> tuple[list[Path], bool]:
    files: list[Path] = []
    for current, dirs, names in os.walk(root):
        dirs[:] = [name for name in dirs if name not in SKIP_DIRS]
        current_path = Path(current)
        for name in sorted(names):
            if name in SKIP_FILE_NAMES:
                continue
            files.append(current_path / name)
            if len(files) >= max_files:
                return files, True
    return files, False


def _read_text_sample(path: Path) -> str:
    try:
        if path.stat().st_size > MAX_TEXT_BYTES or _is_sensitive_file(path):
            return ""
        return path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return ""


def _title_from_markdown(path: Path) -> str | None:
    if path.suffix.lower() != ".md":
        return None
    text = _read_text_sample(path)
    match = HEADING_RE.search(text)
    if match:
        return match.group(1).strip()[:160]
    return None


def _source_type(path: Path) -> str:
    suffix = path.suffix.lower().lstrip(".")
    if suffix == "md":
        return "markdown"
    if suffix == "json":
        return "json"
    if suffix == "jsonl":
        return "jsonl"
    if suffix in {"yaml", "yml"}:
        return "yaml"
    if suffix == "txt":
        return "text"
    return suffix or "file"


def _path_tokens(path: Path) -> set[str]:
    tokens: set[str] = set()
    for part in path.parts:
        lowered = part.lower()
        tokens.add(lowered)
        tokens.update(piece for piece in re.split(r"[^a-z0-9\u4e00-\u9fff]+", lowered) if piece)
    return tokens


def classify_path(path: Path, *, root: Path) -> list[str]:
    rel = Path(_relative(path, root))
    tokens = _path_tokens(rel)
    sections: list[str] = []
    for section, keywords in SECTION_RULES.items():
        if any(_keyword_matches(keyword, tokens) for keyword in keywords):
            sections.append(section)
    if not sections:
        sections.append("other")
    return sections


def _keyword_matches(keyword: str, tokens: set[str]) -> bool:
    if keyword.isascii():
        return keyword in tokens
    return any(keyword in token for token in tokens)


def _json_metadata(path: Path) -> JsonObject:
    if path.suffix.lower() != ".json" or _is_sensitive_file(path):
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return {"json_error": str(exc)}
    if isinstance(loaded, dict):
        keys = [str(key) for key in loaded.keys()]
        safe_keys = [key for key in keys if not _is_sensitive_key(key)]
        return {
            "json_shape": "object",
            "top_level_key_count": len(keys),
            "sample_keys": safe_keys[:12],
            "sensitive_key_count": len(keys) - len(safe_keys),
        }
    if isinstance(loaded, list):
        return {
            "json_shape": "array",
            "item_count": len(loaded),
            "sample_item_types": sorted({type(item).__name__ for item in loaded[:20]}),
        }
    return {"json_shape": type(loaded).__name__}


def _markdown_signals(path: Path) -> JsonObject:
    if path.suffix.lower() != ".md" or _is_sensitive_file(path):
        return {}
    text = _read_text_sample(path)
    return {
        "headings": len(HEADING_RE.findall(text)),
        "tags": len(TAG_RE.findall(text)),
    }


def _file_card(path: Path, *, root: Path, section: str) -> JsonObject:
    title = _title_from_markdown(path) or path.stem
    metadata = _json_metadata(path)
    markdown = _markdown_signals(path)
    card: JsonObject = {
        "path": _relative(path, root),
        "title": title,
        "section": section,
        "source_type": _source_type(path),
    }
    if metadata:
        card["json"] = metadata
    if markdown:
        card["markdown"] = markdown
    if _is_sensitive_file(path):
        card["sensitive_file_name"] = True
    return card


def _counter_dict(values: Iterable[Any]) -> dict[str, int]:
    return dict(sorted(Counter(str(value) for value in values if value is not None).items()))


def _guidance(root: Path) -> JsonObject:
    return {
        "agents_md": (root / "AGENTS.md").is_file(),
        "status_md": (root / "状态.md").is_file(),
        "readme_md": (root / "README.md").is_file(),
        "scratch_md": (root / "scratch.md").is_file(),
        "first_reads": [
            path
            for path in ("状态.md", "AGENTS.md", "README.md", "scratch.md")
            if (root / path).is_file()
        ],
        "write_boundary": "read-only connector; follow rtime-hub AGENTS.md before any hub write",
    }


def doctor(root: Path | None = None, *, repo: Path | None = None) -> JsonObject:
    resolved = resolve_hub_root(root)
    repo_root: Path | None = repo
    repo_error = ""
    if repo_root is None:
        try:
            repo_root = find_repo_root()
        except RuntimeError as exc:
            repo_error = str(exc)

    root_exists = bool(resolved and resolved.is_dir())
    checks: JsonObject = {
        "hub_root": "ok" if root_exists else "missing",
        "agents_md": "missing",
        "status_md": "missing",
        "projects_dir": "missing",
        "devices_dir": "missing",
        "repo_package": "missing",
        "repo_skill": "missing",
        "repo_plugin": "missing",
    }
    if resolved:
        checks["agents_md"] = "ok" if (resolved / "AGENTS.md").is_file() else "missing"
        checks["status_md"] = "ok" if (resolved / "状态.md").is_file() else "missing"
        checks["projects_dir"] = "ok" if (resolved / "projects").is_dir() else "missing"
        checks["devices_dir"] = "ok" if (resolved / "devices").is_dir() else "missing"
    if repo_root:
        checks["repo_package"] = (
            "ok"
            if (
                repo_root
                / "packages"
                / "rtime-hub-connector"
                / "src"
                / "rtime_hub_connector"
                / "cli.py"
            ).is_file()
            else "missing"
        )
        checks["repo_skill"] = (
            "ok" if (repo_root / "skills" / "rtime-hub-connector").is_dir() else "missing"
        )
        checks["repo_plugin"] = (
            "ok" if (repo_root / "plugins" / "rtime-hub-connector").is_dir() else "missing"
        )

    risks = [name for name, status in checks.items() if status != "ok"]
    if repo_error:
        risks.append("repo_root_not_found")
    return {
        "ok": root_exists and checks["repo_package"] == "ok" and checks["repo_skill"] == "ok",
        "root": str(resolved) if resolved else None,
        "repo_root": str(repo_root) if repo_root else None,
        "candidate_roots": [str(path) for path in candidate_hub_roots()],
        "checks": checks,
        "risks": risks,
        "repo_error": repo_error,
    }


def scan_hub(
    root: Path,
    *,
    max_files: int = DEFAULT_MAX_FILES,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
) -> JsonObject:
    if max_files < 1:
        return {"ok": False, "root": str(root), "errors": ["max_files must be >= 1"]}
    if sample_limit < 0:
        return {"ok": False, "root": str(root), "errors": ["sample_limit must be >= 0"]}
    if not root.is_dir():
        return {"ok": False, "root": str(root), "errors": ["root is not a directory"]}

    files, truncated = _walk_files(root, max_files=max_files)
    by_suffix: Counter[str] = Counter()
    source_types: Counter[str] = Counter()
    sensitive_file_count = 0
    sensitive_json_key_count = 0
    sections: dict[str, JsonObject] = {
        name: {"count": 0, "samples": []}
        for name in [*SECTION_RULES.keys(), "other"]
    }

    for path in files:
        suffix = path.suffix.lower().lstrip(".") or "[no_suffix]"
        by_suffix[suffix] += 1
        source_type = _source_type(path)
        source_types[source_type] += 1
        if _is_sensitive_file(path):
            sensitive_file_count += 1
        path_sections = classify_path(path, root=root)
        for section in path_sections:
            bucket = sections[section]
            bucket["count"] += 1
            if len(bucket["samples"]) < sample_limit:
                card = _file_card(path, root=root, section=section)
                if isinstance(card.get("json"), dict):
                    sensitive_json_key_count += int(card["json"].get("sensitive_key_count", 0))
                bucket["samples"].append(card)

    risks: list[str] = []
    if truncated:
        risks.append("scan_truncated")
    if not (root / "AGENTS.md").is_file():
        risks.append("agents_md_missing")
    if not (root / "projects").is_dir():
        risks.append("projects_dir_missing")
    if not (root / "devices").is_dir():
        risks.append("devices_dir_missing")
    if sensitive_file_count:
        risks.append("sensitive_named_files_present")

    return {
        "ok": not any(risk.endswith("_missing") for risk in risks),
        "root": str(root),
        "truncated": truncated,
        "files_scanned": len(files),
        "guidance": _guidance(root),
        "git": {"exists": (root / ".git").exists()},
        "files": {
            "by_suffix": _counter_dict(by_suffix.elements()),
            "source_types": _counter_dict(source_types.elements()),
            "text_like_suffixes": sorted(TEXT_SUFFIXES),
        },
        "sections": sections,
        "privacy": {
            "body_text_returned": False,
            "sensitive_file_name_count": sensitive_file_count,
            "sensitive_json_key_count": sensitive_json_key_count,
            "note": "samples include paths, titles, and metadata only; file bodies are not returned",
        },
        "risks": risks,
    }


def panel(
    root: Path,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    max_files: int = DEFAULT_MAX_FILES,
) -> JsonObject:
    scanned = scan_hub(root, max_files=max_files, sample_limit=sample_limit)
    if scanned.get("ok") is False:
        return scanned
    section_order = ["projects", "devices", "contacts", "services", "tasks", "deployments"]
    cards = {
        section: scanned["sections"][section]["samples"]
        for section in section_order
        if section in scanned["sections"]
    }
    return {
        "ok": True,
        "root": scanned["root"],
        "guidance": scanned["guidance"],
        "cards": cards,
        "counts": {
            section: scanned["sections"][section]["count"]
            for section in section_order
            if section in scanned["sections"]
        },
        "privacy": scanned["privacy"],
        "risks": scanned["risks"],
    }


def contacts(
    root: Path,
    *,
    sample_limit: int = DEFAULT_SAMPLE_LIMIT,
    max_files: int = DEFAULT_MAX_FILES,
) -> JsonObject:
    scanned = scan_hub(root, max_files=max_files, sample_limit=sample_limit)
    if scanned.get("ok") is False:
        return scanned
    contact_section = scanned["sections"]["contacts"]
    return {
        "ok": True,
        "root": scanned["root"],
        "guidance": scanned["guidance"],
        "count": contact_section["count"],
        "samples": contact_section["samples"],
        "privacy": scanned["privacy"],
        "risks": scanned["risks"],
    }


def _resolved_required_root(raw: str | None) -> Path | None:
    return resolve_hub_root(Path(raw) if raw else None)


def _add_common_scan_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--max-files",
        type=int,
        default=DEFAULT_MAX_FILES,
        help="maximum files to scan before reporting truncation",
    )
    parser.add_argument(
        "--sample-limit",
        type=int,
        default=DEFAULT_SAMPLE_LIMIT,
        help="maximum sample cards to return per section",
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-hub-connector",
        description="Read-only rtime-hub project/device/contact diagnostics.",
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=None,
        help="optional rtime-assistant repository root for doctor checks",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    doctor_parser = subparsers.add_parser("doctor", help="check hub and tool surfaces")
    doctor_parser.add_argument("root", nargs="?", help="rtime-hub root")

    scan_parser = subparsers.add_parser("scan", help="scan hub sections")
    scan_parser.add_argument("root", nargs="?", help="rtime-hub root")
    _add_common_scan_args(scan_parser)

    panel_parser = subparsers.add_parser("panel", help="emit project/device/contact cards")
    panel_parser.add_argument("root", nargs="?", help="rtime-hub root")
    _add_common_scan_args(panel_parser)

    contacts_parser = subparsers.add_parser("contacts", help="emit contact-directory cards")
    contacts_parser.add_argument("root", nargs="?", help="rtime-hub root")
    _add_common_scan_args(contacts_parser)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    repo_root = args.repo_root.expanduser().resolve() if args.repo_root else None

    if args.command == "doctor":
        root = _resolved_required_root(args.root)
        data = doctor(root, repo=repo_root)
        _json_print(data)
        return 0 if data["ok"] else 1

    root = _resolved_required_root(args.root)
    if root is None:
        _json_print(
            {
                "ok": False,
                "root": None,
                "candidate_roots": [str(path) for path in candidate_hub_roots()],
                "errors": ["hub root not found; pass root or set RTIME_HUB_ROOT"],
            }
        )
        return 1

    if args.command == "scan":
        data = scan_hub(root, max_files=args.max_files, sample_limit=args.sample_limit)
    elif args.command == "panel":
        data = panel(root, max_files=args.max_files, sample_limit=args.sample_limit)
    elif args.command == "contacts":
        data = contacts(root, max_files=args.max_files, sample_limit=args.sample_limit)
    else:  # pragma: no cover - argparse enforces valid commands
        raise AssertionError(args.command)
    _json_print(data)
    return 0 if data["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
