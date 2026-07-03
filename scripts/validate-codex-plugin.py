#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Validate a repository-owned Codex plugin.

The preferred validator is the `plugin-creator` skill's official script when it
is installed in the local Codex home. This wrapper keeps repository checks
portable by falling back to a small local contract check on machines that do not
have that skill installed.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path, PurePosixPath
from typing import Any


SEMVER_RE = re.compile(
    r"^(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)\."
    r"(0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?"
    r"(?:\+[0-9A-Za-z.-]+)?$"
)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a local Codex plugin.")
    parser.add_argument("plugin_path", help="Path to the plugin root directory")
    parser.add_argument(
        "--fallback-only",
        action="store_true",
        help="skip the installed plugin-creator validator and use the local fallback",
    )
    return parser.parse_args(argv)


def official_validator_candidates() -> list[Path]:
    candidates: list[Path] = []
    env_path = os.environ.get("CODEX_PLUGIN_VALIDATOR")
    if env_path:
        candidates.append(Path(env_path).expanduser())
    codex_home = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser()
    candidates.append(codex_home / "skills/.system/plugin-creator/scripts/validate_plugin.py")
    return candidates


def run_official_validator(plugin_path: Path) -> int | None:
    for candidate in official_validator_candidates():
        if candidate.is_file():
            completed = subprocess.run(
                [sys.executable, str(candidate), str(plugin_path)],
                text=True,
                check=False,
                capture_output=True,
            )
            if completed.returncode == 0:
                if completed.stdout:
                    print(completed.stdout, end="")
                if completed.stderr:
                    print(completed.stderr, end="", file=sys.stderr)
                return completed.returncode
            dependency_missing = (
                "ModuleNotFoundError" in completed.stderr
                and ("yaml" in completed.stderr or "PyYAML" in completed.stderr)
            )
            if dependency_missing:
                print(
                    f"official plugin validator unavailable ({candidate}: missing PyYAML); using local fallback",
                    file=sys.stderr,
                )
                continue
            if completed.stdout:
                print(completed.stdout, end="")
            if completed.stderr:
                print(completed.stderr, end="", file=sys.stderr)
            return completed.returncode
    return None


def load_json_object(path: Path, label: str, errors: list[str]) -> dict[str, Any] | None:
    if not path.is_file():
        errors.append(f"missing {label}")
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except OSError:
        errors.append(f"unable to read {label}")
        return None
    except json.JSONDecodeError as exc:
        errors.append(f"{label} must be valid JSON: {exc}")
        return None
    if not isinstance(payload, dict):
        errors.append(f"{label} must contain a JSON object")
        return None
    return payload


def require_string(payload: dict[str, Any], key: str, errors: list[str], prefix: str = "") -> str:
    value = payload.get(key)
    label = f"{prefix}.{key}" if prefix else key
    if not isinstance(value, str) or not value.strip():
        errors.append(f"plugin.json field `{label}` must be a non-empty string")
        return ""
    return value


def normalized_relative_path(raw_path: Any) -> str | None:
    if not isinstance(raw_path, str):
        return None
    path = PurePosixPath(raw_path.replace("\\", "/"))
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        return None
    return path.as_posix().rstrip("/")


def reject_todo_markers(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, str):
        if "[TODO:" in value:
            errors.append(f"{path} still contains a `[TODO: ...]` placeholder")
        return
    if isinstance(value, list):
        for index, item in enumerate(value):
            reject_todo_markers(item, f"{path}[{index}]", errors)
        return
    if isinstance(value, dict):
        for key, item in value.items():
            reject_todo_markers(item, f"{path}.{key}", errors)


