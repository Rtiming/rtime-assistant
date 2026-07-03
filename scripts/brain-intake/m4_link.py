#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M4 vault local-view reconciliation.

The Obsidian vault is synced across clients. Materialized course views are
syncable by default, while symlink views are local by default because their
targets differ on Mac, Windows, and Orange Pi. This script keeps the shared
view manifest and local ``.stignore`` entries together.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
import shutil
import subprocess
from typing import Any

import intake_common as ic


VIEW_MANIFEST_SCHEMA = "rtime-course-view-v1"
DEFAULT_MANIFEST_REL = Path("80 系统") / "course-view-manifest.json"

ORIGINAL_VIEW_GLOBS = [
    "*.pdf",
    "*.ppt",
    "*.pptx",
    "*.doc",
    "*.docx",
    "*.jpg",
    "*.jpeg",
    "*.png",
    "*.gif",
    "*.md",
]
DERIVED_VIEW_EXCLUDES = [
    "images/**",
    "text/**",
    "md/**",
    "pdf/**",
    "source/**",
    "**/images/**",
    "**/text/**",
    "**/md/**",
    "**/pdf/**",
    "**/source/**",
]

STIGNORE_BEGIN = "// BEGIN rtime local Obsidian view entries"
STIGNORE_END = "// END rtime local Obsidian view entries"
LEGACY_STIGNORE_HEADERS = {
    "// Local view symlinks. Targets differ between Mac, Windows, and Orange Pi.",
    "// Local Obsidian view entries. Targets/content differ between Mac, Windows, and Orange Pi.",
}

VIEW_LINKS: list[dict[str, Any]] = [
    {
        "vault_rel": "01 每日",
        "brain_rel": "notes/daily",
        "mode": "symlink",
        "sync": False,
    },
    {
        "vault_rel": "10 课程/2026春/固体物理/课件",
        "brain_rel": "knowledge/courses/solid-state-physics/slides",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/先进光子物理/讲义",
        "mode": "absent",
        "reason": "advanced-photonics current lecture PDFs are classroom slide decks and are exposed under 课件",
    },
    {
        "vault_rel": "10 课程/2026春/先进光子物理/习题",
        "brain_rel": "knowledge/courses/advanced-photonics/exercises",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/先进光子物理/课件",
        "brain_rel": "knowledge/courses/advanced-photonics/slides",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/热力学与统计物理/试卷",
        "brain_rel": "knowledge/courses/thermal-statistical-physics/exams",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/热力学与统计物理/答案",
        "brain_rel": "knowledge/courses/thermal-statistical-physics/solutions",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/热力学与统计物理/文稿",
        "brain_rel": "knowledge/courses/thermal-statistical-physics/md",
        "mode": "materialize",
        "include_globs": ["**/*.md", "*.md"],
        "exclude_globs": [],
        "prune": True,
    },
    {
        "vault_rel": "10 课程/2026春/热力学与统计物理/讲义",
        "brain_rel": "knowledge/courses/thermal-statistical-physics/lectures",
        "mode": "materialize",
        "include_globs": ORIGINAL_VIEW_GLOBS,
        "exclude_globs": DERIVED_VIEW_EXCLUDES,
        "prune": True,
    },
]

STIGNORE_HEADER = STIGNORE_BEGIN


def default_manifest() -> dict[str, Any]:
    return {
        "schema_version": VIEW_MANIFEST_SCHEMA,
        "default_mode": "materialize",
        "entries": VIEW_LINKS,
    }


def load_manifest(path: Path | None) -> dict[str, Any]:
    if path and path.exists():
        loaded = ic.read_json(path)
    else:
        loaded = default_manifest()
    entries = loaded.get("entries")
    if not isinstance(entries, list) or not entries:
        raise ValueError("view manifest must contain a non-empty entries list")
    schema = loaded.get("schema_version", VIEW_MANIFEST_SCHEMA)
    if schema != VIEW_MANIFEST_SCHEMA:
        raise ValueError(f"unsupported view manifest schema: {schema}")
    return loaded


