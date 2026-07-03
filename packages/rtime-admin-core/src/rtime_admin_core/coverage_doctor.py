# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Config-coverage doctor — measure how much of the live env surface the schema owns.

The audit (docs/audit/codebase-audit-2026-07.zh-CN.md §二) found ~220 env vars
read once at startup, scattered across apps/ + packages/ + deploy/. The P2 config
收编 migrates them, module by module, onto the ``rtime-config`` schemas registered
in :func:`rtime_admin_core.default_registry`. This module measures that migration
and turns it into a ratchet (see ``tests/test_config_coverage.py``):

  Collector A (USED_ENV)      — AST-scan the repo for the env keys code actually
                                reads: ``os.getenv("X")`` / ``os.environ.get("X")``
                                / ``os.environ["X"]`` with a string-literal key.
                                Records ``file:line`` for every occurrence.
  Collector B (REGISTERED_ENV)— from the registry, the env names each field
                                actually ACCEPTS: its ``x-env-aliases`` if declared,
                                else the pydantic-settings-derived
                                ``<env_prefix><FIELD>`` name.

  coverage = REGISTERED_ENV ∩ USED_ENV ;  uncovered = USED_ENV − REGISTERED_ENV.

Run ``python -m rtime_admin_core.coverage_doctor`` for the X/Y modules, Z/N fields
report and the uncovered list. Zero functional change — measurement only.
"""

from __future__ import annotations

import ast
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .registry import Registry, default_registry

# --- repo layout --------------------------------------------------------------
# Scanned top-level dirs (audit scope: the code + deploy glue that reads env).
SCAN_DIRS = ("apps", "packages", "deploy")
# Never descend into these anywhere in the tree (vendored/venv/generated/tests).
EXCLUDE_DIR_NAMES = frozenset(
    {
        ".venv",
        "venv",
        "site-packages",
        "__pycache__",
        ".git",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
        "build",
        "dist",
    }
)
# A path component OR filename matching these marks a test file/dir -> excluded
# from USED_ENV (a test's ``os.getenv`` is scaffolding, not a live config point).
TEST_MARKERS = ("tests", "test")


def repo_root() -> Path:
    """Locate the repo root by walking up until a scanned dir + .git is found.

    Falls back to four parents up from this file (…/packages/rtime-admin-core/src/
    rtime_admin_core/coverage_doctor.py -> repo root) if the walk finds nothing —
    keeps the doctor usable from an odd CWD or an installed copy.
    """
    here = Path(__file__).resolve()
    for parent in here.parents:
        if (parent / ".git").exists() and (parent / "apps").is_dir():
            return parent
    return here.parents[3]


def _is_test_path(path: Path, root: Path) -> bool:
    """True if ``path`` is inside a tests dir or is a test module."""
    rel_parts = path.relative_to(root).parts
    for part in rel_parts[:-1]:
        if part in TEST_MARKERS:
            return True
    name = path.name
    return (
        name.startswith("test_") or name == "conftest.py" or name.endswith("_test.py")
    )


# --- Collector A: env keys the code reads (AST) -------------------------------
@dataclass
class EnvUse:
    """One env-read site: ``KEY`` read at ``file:line``."""

    key: str
    file: str  # repo-relative
    line: int

    def where(self) -> str:
        return f"{self.file}:{self.line}"


def _const_str(node: ast.AST) -> str | None:
    """The string value of a literal ``ast`` node, or None if not a str literal."""
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    return None


class _EnvVisitor(ast.NodeVisitor):
    """Collect string-literal env keys from getenv / environ.get / environ[...]."""

    def __init__(self, rel: str) -> None:
        self.rel = rel
        self.uses: list[EnvUse] = []

    def _record(self, key: str | None, lineno: int) -> None:
        if key:
            self.uses.append(EnvUse(key=key, file=self.rel, line=lineno))

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802 (ast API)
        func = node.func
        if isinstance(func, ast.Attribute) and node.args:
            attr = func.attr
            # os.getenv("X") / getenv("X")
            if attr == "getenv":
                self._record(_const_str(node.args[0]), node.lineno)
            # os.environ.get("X")
            elif attr == "get" and _is_environ(func.value):
                self._record(_const_str(node.args[0]), node.lineno)
        # bare getenv("X") (from os import getenv)
        elif isinstance(func, ast.Name) and func.id == "getenv" and node.args:
            self._record(_const_str(node.args[0]), node.lineno)
        self.generic_visit(node)

    def visit_Subscript(self, node: ast.Subscript) -> None:  # noqa: N802
        # os.environ["X"] / environ["X"]
        if _is_environ(node.value):
            self._record(_const_str(node.slice), node.lineno)
        self.generic_visit(node)


def _is_environ(node: ast.AST) -> bool:
    """True if ``node`` denotes ``os.environ`` or a bare ``environ``."""
    if isinstance(node, ast.Attribute) and node.attr == "environ":
        return True
    return isinstance(node, ast.Name) and node.id == "environ"


def scan_used_env(root: Path | None = None) -> dict[str, list[EnvUse]]:
    """AST-scan the repo -> ``{env_key: [EnvUse, ...]}`` (sorted, deduped sites).

    Scans :data:`SCAN_DIRS`, skipping :data:`EXCLUDE_DIR_NAMES` and test files.
    A file that fails to parse (syntax error / py2 leftover) is skipped with a
    note on stderr rather than aborting the whole scan.
    """
    root = root or repo_root()
    out: dict[str, list[EnvUse]] = {}
    for top in SCAN_DIRS:
        base = root / top
        if not base.is_dir():
            continue
        for py in base.rglob("*.py"):
            if any(part in EXCLUDE_DIR_NAMES for part in py.parts):
                continue
            if _is_test_path(py, root):
                continue
            rel = str(py.relative_to(root))
            try:
                tree = ast.parse(py.read_text(encoding="utf-8"), filename=rel)
            except (SyntaxError, UnicodeDecodeError) as exc:  # pragma: no cover
                print(
                    f"coverage_doctor: skip unparseable {rel}: {exc}", file=sys.stderr
                )
                continue
            visitor = _EnvVisitor(rel)
            visitor.visit(tree)
            for use in visitor.uses:
                out.setdefault(use.key, []).append(use)
    # stable order: by first occurrence site
    for key in out:
        out[key].sort(key=lambda u: (u.file, u.line))
    return out


# --- Collector B: env names the schema accepts --------------------------------
def accepted_env_names(model: Any, field_name: str, prop: dict[str, Any]) -> list[str]:
    """The env name(s) that populate ``field_name`` on ``model``.

    Mirrors :mod:`rtime_config.fields`: an ``x-env-aliases`` list (declared via
    ``env_aliases=[…]``) IS the exact accepted surface; when absent,
    pydantic-settings derives ``<env_prefix><FIELD>`` (upper-cased — settings env
    matching is case-insensitive but we normalise upper so it aligns with the
    literal keys code uses).
    """
    aliases = prop.get("x-env-aliases")
    if aliases:
        return list(aliases)
    prefix = ""
    try:
        prefix = model.model_config.get("env_prefix", "") or ""
    except Exception:  # pragma: no cover - defensive
        prefix = ""
    return [f"{prefix}{field_name}".upper()]


@dataclass
class ModuleCoverage:
    """Per-module field/env coverage detail."""

    module: str
    field_env: dict[str, list[str]] = field(default_factory=dict)  # field -> env names

    @property
    def all_env(self) -> set[str]:
        out: set[str] = set()
        for names in self.field_env.values():
            out.update(names)
        return out


def _ensure_qq_on_path(root: Path | None = None) -> None:
    """Best-effort: put the qq-bridge app on sys.path so its module can register.

    admin-core stays a LEAF — it never depends on the app. This only makes the app
    IMPORTABLE when running against a repo checkout (mirrors the test bootstrap and
    apps/qq-bridge/tests/conftest.py), so the doctor's standalone CLI reports the
    full baseline (qq is the migrated exemplar) instead of the qq-less degraded
    count. A no-op when qq is already importable or the app dir is absent.
    """
    import importlib.util

    if importlib.util.find_spec("qq_bridge") is not None:
        return
    app = (root or repo_root()) / "apps" / "qq-bridge"
    if app.is_dir() and str(app) not in sys.path:
        sys.path.insert(0, str(app))


def _ensure_web_chat_on_path(root: Path | None = None) -> None:
    """Best-effort: put the web-chat app on sys.path so its module can register.

    Same rationale as :func:`_ensure_qq_on_path` — admin-core stays a leaf; this
    only makes ``web_chat.config`` importable against a repo checkout so the doctor
    counts the web-chat module. No-op when already importable or the dir is absent.
    """
    import importlib.util

    if importlib.util.find_spec("web_chat") is not None:
        return
    app = (root or repo_root()) / "apps" / "web-chat"
    if app.is_dir() and str(app) not in sys.path:
        sys.path.insert(0, str(app))


def _ensure_feishu_on_path(root: Path | None = None) -> None:
    """Best-effort: put the feishu-bridge app on sys.path so its module can register.

    Same rationale as :func:`_ensure_qq_on_path` — admin-core stays a leaf; this only
    makes ``feishu_config`` importable against a repo checkout so the doctor counts
    the feishu module. The feishu-bridge is a flat module layout, so its app dir is
    the import root. No-op when already importable or the dir is absent. Also puts
    ``packages/rtime-config`` on the path (feishu_config imports rtime_config) so the
    probe can import it standalone even without an editable install / PYTHONPATH.
    """
    import importlib.util

    if importlib.util.find_spec("feishu_config") is not None:
        return
    base = root or repo_root()
    app = base / "apps" / "feishu-bridge"
    config_src = base / "packages" / "rtime-config" / "src"
    for path in (app, config_src):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))


def _ensure_assistant_gateway_on_path(root: Path | None = None) -> None:
    """Best-effort: put the assistant-gateway app on sys.path so its module can register.

    Same rationale as :func:`_ensure_qq_on_path` — admin-core stays a leaf; this only
    makes ``gateway_config_schema`` importable against a repo checkout so the doctor
    counts the assistant-gateway module. The assistant-gateway is a flat module layout,
    so its app dir is the import root. No-op when already importable or the dir is
    absent. Also puts ``packages/rtime-config`` on the path (gateway_config_schema
    imports rtime_config) so the probe can import it standalone even without an
    editable install / PYTHONPATH.
    """
    import importlib.util

    if importlib.util.find_spec("gateway_config_schema") is not None:
        return
    base = root or repo_root()
    app = base / "apps" / "assistant-gateway"
    config_src = base / "packages" / "rtime-config" / "src"
    for path in (app, config_src):
        if path.is_dir() and str(path) not in sys.path:
            sys.path.insert(0, str(path))
def _ensure_ustc_kb_on_path(root: Path | None = None) -> None:
    """Best-effort: put the ustc-kb package src on sys.path so its schema imports.

    Same rationale as :func:`_ensure_qq_on_path` — admin-core stays a leaf; this only
    makes ``ustc_kb.config_schema`` importable against a repo checkout so the doctor
    counts the ustc-kb module. No-op when already importable or the dir is absent.
    """
    import importlib.util

    if importlib.util.find_spec("ustc_kb.config_schema") is not None:
        return
    src = (root or repo_root()) / "packages" / "ustc-kb" / "src"
    if src.is_dir() and str(src) not in sys.path:
        sys.path.insert(0, str(src))


def scan_registered_env(
    registry: Registry | None = None,
) -> tuple[dict[str, ModuleCoverage], set[str]]:
    """From the registry -> per-module coverage detail + the flat REGISTERED_ENV set.

    Uses ``default_registry(include_qq=True, …)`` by default so every migrated opt-in
    module that is importable counts (qq is the migrated exemplar; web-chat / feishu /
    qq-selfheal / ustc-kb follow). Falls back to fewer modules if an app/package is not
    importable (admin-core stays a leaf; each opt-in is independently guarded).
    """
    if registry is None:
        _ensure_qq_on_path()
        _ensure_web_chat_on_path()
        _ensure_feishu_on_path()
        _ensure_assistant_gateway_on_path()
        # Include every opt-in app module that is importable so coverage reflects
        # reality; each is independently guarded (admin-core stays a leaf).
        _ensure_ustc_kb_on_path()
        # Include every opt-in app/package module that is importable so coverage
        # reflects reality; each is independently guarded (admin-core stays a leaf).
        kwargs: dict[str, bool] = {}
        for flag, probe in (
            ("include_qq", "qq_bridge.config"),
            ("include_web_chat", "web_chat.config"),
            ("include_feishu", "feishu_config"),
            ("include_assistant_gateway", "gateway_config_schema"),
            ("include_qq_selfheal", "qq_bridge.selfheal_config"),
            ("include_ustc_kb", "ustc_kb.config_schema"),
        ):
            try:
                __import__(probe)
                kwargs[flag] = True
            except ModuleNotFoundError:
                pass
        registry = default_registry(**kwargs)
    per_module: dict[str, ModuleCoverage] = {}
    registered: set[str] = set()
    for module in registry.list_modules():
        model = registry.model(module)
        schema = registry.get_schema(module)
        cov = ModuleCoverage(module=module)
        for fld, prop in schema.get("properties", {}).items():
            names = accepted_env_names(model, fld, prop)
            cov.field_env[fld] = names
            registered.update(names)
        per_module[module] = cov
    return per_module, registered


# --- report -------------------------------------------------------------------
@dataclass
class CoverageReport:
    used: dict[str, list[EnvUse]]
    registered: set[str]
    per_module: dict[str, ModuleCoverage]

    @property
    def used_keys(self) -> set[str]:
        return set(self.used)

    @property
    def covered(self) -> set[str]:
        """USED env keys that a registered field accepts."""
        return self.used_keys & self.registered

    @property
    def uncovered(self) -> set[str]:
        """USED env keys no registered field accepts (the migration backlog)."""
        return self.used_keys - self.registered

    def field_count(self) -> tuple[int, int]:
        """(Z, N): registered fields whose env name(s) are used / total fields."""
        n = 0
        z = 0
        used = self.used_keys
        for cov in self.per_module.values():
            for names in cov.field_env.values():
                n += 1
                if used & set(names):
                    z += 1
        return z, n

    def module_count(self) -> tuple[int, int]:
        """(X, Y): modules with >=1 field whose env is used / total modules."""
        y = len(self.per_module)
        used = self.used_keys
        x = 0
        for cov in self.per_module.values():
            if used & cov.all_env:
                x += 1
        return x, y


def build_report(root: Path | None = None) -> CoverageReport:
    root = root or repo_root()
    used = scan_used_env(root)
    per_module, registered = scan_registered_env()
    return CoverageReport(used=used, registered=registered, per_module=per_module)


def format_report(report: CoverageReport) -> str:
    z, n = report.field_count()
    x, y = report.module_count()
    lines: list[str] = []
    lines.append("=== rtime config coverage doctor ===")
    lines.append(f"modules covered: {x}/{y}")
    lines.append(f"fields covered:  {z}/{n}")
    lines.append(
        f"env keys: {len(report.covered)} covered / "
        f"{len(report.used_keys)} used ({len(report.uncovered)} uncovered)"
    )
    lines.append("")
    lines.append(f"--- uncovered env keys ({len(report.uncovered)}) ---")
    for key in sorted(report.uncovered):
        first = report.used[key][0].where()
        extra = len(report.used[key]) - 1
        suffix = f"  (+{extra} more)" if extra else ""
        lines.append(f"  {key}  @ {first}{suffix}")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    report = build_report()
    print(format_report(report))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