def validate_skill(skill_root: Path, errors: list[str]) -> None:
    skill_md = skill_root / "SKILL.md"
    if not skill_md.is_file():
        errors.append(f"skill `{skill_root.name}` is missing SKILL.md")
        return
    try:
        contents = skill_md.read_text(encoding="utf-8")
    except OSError:
        errors.append(f"unable to read skill `{skill_root.name}`")
        return
    if not contents.startswith("---\n"):
        errors.append(f"skill `{skill_root.name}` must start with YAML frontmatter")
        return
    frontmatter_end = contents.find("\n---", 4)
    if frontmatter_end == -1:
        errors.append(f"skill `{skill_root.name}` frontmatter is not closed")
        return
    frontmatter = contents[4:frontmatter_end]
    for field in ("name", "description"):
        if re.search(rf"(?m)^{field}:\s*\S", frontmatter) is None:
            errors.append(f"skill `{skill_root.name}` frontmatter field `{field}` must be non-empty")


def validate_fallback(plugin_root: Path) -> list[str]:
    errors: list[str] = []
    manifest = load_json_object(
        plugin_root / ".codex-plugin" / "plugin.json",
        "`.codex-plugin/plugin.json`",
        errors,
    )
    if manifest is None:
        return errors

    reject_todo_markers(manifest, "$", errors)
    allowed_keys = {
        "id",
        "name",
        "version",
        "description",
        "author",
        "skills",
        "apps",
        "mcpServers",
        "interface",
        "homepage",
        "repository",
        "license",
        "keywords",
    }
    for key in sorted(set(manifest) - allowed_keys):
        errors.append(f"plugin.json field `{key}` is not accepted")

    require_string(manifest, "name", errors)
    version = require_string(manifest, "version", errors)
    if version and SEMVER_RE.fullmatch(version) is None:
        errors.append("plugin.json field `version` must be semver")
    require_string(manifest, "description", errors)

    author = manifest.get("author")
    if not isinstance(author, dict):
        errors.append("plugin.json field `author` must be an object")
    else:
        require_string(author, "name", errors, "author")

    interface = manifest.get("interface")
    if not isinstance(interface, dict):
        errors.append("plugin.json field `interface` must be an object")
    else:
        for field in (
            "displayName",
            "shortDescription",
            "longDescription",
            "developerName",
            "category",
        ):
            require_string(interface, field, errors, "interface")
        prompts = interface.get("defaultPrompt", interface.get("default_prompt"))
        if not isinstance(prompts, (str, list)) or not prompts:
            errors.append("plugin.json field `interface.defaultPrompt` is required")
        capabilities = interface.get("capabilities")
        if not isinstance(capabilities, list) or not all(
            isinstance(item, str) and item.strip() for item in capabilities
        ):
            errors.append("plugin.json field `interface.capabilities` must be a string array")

    skills_path = normalized_relative_path(manifest.get("skills"))
    if manifest.get("skills") is not None:
        if skills_path != "skills":
            errors.append("plugin.json field `skills` must resolve to `skills`")
        elif not (plugin_root / skills_path).is_dir():
            errors.append("plugin skills directory is missing")
        else:
            for skill_root in sorted((plugin_root / skills_path).iterdir()):
                if skill_root.is_dir() and not skill_root.name.startswith("."):
                    validate_skill(skill_root, errors)

    mcp_path = normalized_relative_path(manifest.get("mcpServers"))
    if manifest.get("mcpServers") is not None:
        if mcp_path != ".mcp.json":
            errors.append("plugin.json field `mcpServers` must resolve to `.mcp.json`")
        else:
            mcp_payload = load_json_object(plugin_root / ".mcp.json", "`.mcp.json`", errors)
            if mcp_payload is not None and not isinstance(mcp_payload.get("mcpServers"), dict):
                errors.append("`.mcp.json` field `mcpServers` must be an object")

    return errors


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    plugin_path = Path(args.plugin_path).expanduser().resolve()
    if not args.fallback_only:
        official_exit = run_official_validator(plugin_path)
        if official_exit is not None:
            return official_exit

    errors = validate_fallback(plugin_path)
    if errors:
        print("Plugin validation failed:")
        for error in errors:
            print(f"- {error}")
        return 1
    print(f"Plugin validation passed: {plugin_path} (local fallback)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