def default_manifest_path(vault_root: Path) -> Path:
    return vault_root / DEFAULT_MANIFEST_REL


def _entry_brain_rels(item: dict[str, Any]) -> list[str]:
    if "brain_rels" in item:
        rels = item["brain_rels"]
        if not isinstance(rels, list) or not rels:
            raise ValueError("brain_rels must be a non-empty list")
        return [str(rel) for rel in rels]
    if "brain_rel" in item:
        return [str(item["brain_rel"])]
    return []


def _entry_mode(item: dict[str, Any], manifest: dict[str, Any]) -> str:
    mode = item.get("mode") or manifest.get("default_mode") or "materialize"
    if mode not in {"symlink", "materialize", "absent"}:
        raise ValueError(f"unsupported view entry mode: {mode}")
    return mode


def _entry_sync(item: dict[str, Any], manifest: dict[str, Any]) -> bool:
    if "sync" in item:
        return bool(item["sync"])
    mode = _entry_mode(item, manifest)
    return mode == "materialize"


def stignore_lines(entries: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    ignored = [
        f"/{item['vault_rel']}"
        for item in entries
        if _entry_mode(item, manifest) != "absent" and not _entry_sync(item, manifest)
    ]
    if not ignored:
        return []
    return [
        STIGNORE_BEGIN,
        "// Entries below are local-only views, usually symlinks into brain.",
        *ignored,
        STIGNORE_END,
    ]


def _rewrite_stignore_lines(existing: list[str], entries: list[dict[str, Any]], manifest: dict[str, Any]) -> list[str]:
    managed_paths = {f"/{item['vault_rel']}" for item in entries}
    desired = stignore_lines(entries, manifest)
    rewritten: list[str] = []
    in_block = False
    for line in existing:
        if line == STIGNORE_BEGIN:
            in_block = True
            continue
        if line == STIGNORE_END:
            in_block = False
            continue
        if in_block:
            continue
        if line in LEGACY_STIGNORE_HEADERS:
            continue
        if line in managed_paths:
            continue
        rewritten.append(line)
    while rewritten and rewritten[-1] == "":
        rewritten.pop()
    if desired:
        if rewritten:
            rewritten.append("")
        rewritten.extend(desired)
    return rewritten


def _strip_extended_prefix(text: str) -> str:
    # os.readlink on Windows returns targets with the \\?\ (or \\?\UNC\)
    # extended-length prefix; strip it so a symlink pointing at the configured
    # brain target is not falsely flagged as mismatched.
    if text.startswith("\\\\?\\UNC\\"):
        return "\\\\" + text[len("\\\\?\\UNC\\"):]
    if text.startswith("\\\\?\\"):
        return text[len("\\\\?\\"):]
    return text


def _same_path(left: Path, right: Path) -> bool:
    left_norm = os.path.normcase(os.path.normpath(_strip_extended_prefix(str(left))))
    right_norm = os.path.normcase(os.path.normpath(_strip_extended_prefix(str(right))))
    return left_norm == right_norm


def _link_points_to(link: Path, target: Path) -> bool:
    if not link.is_symlink():
        return False
    current = Path(os.readlink(link))
    return _same_path(current, target)


def _create_dir_link(link: Path, target: Path) -> None:
    if os.name == "nt":
        try:
            os.symlink(str(target), str(link), target_is_directory=True)
            return
        except OSError:
            # Fallback for older Windows setups where Python symlink creation is
            # blocked despite an administrative shell.
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/D", str(link), str(target)],
                check=False,
                capture_output=True,
                text=True,
            )
            if result.returncode != 0:
                raise OSError(result.stderr.strip() or result.stdout.strip())
            return
    os.symlink(str(target), str(link))


def _summary(actions: list[dict[str, Any]]) -> dict[str, int]:
    keys = sorted({action["action"] for action in actions})
    return {key: sum(1 for action in actions if action["action"] == key) for key in keys}


