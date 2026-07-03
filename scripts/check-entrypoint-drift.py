#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Check cross-surface entrypoint drift for rtime-assistant.

A single tool (e.g. brain-docpack) is described on many surfaces at once:
``packages/<n>/pyproject.toml`` console scripts, ``plugins/<n>/.codex-plugin/
plugin.json``, ``plugins/<n>/.mcp.json``, the wrapper under ``plugins/<n>/
scripts/``, the canonical ``skills/<n>/SKILL.md`` and its bundled copy, and
``module-submit.json``. Renaming or editing one surface and forgetting the rest
silently drifts. This script asserts the mechanical couplings between them so the
drift fails fast. It pairs with the ``KEEP IN SYNC:`` marker convention
(see docs/maintainability-standards.zh-CN.md): this guards what is mechanically
checkable; the markers guard the rest.

Errors (exit 1):
  - a bundled ``plugins/<n>/skills/<n>/SKILL.md`` that differs from its canonical
    ``skills/<n>/SKILL.md`` (the bundled copy is a derived artifact);
  - a plugin whose dir name, ``plugin.json`` ``name`` and ``.mcp.json`` server key
    disagree;
  - an ``.mcp.json`` ``command`` that points at a missing file;
  - a ``plugin.json`` ``version`` that disagrees with its package pyproject version.

Warnings (exit 0):
  - ``module-submit.json`` paths that do not exist on disk (some are intentional
    forward-looking placeholders, e.g. apps/wechat-bridge/);
  - ``packages/``/``plugins/``/``skills/`` dirs not referenced by any module.

Deliberate exceptions that must NOT be flagged (kept narrow, see code):
  - ``rtime-reminder`` is plugin-only (no ``packages/rtime-reminder``) -> version
    check skipped; its wrapper execs ``deploy/bin/rtime-reminder-mcp``.
  - wrapper *filenames* may differ from the server name (rtime-assistant-runtime
    ships ``rtime-runtime-mcp.sh``; rtime-hub-connector ships ``rtime-hub-mcp.sh``):
    we assert the command FILE exists, not that it is named ``<n>-mcp.sh``.