def _ensure_lexically_inside(root: Path, path: Path) -> None:
    root_abs = Path(os.path.abspath(root))
    path_abs = Path(os.path.abspath(path))
    if path_abs != root_abs and root_abs not in path_abs.parents:
        raise ValueError(f"path escapes root: {path_abs} not under {root_abs}")


def _matches_any(patterns: list[str], relative: str, name: str) -> bool:
    return any(fnmatch.fnmatch(relative, pattern) or fnmatch.fnmatch(name, pattern) for pattern in patterns)


def _iter_materialized_sources(target: Path, item: dict[str, Any]) -> list[Path]:
    include_globs = item.get("include_globs") or ORIGINAL_VIEW_GLOBS
    exclude_globs = item.get("exclude_globs") or []
    files: list[Path] = []
    if target.is_file():
        rel = target.name
        if _matches_any(include_globs, rel, target.name) and not _matches_any(exclude_globs, rel, target.name):
            return [target]
        return []
    for root, dirs, names in os.walk(target):
        root_path = Path(root)
        rel_root = "" if root_path == target else root_path.relative_to(target).as_posix()
        kept_dirs = []
        for dirname in sorted(dirs):
            rel_dir = f"{rel_root}/{dirname}".strip("/")
            if _matches_any(exclude_globs, f"{rel_dir}/__dir__", dirname):
                continue
            kept_dirs.append(dirname)
        dirs[:] = kept_dirs
        for name in sorted(names):
            source = root_path / name
            rel = f"{rel_root}/{name}".strip("/")
            if not _matches_any(include_globs, rel, name):
                continue
            if _matches_any(exclude_globs, rel, name):
                continue
            files.append(source)
    return files


def _file_current(source: Path, destination: Path, *, verify_hash: bool = False) -> bool:
    if not destination.is_file():
        return False
    try:
        if source.stat().st_size != destination.stat().st_size:
            return False
        if not verify_hash:
            return True
        return ic.sha256_file(source) == ic.sha256_file(destination)
    except OSError:
        return False


def _copy_reading_file(source: Path, target: Path) -> None:
    try:
        shutil.copy2(source, target)
        return
    except PermissionError:
        shutil.copyfile(source, target)
        try:
            shutil.copymode(source, target)
        except OSError:
            pass


def _materialize_action(item: dict[str, Any], source_roots: list[Path], view_path: Path) -> dict[str, Any]:
    desired_files = []
    replacing_symlink = view_path.is_symlink()
    verify_hash = bool(item.get("verify_hash", False))
    duplicate_relatives: list[dict[str, str]] = []
    seen_relatives: dict[str, str] = {}
    for source_root in source_roots:
        for source in _iter_materialized_sources(source_root, item):
            relative = source.name if source_root.is_file() else source.relative_to(source_root).as_posix()
            if relative in seen_relatives:
                duplicate_relatives.append(
                    {
                        "relative": relative,
                        "first_source": seen_relatives[relative],
                        "duplicate_source": str(source),
                    }
                )
                continue
            seen_relatives[relative] = str(source)
            destination = view_path / relative
            status = (
                "copy"
                if replacing_symlink
                else "current"
                if _file_current(source, destination, verify_hash=verify_hash)
                else "copy"
            )
            desired_files.append(
                {
                    "relative": relative,
                    "source": str(source),
                    "target": str(destination),
                    "status": status,
                }
            )

    stale_files: list[str] = []
    if item.get("prune", True) and view_path.is_dir() and not view_path.is_symlink():
        desired_paths = {Path(file["target"]) for file in desired_files}
        for existing in sorted(view_path.rglob("*")):
            if existing.is_file() and existing not in desired_paths:
                stale_files.append(str(existing))

    return {
        "action": "materialize",
        "path": str(view_path),
        "targets": [str(source_root) for source_root in source_roots],
        "vault_rel": item["vault_rel"],
        "brain_rels": _entry_brain_rels(item),
        "replace_symlink": replacing_symlink,
        "prune": bool(item.get("prune", True)),
        "verify_hash": verify_hash,
        "include_globs": item.get("include_globs") or ORIGINAL_VIEW_GLOBS,
        "exclude_globs": item.get("exclude_globs") or [],
        "duplicate_relatives": duplicate_relatives,
        "files": desired_files,
        "stale_files": stale_files,
        "counts": {
            "desired": len(desired_files),
            "copy": sum(1 for file in desired_files if file["status"] == "copy"),
            "current": sum(1 for file in desired_files if file["status"] == "current"),
            "stale": len(stale_files),
        },
    }


def build_plan(
    brain_root: Path,
    vault_root: Path,
    run_dir: Path,
    manifest: dict[str, Any] | None = None,
) -> dict:
    manifest = manifest or default_manifest()
    entries = manifest["entries"]
    actions: list[dict[str, Any]] = []
    for item in entries:
        link = vault_root / item["vault_rel"]
        mode = _entry_mode(item, manifest)

        if mode == "absent":
            if link.exists() or link.is_symlink():
                actions.append(
                    {
                        "action": "remove_view_path",
                        "path": str(link),
                        "vault_rel": item["vault_rel"],
                        "reason": item.get("reason", "view path is retired by manifest"),
                    }
                )
            continue

        brain_rels = _entry_brain_rels(item)
        source_roots = [brain_root / rel for rel in brain_rels]
        if not source_roots:
            actions.append(
                {
                    "action": "hold",
                    "path": str(link),
                    "reason": "view entry has no brain_rel or brain_rels",
                }
            )
            continue

        missing_targets = [str(target) for target in source_roots if not target.exists()]
        if missing_targets:
            actions.append(
                {
                    "action": "hold",
                    "path": str(link),
                    "targets": [str(target) for target in source_roots],
                    "missing_targets": missing_targets,
                    "reason": "brain target does not exist",
                }
            )
        elif mode == "materialize":
            if link.exists() and not link.is_dir() and not link.is_symlink():
                actions.append(
                    {
                        "action": "hold",
                        "path": str(link),
                        "targets": [str(target) for target in source_roots],
                        "reason": "vault path exists and cannot be materialized as a directory",
                    }
                )
            else:
                action = _materialize_action(item, source_roots, link)
                if action["duplicate_relatives"]:
                    actions.append(
                        {
                            "action": "hold",
                            "path": str(link),
                            "targets": [str(target) for target in source_roots],
                            "duplicate_relatives": action["duplicate_relatives"],
                            "reason": "multiple source roots contain the same relative file name",
                        }
                    )
                elif action["replace_symlink"] or action["counts"]["copy"] > 0 or action["counts"]["stale"] > 0:
                    actions.append(action)
        elif len(source_roots) != 1:
            actions.append(
                {
                    "action": "hold",
                    "path": str(link),
                    "targets": [str(target) for target in source_roots],
                    "reason": "symlink view supports exactly one brain target",
                }
            )
        elif not link.exists() and not link.is_symlink():
            target = source_roots[0]
            actions.append(
                {
                    "action": "symlink",
                    "target": str(target),
                    "link": str(link),
                    "vault_rel": item["vault_rel"],
                    "brain_rel": brain_rels[0],
                }
            )
        elif _link_points_to(link, source_roots[0]):
            pass
        else:
            actions.append(
                {
                    "action": "hold",
                    "path": str(link),
                    "target": str(source_roots[0]),
                    "reason": "vault path exists and is not the expected local view link",
                }
            )
    stignore = vault_root / ".stignore"
    existing = ic.read_text(stignore).splitlines() if stignore.exists() else []
    desired = _rewrite_stignore_lines(existing, entries, manifest)
    if desired != existing:
        actions.append(
            {
                "action": "stignore_rewrite",
                "target": str(stignore),
                "lines": desired,
                "ignored_paths": [line for line in desired if line.startswith("/") and not line.startswith("//")],
            }
        )
    return {
        "run_id": ic.RUN_ID,
        "generated_at": ic.utc_now(),
        "manifest_schema": manifest.get("schema_version", VIEW_MANIFEST_SCHEMA),
        "manifest_entries": len(entries),
        "actions": actions,
        "summary": _summary(actions),
    }