"""

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
from pathlib import Path

# Plugins that intentionally have no packages/<name> sibling (version check skipped).
PLUGINS_WITHOUT_PACKAGE = {"rtime-reminder"}


def _read_pyproject_version(pyproject: Path) -> str | None:
    section = None
    for line in pyproject.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            section = stripped[1:-1]
            continue
        if section == "project":
            match = re.match(r'version\s*=\s*"([^"]+)"', stripped)
            if match:
                return match.group(1)
    return None


def _load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))


def _module_literals(path: Path) -> dict:
    """Return {name: value} for top-level constant assignments that are literals.
    Used to read constants out of .py / extension-less scripts without importing
    them (so this gate stays pure-stdlib and side-effect free)."""
    out: dict = {}
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return out
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name):
                try:
                    out[target.id] = ast.literal_eval(node.value)
                except (ValueError, SyntaxError, TypeError):
                    pass
    return out


def _check_course_vocab(root: Path, errors: list[str]) -> None:
    """Course-view Chinese vocab is mirrored in two files (a discarded draft asked
    for this gate): course_intake.OBSIDIAN_CATEGORY_FOLDERS (+ markdown folder) must
    reproduce m4_link.COURSE_VIEW_NAMES. KEEP IN SYNC markers live in both files."""
    ci = root / "packages/brain-docpack/src/brain_docpack/course_intake.py"
    ml = root / "scripts/brain-intake/m4_link.py"
    if not (ci.is_file() and ml.is_file()):
        return
    ci_lit = _module_literals(ci)
    ml_lit = _module_literals(ml)
    folders = ci_lit.get("OBSIDIAN_CATEGORY_FOLDERS")
    markdown = ci_lit.get("OBSIDIAN_MARKDOWN_FOLDER")
    view_names = ml_lit.get("COURSE_VIEW_NAMES")
    if not isinstance(folders, dict) or not isinstance(markdown, str) or not isinstance(view_names, (set, frozenset)):
        errors.append(
            "course vocab: could not read OBSIDIAN_CATEGORY_FOLDERS/OBSIDIAN_MARKDOWN_FOLDER "
            "(course_intake.py) or COURSE_VIEW_NAMES (m4_link.py)"
        )
        return
    # Visible view names = every category folder except the misc fallback, plus the
    # markdown folder. (misc/资料 is a temporary catch-all, not an Obsidian view.)
    misc = folders.get("misc")
    expected = (set(folders.values()) - {misc}) | {markdown}
    if set(view_names) != expected:
        errors.append(
            "course vocab drift: m4_link.COURSE_VIEW_NAMES "
            f"{sorted(view_names)} != expected {sorted(expected)} derived from "
            "course_intake.OBSIDIAN_CATEGORY_FOLDERS (minus misc) + OBSIDIAN_MARKDOWN_FOLDER"
        )


def _check_gateway_port(root: Path, errors: list[str]) -> None:
    """Default gateway port 8765 is hard-coded in gateway.py, the Obsidian plugin
    settings.ts, and the deploy env example. Assert the three agree."""
    found: dict[str, str] = {}
    gateway = root / "apps/assistant-gateway/gateway.py"
    if gateway.is_file():
        m = re.search(r'GATEWAY_PORT["\']\s*,\s*["\'](\d+)["\']', gateway.read_text(encoding="utf-8"))
        if m:
            found["gateway.py"] = m.group(1)
    settings = root / "apps/obsidian-rtime-assistant/src/settings.ts"
    if settings.is_file():
        ports = set(re.findall(r"127\.0\.0\.1:(\d+)", settings.read_text(encoding="utf-8")))
        if len(ports) == 1:
            found["settings.ts"] = next(iter(ports))
        elif len(ports) > 1:
            errors.append(f"gateway port: settings.ts has inconsistent 127.0.0.1 ports {sorted(ports)}")
    env = root / "deploy/env/assistant-gateway.env.example"
    if env.is_file():
        m = re.search(r"^GATEWAY_PORT=(\d+)", env.read_text(encoding="utf-8"), re.MULTILINE)
        if m:
            found["assistant-gateway.env.example"] = m.group(1)
    distinct = set(found.values())
    if len(distinct) > 1:
        errors.append(f"gateway port drift: {found} (default port 8765 must agree across all three)")


# ---- model registry (P3) consistency -------------------------------------------------

def _registry_module(root: Path):
    """Import the model registry loader from ``root`` (pointing it at root's
    registry JSON). Returns the module or None when the package is absent (so the
    drift-gate unit tests, which build minimal repos, skip the registry checks)."""
    src = root / "packages/rtime-models/src"
    reg = root / "packages/rtime-models/model-registry.json"
    if not (src.is_dir() and reg.is_file()):
        return None
    sys.path.insert(0, str(src))
    os.environ["RTIME_MODEL_REGISTRY"] = str(reg)
    try:
        import rtime_models

        rtime_models.load_registry(force_reload=True)
        return rtime_models
    except Exception as exc:  # pragma: no cover - defensive
        return _RegistryImportError(str(exc))


class _RegistryImportError:
    def __init__(self, message: str):
        self.message = message


def _parse_env_assignments(text: str, key: str) -> list[str]:
    """All values assigned to ``key`` in an env file, including commented examples
    (leading ``#``) and an optional surrounding single quote."""
    values: list[str] = []
    pattern = re.compile(rf"^\s*#?\s*{re.escape(key)}=(.*)$")
    for line in text.splitlines():
        m = pattern.match(line)
        if not m:
            continue
        raw = m.group(1).strip()
        if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "'\"":
            raw = raw[1:-1]
        values.append(raw)
    return values


def _check_model_registry(root: Path, errors: list[str]) -> None:
    reg = _registry_module(root)
    if reg is None:
        return
    if isinstance(reg, _RegistryImportError):
        errors.append(f"model registry: could not import loader: {reg.message}")
        return

    # 1) Generated bash defaults must match the registry render (regenerate-and-diff).
    defaults = root / "deploy/bin/model-defaults.sh"
    if defaults.is_file():
        if defaults.read_text(encoding="utf-8") != reg.render_bash_defaults():
            errors.append(
                "model registry drift: deploy/bin/model-defaults.sh is stale "
                "(regen: python -m rtime_models gen-bash-defaults > deploy/bin/model-defaults.sh)"
            )

    # 2) claude-rtime import-guarded fallbacks must equal the registry projection.
    rtime = root / "deploy/bin/claude-rtime"
    if rtime.is_file():
        lit = _module_literals(rtime)
        _eq(errors, "claude-rtime _FALLBACK_ALIASES", lit.get("_FALLBACK_ALIASES"),
            reg.alias_map(["ustc-openai", "deepseek-code", "qwen-code", "ollama"]))
        _eq(errors, "claude-rtime _FALLBACK_DEEPSEEK_CODE_MODELS", _as_set(lit.get("_FALLBACK_DEEPSEEK_CODE_MODELS")),
            set(reg.code_model_ids("deepseek-code")))
        _eq(errors, "claude-rtime _FALLBACK_QWEN_CODE_MODELS", _as_set(lit.get("_FALLBACK_QWEN_CODE_MODELS")),
            set(reg.code_model_ids("qwen-code")))
        _eq(errors, "claude-rtime _FALLBACK_USTC_MODELS", _as_set(lit.get("_FALLBACK_USTC_MODELS")),
            set(reg.routing_model_ids("ustc-openai")))
        _eq(errors, "claude-rtime _FALLBACK_OLLAMA_MODELS", _as_set(lit.get("_FALLBACK_OLLAMA_MODELS")),
            set(reg.routing_model_ids("ollama")))
        _eq(errors, "claude-rtime _FALLBACK_USTC_BASE_URL", lit.get("_FALLBACK_USTC_BASE_URL"),
            reg.base_url("ustc-openai"))

    # 3) Feishu bridge model_routing import-guarded fallbacks.
    routing = root / "apps/feishu-bridge/model_routing.py"
    if routing.is_file():
        lit = _module_literals(routing)
        _eq(errors, "model_routing _FALLBACK_BASE_ALIASES", lit.get("_FALLBACK_BASE_ALIASES"),
            reg.alias_map(["claude-anthropic"]))
        _eq(errors, "model_routing _FALLBACK_DEFAULT_MODEL", lit.get("_FALLBACK_DEFAULT_MODEL"),
            reg.default_model_id())
        _eq(errors, "model_routing _FALLBACK_USTC_CHAT_MODELS", _as_set(lit.get("_FALLBACK_USTC_CHAT_MODELS")),
            set(reg.routing_model_ids("ustc-openai")))
        _eq(errors, "model_routing _FALLBACK_OLLAMA_MODELS", _as_set(lit.get("_FALLBACK_OLLAMA_MODELS")),
            set(reg.routing_model_ids("ollama")))
        _eq(errors, "model_routing _FALLBACK_DEEPSEEK_CODE_MODELS", _as_set(lit.get("_FALLBACK_DEEPSEEK_CODE_MODELS")),
            reg.code_models_with_aliases("deepseek-code"))
        _eq(errors, "model_routing _FALLBACK_QWEN_CODE_MODELS", _as_set(lit.get("_FALLBACK_QWEN_CODE_MODELS")),
            reg.code_models_with_aliases("qwen-code"))

    # 4) Bash wrapper inline ${REG_*:-LITERAL} fallbacks must equal the registry.
    _check_wrapper_inline(root, reg, errors)

    # 5) MODEL_ALIASES_JSON in the env examples must equal the registry alias map.
    expected_aliases = reg.feishu_model_aliases()
    for rel in (".env.example", "apps/feishu-bridge/.env.example", "deploy/env/feishu-bridge.prod.env.example"):
        path = root / rel
        if not path.is_file():
            continue
        for raw in _parse_env_assignments(path.read_text(encoding="utf-8"), "MODEL_ALIASES_JSON"):
            try:
                parsed = json.loads(raw)
            except ValueError:
                errors.append(f"model registry drift: {rel} MODEL_ALIASES_JSON is not valid JSON")
                continue
            if parsed != expected_aliases:
                errors.append(
                    f"model registry drift: {rel} MODEL_ALIASES_JSON does not match the registry "
                    "alias map (regen from rtime_models.feishu_model_aliases())"
                )

    # 6) Obsidian capability schema keys must match rtime_models.CAPABILITY_KEYS.
    types_ts = root / "apps/obsidian-rtime-assistant/src/types.ts"
    if types_ts.is_file():
        text = types_ts.read_text(encoding="utf-8")
        m = re.search(r"interface AssistantModelCapabilities\s*\{([^}]*)\}", text)
        if m:
            keys = set(re.findall(r"(\w+)\??\s*:", m.group(1)))
            if keys != set(reg.CAPABILITY_KEYS):
                errors.append(
                    "model registry drift: AssistantModelCapabilities keys "
                    f"{sorted(keys)} != rtime_models.CAPABILITY_KEYS {sorted(reg.CAPABILITY_KEYS)}"
                )


def _check_wrapper_inline(root: Path, reg, errors: list[str]) -> None:
    """Each claude-* wrapper carries ${RTIME_*:-${REG_*:-LITERAL}} fallbacks; the
    LITERAL must equal the registry value so a missing model-defaults.sh still routes
    correctly."""
    def inline(path: Path, var: str) -> str | None:
        if not path.is_file():
            return None
        m = re.search(rf"\$\{{{var}:-([^}}]*)\}}", path.read_text(encoding="utf-8"))
        return m.group(1) if m else None

    ds = root / "deploy/bin/claude-deepseek"
    qw = root / "deploy/bin/claude-qwen"
    km = root / "deploy/bin/claude-kimi"
    us = root / "deploy/bin/claude-ustc"
    ol = root / "deploy/bin/claude-ollama"
    ds_tiers, qw_tiers = reg.tiers("deepseek-code"), reg.tiers("qwen-code")
    ustc_tiers, ollama_tiers = reg.tiers("ustc-openai"), reg.tiers("ollama")
    checks = [
        (ds, "REG_DEEPSEEK_MODEL", ds_tiers.get("default")),
        (ds, "REG_DEEPSEEK_FAST_MODEL", ds_tiers.get("fast")),
        (ds, "REG_DEEPSEEK_BASE_URL", reg.base_url("deepseek-code")),
        (qw, "REG_QWEN_MODEL", qw_tiers.get("default")),
        (qw, "REG_QWEN_FAST_MODEL", qw_tiers.get("fast")),
        (qw, "REG_QWEN_QUALITY_MODEL", qw_tiers.get("quality")),
        (qw, "REG_QWEN_BASE_URL", reg.base_url("qwen-code")),
        (km, "REG_KIMI_BASE_URL", reg.base_url("kimi-code-wrapper")),
        (us, "REG_USTC_MODEL", ustc_tiers.get("default")),
        (ol, "REG_OLLAMA_MODEL", ollama_tiers.get("default")),
        (ol, "REG_OLLAMA_BASE_URL", reg.base_url("ollama")),
    ]
    for path, var, expected in checks:
        got = inline(path, var)
        if got is not None and got != expected:
            errors.append(
                f"model registry drift: {path.name} inline {var} fallback {got!r} != registry {expected!r}"
            )


def _as_set(value):
    return set(value) if isinstance(value, (set, frozenset, list, tuple)) else value


def _eq(errors: list[str], label: str, got, expected) -> None:
    if got != expected:
        errors.append(f"model registry drift: {label} {got!r} != registry {expected!r}")


def run_checks(root: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) as human-readable message strings."""
    errors: list[str] = []
    warnings: list[str] = []
    plugins_dir = root / "plugins"
    skills_dir = root / "skills"
    packages_dir = root / "packages"

    plugin_dirs = (
        sorted(p for p in plugins_dir.iterdir() if p.is_dir())
        if plugins_dir.is_dir()
        else []
    )
    for plugin in plugin_dirs:
        name = plugin.name
        plugin_json = plugin / ".codex-plugin" / "plugin.json"
        mcp_json = plugin / ".mcp.json"

        if not plugin_json.is_file():
            errors.append(f"{name}: missing {plugin_json.relative_to(root)}")
            continue
        try:
            pj = _load_json(plugin_json)
        except ValueError as exc:
            errors.append(f"{name}: cannot parse {plugin_json.relative_to(root)}: {exc}")
            continue

        if pj.get("name") != name:
            errors.append(f"{name}: plugin.json name={pj.get('name')!r} != dir name {name!r}")

        if not mcp_json.is_file():
            errors.append(f"{name}: missing {mcp_json.relative_to(root)}")
        else:
            try:
                mj = _load_json(mcp_json)
            except ValueError as exc:
                errors.append(f"{name}: cannot parse {mcp_json.relative_to(root)}: {exc}")
                mj = None
            if mj is not None:
                servers = mj.get("mcpServers", {})
                if list(servers) != [name]:
                    errors.append(
                        f"{name}: .mcp.json server keys {list(servers)} != [{name!r}]"
                    )
                for key, spec in servers.items():
                    command = spec.get("command", "")
                    command_path = (plugin / command).resolve()
                    if not command_path.is_file():
                        errors.append(
                            f"{name}: .mcp.json server {key!r} command {command!r} "
                            f"-> missing file {command_path}"
                        )

        bundled = plugin / "skills" / name / "SKILL.md"
        canonical = skills_dir / name / "SKILL.md"
        if bundled.is_file():
            if not canonical.is_file():
                errors.append(
                    f"{name}: bundled skill exists but canonical "
                    f"skills/{name}/SKILL.md is missing"
                )
            elif bundled.read_bytes() != canonical.read_bytes():
                errors.append(
                    f"{name}: bundled plugin SKILL.md differs from canonical "
                    f"skills/{name}/SKILL.md (regenerate: "
                    f"cp 'skills/{name}/SKILL.md' 'plugins/{name}/skills/{name}/SKILL.md')"
                )

        if name not in PLUGINS_WITHOUT_PACKAGE:
            pyproject = packages_dir / name / "pyproject.toml"
            if pyproject.is_file():
                pkg_version = _read_pyproject_version(pyproject)
                if pkg_version is not None and pj.get("version") != pkg_version:
                    errors.append(
                        f"{name}: plugin.json version={pj.get('version')!r} != "
                        f"packages/{name}/pyproject.toml version={pkg_version!r}"
                    )

    referenced: list[str] = []
    submit = root / "module-submit.json"
    if submit.is_file():
        try:
            data = _load_json(submit)
        except ValueError as exc:
            errors.append(f"module-submit.json: cannot parse: {exc}")
            data = {}
        for module in data.get("modules", []):
            for rel in module.get("paths", []):
                referenced.append(rel)
                if not (root / rel).exists():
                    warnings.append(
                        f"module-submit.json: module {module.get('id')!r} "
                        f"path does not exist: {rel}"
                    )
    else:
        warnings.append("module-submit.json not found")

    for base in ("packages", "plugins", "skills"):
        base_dir = root / base
        if not base_dir.is_dir():
            continue
        for child in sorted(d for d in base_dir.iterdir() if d.is_dir()):
            rel = f"{base}/{child.name}"
            if not any(ref.startswith(rel) for ref in referenced):
                warnings.append(
                    f"{rel}/ is not referenced by any module-submit.json module"
                )

    _check_course_vocab(root, errors)
    _check_gateway_port(root, errors)
    _check_model_registry(root, errors)

    return errors, warnings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Check cross-surface entrypoint drift.")
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repo root to check (default: this repo)",
    )
    parser.add_argument("--json", action="store_true", help="emit JSON instead of text")
    args = parser.parse_args(argv)
    root = args.root.resolve()

    errors, warnings = run_checks(root)

    if args.json:
        print(
            json.dumps(
                {"ok": not errors, "errors": errors, "warnings": warnings},
                ensure_ascii=False,
                indent=2,
            )
        )
    else:
        for warning in warnings:
            print(f"warning: {warning}", file=sys.stderr)
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
        if errors:
            print(
                f"\nentrypoint drift: {len(errors)} error(s), {len(warnings)} warning(s)",
                file=sys.stderr,
            )
        else:
            print(f"entrypoint drift: OK ({len(warnings)} warning(s))")

    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