# KEEP IN SYNC: packages/brain-docpack/src/brain_docpack/course_intake.py
# (OBSIDIAN_CATEGORY_FOLDERS / COURSE_VIEW_SUBDIRS) and
# docs/maintainability-standards.zh-CN.md. The Chinese view vocab below mirrors
# the visible names mapped there; changing the category list + Chinese vocab
# must update both files + the doc.
COURSE_VIEW_NAMES = {"课件", "讲义", "试卷", "答案", "习题", "文稿", "参考资料"}


def _issue(severity: str, vault_rel: str, message: str, **extra: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "severity": severity,
        "vault_rel": vault_rel,
        "message": message,
    }
    payload.update(extra)
    return payload


def verify_views(
    brain_root: Path,
    vault_root: Path,
    run_dir: Path,
    manifest: dict[str, Any],
) -> dict[str, Any]:
    """Validate that the vault-visible course view matches the manifest."""

    errors: list[dict[str, Any]] = []
    warnings: list[dict[str, Any]] = []
    entries: list[dict[str, Any]] = []

    plan = build_plan(brain_root, vault_root, run_dir, manifest=manifest)
    for action in plan["actions"]:
        severity = "warning" if action["action"] == "stignore_rewrite" else "error"
        bucket = warnings if severity == "warning" else errors
        bucket.append(
            _issue(
                severity,
                action.get("vault_rel") or action.get("path") or action.get("target", ""),
                "view is not reconciled; run m4_link.py --plan/--apply first",
                action=action["action"],
                counts=action.get("counts", {}),
                reason=action.get("reason", ""),
            )
        )

    existing_stignore = ic.read_text(vault_root / ".stignore").splitlines() if (vault_root / ".stignore").exists() else []
    actual_ignored_paths = {line for line in existing_stignore if line.startswith("/") and not line.startswith("//")}
    desired_stignore = _rewrite_stignore_lines(
        existing_stignore,
        manifest["entries"],
        manifest,
    )
    ignored_paths = {line for line in desired_stignore if line.startswith("/") and not line.startswith("//")}

    for item in manifest["entries"]:
        vault_rel = str(item["vault_rel"])
        view_path = vault_root / vault_rel
        mode = _entry_mode(item, manifest)
        sync = _entry_sync(item, manifest)
        record: dict[str, Any] = {
            "vault_rel": vault_rel,
            "mode": mode,
            "sync": sync,
            "exists": view_path.exists() or view_path.is_symlink(),
        }

        if vault_rel.startswith("10 课程/"):
            view_name = Path(vault_rel).name
            if view_name not in COURSE_VIEW_NAMES:
                errors.append(
                    _issue(
                        "error",
                        vault_rel,
                        "course view name is outside the controlled Obsidian vocabulary",
                        allowed=sorted(COURSE_VIEW_NAMES),
                    )
                )
            if "source" in Path(vault_rel).parts:
                errors.append(
                    _issue(
                        "error",
                        vault_rel,
                        "source-only folders such as slides/source must not be exposed as default Obsidian views",
                    )
                )

        if mode == "absent":
            if view_path.exists() or view_path.is_symlink():
                errors.append(_issue("error", vault_rel, "retired view path still exists"))
            entries.append(record)
            continue

        brain_rels = _entry_brain_rels(item)
        source_roots = [brain_root / rel for rel in brain_rels]
        missing_targets = [str(target) for target in source_roots if not target.exists()]
        if missing_targets:
            errors.append(
                _issue(
                    "error",
                    vault_rel,
                    "manifest points to missing brain target",
                    missing_targets=missing_targets,
                )
            )

        if not sync and f"/{vault_rel}" not in ignored_paths:
            errors.append(
                _issue(
                    "error",
                    vault_rel,
                    "sync:false view is missing from managed .stignore block",
                )
            )
        if sync and f"/{vault_rel}" in actual_ignored_paths:
            errors.append(
                _issue(
                    "error",
                    vault_rel,
                    "syncable materialized view is incorrectly ignored by .stignore",
                )
            )

        if mode == "materialize":
            if not view_path.is_dir() or view_path.is_symlink():
                errors.append(
                    _issue(
                        "error",
                        vault_rel,
                        "materialized view must be a real vault directory, not a symlink or missing path",
                    )
                )
            desired_count = sum(len(_iter_materialized_sources(root, item)) for root in source_roots if root.exists())
            record["desired_files"] = desired_count
            if desired_count == 0:
                warnings.append(
                    _issue(
                        "warning",
                        vault_rel,
                        "materialized view has no matching source files",
                    )
                )
            if vault_rel.startswith("10 课程/") and Path(vault_rel).name != "文稿":
                excludes = set(item.get("exclude_globs") or [])
                missing_excludes = sorted(set(DERIVED_VIEW_EXCLUDES) - excludes)
                if missing_excludes:
                    warnings.append(
                        _issue(
                            "warning",
                            vault_rel,
                            "course material view does not list the full derived-folder exclude set",
                            missing_excludes=missing_excludes,
                        )
                    )
        elif mode == "symlink":
            if len(source_roots) != 1 or not _link_points_to(view_path, source_roots[0]):
                errors.append(
                    _issue(
                        "error",
                        vault_rel,
                        "symlink view does not point to the configured brain target",
                    )
                )

        entries.append(record)

    return {
        "ok": not errors,
        "summary": {
            "entries": len(entries),
            "errors": len(errors),
            "warnings": len(warnings),
            "pending_actions": len(plan["actions"]),
        },
        "errors": errors,
        "warnings": warnings,
        "entries": entries,
    }


def _remove_empty_dirs(root: Path) -> None:
    if not root.is_dir():
        return
    for directory in sorted((p for p in root.rglob("*") if p.is_dir()), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            pass


def _move_view_path_to_backup(path: Path, vault_root: Path, run_dir: Path) -> Path:
    _ensure_lexically_inside(vault_root, path)
    backup_root = run_dir / "backups" / "removed-view-paths"
    try:
        rel = Path(ic.rel_to(vault_root, path))
    except ValueError:
        rel = Path(path.name)
    dest = backup_root / rel
    base = dest
    counter = 1
    while dest.exists() or dest.is_symlink():
        dest = base.with_name(f"{base.name}.{counter}")
        counter += 1
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(path), str(dest))
    return dest


def apply_plan(plan: dict, vault_root: Path, run_dir: Path | None = None) -> dict:
    log = []
    for action in plan["actions"]:
        if action["action"] == "symlink":
            link = Path(action["link"])
            target = Path(action["target"])
            link.parent.mkdir(parents=True, exist_ok=True)
            if not link.exists() and not link.is_symlink():
                _create_dir_link(link, target)
                log.append({"status": "done", **action})
            else:
                log.append({"status": "skipped", **action})
        elif action["action"] == "materialize":
            path = Path(action["path"])
            _ensure_lexically_inside(vault_root, path)
            if path.is_symlink():
                if run_dir:
                    ic.backup_file(path, run_dir / "backups" / "m4-link", vault_root)
                path.unlink()
            elif path.exists() and not path.is_dir():
                log.append({"status": "held", **action, "reason": "materialize path is not a directory"})
                continue
            path.mkdir(parents=True, exist_ok=True)
            copied = 0
            current = 0
            pruned = 0
            for stale in action.get("stale_files", []):
                stale_path = Path(stale)
                _ensure_lexically_inside(path, stale_path)
                if stale_path.is_file() or stale_path.is_symlink():
                    stale_path.unlink()
                    pruned += 1
            for file_action in action["files"]:
                source = Path(file_action["source"])
                target = Path(file_action["target"])
                _ensure_lexically_inside(path, target)
                if file_action["status"] == "current" and _file_current(
                    source,
                    target,
                    verify_hash=bool(action.get("verify_hash", False)),
                ):
                    current += 1
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                _copy_reading_file(source, target)
                copied += 1
            _remove_empty_dirs(path)
            log.append(
                {
                    "status": "done",
                    **{k: v for k, v in action.items() if k != "files"},
                    "copied": copied,
                    "current": current,
                    "pruned": pruned,
                }
            )
        elif action["action"] == "stignore_update":
            target = Path(action["target"])
            lines = ic.read_text(target).splitlines() if target.exists() else []
            for line in action["missing"]:
                if line not in lines:
                    lines.append(line)
            ic.write_text(target, "\n".join(lines) + "\n")
            log.append({"status": "done", **action})
        elif action["action"] == "stignore_rewrite":
            target = Path(action["target"])
            lines = action.get("lines", [])
            ic.write_text(target, "\n".join(lines) + ("\n" if lines else ""))
            log.append(
                {
                    "status": "done",
                    "action": action["action"],
                    "target": action["target"],
                    "ignored_paths": action.get("ignored_paths", []),
                    "line_count": len(lines),
                }
            )
        elif action["action"] == "remove_view_path":
            path = Path(action["path"])
            if path.exists() or path.is_symlink():
                if not run_dir:
                    log.append({"status": "held", **action, "reason": "run_dir is required to back up removed view paths"})
                    continue
                backup_path = _move_view_path_to_backup(path, vault_root, run_dir)
                log.append({"status": "done", **action, "backup_path": str(backup_path)})
            else:
                log.append({"status": "skipped", **action})
        elif action["action"] == "hold":
            log.append({"status": "held", **action})
    return {"ok": True, "actions": log, "summary": {s: sum(1 for a in log if a["status"] == s) for s in sorted({a["status"] for a in log})}}


def main() -> int:
    p = ic.parser("M4 link")
    ic.add_plan_apply(p)
    p.add_argument("--verify", action="store_true", help="verify materialized Obsidian course views without writing")
    p.add_argument("--manifest", type=Path, help="view manifest JSON; defaults to vault/80 系统/course-view-manifest.json when present")
    args = p.parse_args()
    if args.verify and (args.plan or args.apply):
        p.error("--verify cannot be combined with --plan or --apply")
    brain_root = ic.resolve_path(args.brain_root)
    vault_root = ic.resolve_path(args.vault_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    manifest_path = args.manifest or default_manifest_path(vault_root)
    if args.verify:
        manifest = load_manifest(manifest_path)
        result = verify_views(brain_root, vault_root, run_dir, manifest)
        result["manifest_path"] = str(manifest_path) if manifest_path.exists() else "built-in"
        ic.write_json(run_dir / "M4-verify.json", result)
        ic.write_json(run_dir / "M4-log.json", {"ok": result["ok"], "mode": "verify", "summary": result["summary"]})
        summary = result["summary"]
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        exit_code = 0 if result["ok"] else 1
    elif args.apply:
        plan = ic.read_json(ic.require_approved_plan(args, "link-plan.json"))
        result = apply_plan(plan, vault_root, run_dir)
        ic.write_json(run_dir / "M4-log.json", result)
        summary = result["summary"]
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        exit_code = 0
    else:
        manifest = load_manifest(manifest_path)
        plan = build_plan(brain_root, vault_root, run_dir, manifest=manifest)
        plan["manifest_path"] = str(manifest_path) if manifest_path.exists() else "built-in"
        ic.write_json(run_dir / "link-plan.json", plan)
        ic.write_json(run_dir / "M4-log.json", {"ok": True, "mode": "plan", "summary": plan["summary"]})
        summary = plan["summary"]
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        exit_code = 0
    ic.markdown_report(
        run_dir / "M4-报告.md",
        "M4 互通报告",
        [("做了什么", [f"{k}: {v}" for k, v in summary.items()]), ("跳过什么", ["Zotero批量建条目未执行"]), ("异常", [])],
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
