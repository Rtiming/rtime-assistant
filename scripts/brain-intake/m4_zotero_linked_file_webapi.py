#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Plan/apply run-04 Zotero linked-file attachment repair via Web API.

This is deliberately separate from ``m4_zotero.py`` because it uses Zotero's
cloud Web API write path. It never uploads PDF bytes: each action creates a
child attachment item with ``linkMode=linked_file`` and
``path=attachments:<brain_path>``.
"""

from __future__ import annotations

import http.client
import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import intake_common as ic
import m4_zotero


DEFAULT_API_BASE = "https://api.zotero.org"
DEFAULT_ZOTERO_PROFILES_ROOT = Path.home() / "Library" / "Application Support" / "Zotero" / "Profiles"


def _read_reconcile_log(run_dir: Path, explicit: Path | None = None) -> dict[str, Any]:
    path = explicit or run_dir / "M4-zotero-reconcile-log.json"
    if not path.exists():
        raise FileNotFoundError(f"missing reconcile log: {path}")
    return ic.read_json(path)


def _db_copy_from_log(log: dict[str, Any], explicit: Path | None = None) -> Path:
    if explicit:
        return explicit
    raw = log.get("zotero_db_copy")
    if not raw:
        raise ValueError("reconcile log has no zotero_db_copy; pass --zotero-db-copy")
    return Path(raw)


def _attachment_payload(zotero_item_key: str, brain_path: str) -> dict[str, Any]:
    filename = Path(brain_path).name
    return {
        "itemType": "attachment",
        "linkMode": "linked_file",
        "title": filename,
        "path": f"attachments:{brain_path}",
        "contentType": "application/pdf",
        "parentItem": zotero_item_key,
    }


def _detect_library_metadata(db_copy: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    conn = sqlite3.connect(f"file:{db_copy}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        user = conn.execute("SELECT userID, name FROM users LIMIT 1").fetchone()
        if user:
            metadata["user_id"] = int(user["userID"])
            metadata["username"] = user["name"]
        library = conn.execute(
            """
            SELECT libraryID, type, editable, filesEditable, version
            FROM libraries
            WHERE type = 'user'
            ORDER BY libraryID
            LIMIT 1
            """
        ).fetchone()
        if library:
            metadata["library_id"] = int(library["libraryID"])
            metadata["type"] = library["type"]
            metadata["editable"] = bool(library["editable"])
            metadata["files_editable"] = bool(library["filesEditable"])
            metadata["version"] = int(library["version"])
    except sqlite3.DatabaseError:
        metadata["detection_error"] = "zotero_library_metadata_unavailable"
    finally:
        conn.close()
    return metadata


def _read_zotero_pref_value(prefs_text: str, pref_name: str) -> str | None:
    prefix = f'user_pref("{pref_name}", '
    for line in prefs_text.splitlines():
        line = line.strip()
        if not line.startswith(prefix) or not line.endswith(");"):
            continue
        raw = line[len(prefix):-2].strip()
        try:
            value = json.loads(raw)
        except json.JSONDecodeError:
            return None
        return str(value) if isinstance(value, str) else None
    return None


def _detect_zotero_profile_paths(brain_root: Path, profiles_root: Path = DEFAULT_ZOTERO_PROFILES_ROOT) -> dict[str, Any]:
    expected = str(brain_root)
    profiles: list[dict[str, Any]] = []
    if profiles_root.exists():
        for prefs in sorted(profiles_root.glob("*/prefs.js")):
            text = prefs.read_text(encoding="utf-8", errors="replace")
            base_path = _read_zotero_pref_value(text, "extensions.zotero.baseAttachmentPath")
            bbt_base_path = _read_zotero_pref_value(text, "extensions.zotero.translators.better-bibtex.baseAttachmentPath")
            data_dir = _read_zotero_pref_value(text, "extensions.zotero.dataDir")
            profiles.append({
                "prefs": str(prefs),
                "baseAttachmentPath": base_path,
                "betterBibTeX_baseAttachmentPath": bbt_base_path,
                "dataDir": data_dir,
                "base_matches_brain_root": base_path == expected,
                "betterBibTeX_base_matches_brain_root": bbt_base_path in (None, expected),
            })
    return {
        "expected_brain_root": expected,
        "profiles_root": str(profiles_root),
        "profiles": profiles,
        "matching_profile_count": sum(1 for profile in profiles if profile["base_matches_brain_root"]),
        "ok": any(profile["base_matches_brain_root"] for profile in profiles),
    }


def build_plan(
    brain_root: Path,
    run_dir: Path,
    reconcile_log: Path | None = None,
    zotero_db_copy: Path | None = None,
) -> dict[str, Any]:
    log = _read_reconcile_log(run_dir, reconcile_log)
    db_copy = _db_copy_from_log(log, zotero_db_copy)
    run04_items = m4_zotero.read_run04_collection_items(db_copy)
    holds = [h for h in log.get("holds", []) if h.get("brain_path")]

    actions: list[dict[str, Any]] = []
    plan_holds: list[dict[str, Any]] = []
    for hold in holds:
        brain_path = str(hold["brain_path"])
        item = run04_items.get(brain_path)
        source = brain_root / brain_path
        if not item:
            plan_holds.append({**hold, "reason": "run04_collection_item_not_found"})
            continue
        if item.get("zotero_linked_attachment_key"):
            plan_holds.append({**hold, "reason": "already_has_linked_attachment"})
            continue
        if not source.exists():
            plan_holds.append({**hold, "reason": "brain_pdf_missing"})
            continue
        expected_sha = str(hold.get("sha256") or item.get("sha256") or "")
        actual_sha = ic.sha256_file(source)
        if expected_sha and actual_sha != expected_sha:
            plan_holds.append({
                **hold,
                "reason": "brain_pdf_sha256_mismatch",
                "actual_sha256": actual_sha,
            })
            continue
        payload = _attachment_payload(str(item["zotero_item_key"]), brain_path)
        actions.append({
            "action": "webapi_create_linked_file_attachment",
            "brain_path": brain_path,
            "sha256": expected_sha or actual_sha,
            "zotero_item_key": item["zotero_item_key"],
            "citekey": item.get("citekey"),
            "payload": payload,
            "rollback": {
                "delete_created_attachment_item_key": "recorded in apply log",
                "does_not_upload_or_delete_pdf": True,
            },
        })

    return {
        "run_id": ic.run_id_from_dir(run_dir),
        "created_at": ic.utc_now(),
        "method": "zotero-web-api-linked-file",
        "brain_root": str(brain_root),
        "zotero_db_copy": str(db_copy),
        "zotero_library": _detect_library_metadata(db_copy),
        "zotero_profile": _detect_zotero_profile_paths(brain_root),
        "source_reconcile_log": str(reconcile_log or run_dir / "M4-zotero-reconcile-log.json"),
        "requires_env": ["ZOTERO_API_KEY", "ZOTERO_LIBRARY_ID or ZOTERO_USER_ID if not detected from zotero_db_copy"],
        "summary": {
            "source_holds": len(holds),
            "planned_linked_file_attachments": len(actions),
            "plan_holds": len(plan_holds),
        },
        "actions": actions,
        "holds": plan_holds,
    }


def _webapi_credentials(plan: dict[str, Any] | None = None) -> tuple[str, str, str]:
    api_key = os.environ.get("ZOTERO_API_KEY", "").strip()
    plan_library = (plan or {}).get("zotero_library") or {}
    library_type = os.environ.get("ZOTERO_LIBRARY_TYPE", str(plan_library.get("type") or "user")).strip().lower() or "user"
    library_id = (os.environ.get("ZOTERO_LIBRARY_ID") or os.environ.get("ZOTERO_USER_ID") or "").strip()
    if not library_id and library_type == "user" and plan_library.get("user_id"):
        library_id = str(plan_library["user_id"])
    if library_type not in {"user", "group"}:
        raise ValueError("ZOTERO_LIBRARY_TYPE must be user or group")
    missing = []
    if not api_key:
        missing.append("ZOTERO_API_KEY")
    if not library_id:
        missing.append("ZOTERO_LIBRARY_ID or ZOTERO_USER_ID")
    if missing:
        raise ValueError("missing Zotero Web API credentials: " + ", ".join(missing))
    return api_key, library_id, library_type


def _is_run04_research_pdf(brain_path: str) -> bool:
    return (
        brain_path.startswith("knowledge/research/")
        and "/papers/" in brain_path
        and brain_path.lower().endswith(".pdf")
    )


def validate_plan_for_apply(plan: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    actions = plan.get("actions")
    holds = plan.get("holds") or []
    summary = plan.get("summary") or {}
    library = plan.get("zotero_library") or {}
    profile = plan.get("zotero_profile") or {}
    if plan.get("run_id") != "run-04":
        errors.append("run_id_must_be_run-04")
    if plan.get("method") != "zotero-web-api-linked-file":
        errors.append("method_must_be_zotero-web-api-linked-file")
    if not isinstance(actions, list) or not actions:
        errors.append("actions_must_be_non_empty_list")
        actions = []
    if holds:
        errors.append("plan_holds_must_be_empty")
    if summary.get("plan_holds") not in (0, None):
        errors.append("summary_plan_holds_must_be_zero")
    if summary.get("planned_linked_file_attachments") not in (None, len(actions)):
        errors.append("summary_planned_count_mismatch")
    if library.get("type") not in (None, "user"):
        errors.append("zotero_library_type_must_be_user_for_run-04")
    if library.get("editable") is False or library.get("files_editable") is False:
        errors.append("zotero_library_must_be_editable")
    if profile.get("ok") is not True:
        errors.append("zotero_baseAttachmentPath_must_match_brain_root")

    seen_brain_paths: set[str] = set()
    seen_parent_paths: set[tuple[str, str]] = set()
    for idx, action in enumerate(actions):
        prefix = f"action[{idx}]"
        if action.get("action") != "webapi_create_linked_file_attachment":
            errors.append(f"{prefix}.action_invalid")
            continue
        brain_path = str(action.get("brain_path") or "")
        payload = action.get("payload") or {}
        if not _is_run04_research_pdf(brain_path):
            errors.append(f"{prefix}.brain_path_out_of_scope")
        if brain_path in seen_brain_paths:
            errors.append(f"{prefix}.duplicate_brain_path")
        seen_brain_paths.add(brain_path)
        parent_key = str(action.get("zotero_item_key") or "")
        payload_parent = str(payload.get("parentItem") or "")
        payload_path = str(payload.get("path") or "")
        if not parent_key:
            errors.append(f"{prefix}.missing_zotero_item_key")
        if payload_parent != parent_key:
            errors.append(f"{prefix}.payload_parent_mismatch")
        if payload.get("itemType") != "attachment":
            errors.append(f"{prefix}.payload_itemType_invalid")
        if payload.get("linkMode") != "linked_file":
            errors.append(f"{prefix}.payload_linkMode_invalid")
        if "filename" in payload:
            errors.append(f"{prefix}.payload_filename_invalid_for_linked_file")
        if payload.get("contentType") != "application/pdf":
            errors.append(f"{prefix}.payload_contentType_invalid")
        if payload_path != f"attachments:{brain_path}":
            errors.append(f"{prefix}.payload_path_invalid")
        parent_path_key = (parent_key, payload_path)
        if parent_path_key in seen_parent_paths:
            errors.append(f"{prefix}.duplicate_parent_path")
        seen_parent_paths.add(parent_path_key)
        rollback = action.get("rollback") or {}
        if rollback.get("does_not_upload_or_delete_pdf") is not True:
            errors.append(f"{prefix}.rollback_pdf_safety_missing")
    return {
        "ok": not errors,
        "errors": errors,
        "summary": {
            "actions": len(actions),
            "holds": len(holds),
            "unique_brain_paths": len(seen_brain_paths),
            "unique_parent_paths": len(seen_parent_paths),
        },
    }


def _post_items(api_base: str, api_key: str, library_id: str, library_type: str, items: list[dict[str, Any]]) -> dict[str, Any]:
    plural = "users" if library_type == "user" else "groups"
    url = f"{api_base.rstrip('/')}/{plural}/{library_id}/items"
    req = urllib.request.Request(
        url,
        data=json.dumps(items, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={
            "Content-Type": "application/json",
            "Zotero-API-Key": api_key,
            "Zotero-API-Version": "3",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            return {"status": resp.status, "body": json.loads(body) if body else {}}
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": body}
    except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
        return {"status": 0, "body": {"error": type(exc).__name__, "message": str(exc)}}


def _get_item_children(api_base: str, api_key: str, library_id: str, library_type: str, parent_key: str) -> dict[str, Any]:
    plural = "users" if library_type == "user" else "groups"
    url = f"{api_base.rstrip('/')}/{plural}/{library_id}/items/{parent_key}/children?limit=100&format=json"
    req = urllib.request.Request(
        url,
        method="GET",
        headers={
            "Zotero-API-Key": api_key,
            "Zotero-API-Version": "3",
        },
    )
    last_error: BaseException | None = None
    for attempt in range(3):
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                body = resp.read().decode("utf-8", errors="replace")
                return {"status": resp.status, "body": json.loads(body) if body else []}
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            return {"status": exc.code, "body": body}
        except (urllib.error.URLError, TimeoutError, http.client.HTTPException) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(1.5 * (attempt + 1))
                continue
    return {
        "status": 0,
        "body": {
            "error": type(last_error).__name__ if last_error else "unknown_error",
            "message": str(last_error) if last_error else "",
        },
    }


def _delete_item(
    api_base: str,
    api_key: str,
    library_id: str,
    library_type: str,
    item_key: str,
    version: int,
) -> dict[str, Any]:
    plural = "users" if library_type == "user" else "groups"
    url = f"{api_base.rstrip('/')}/{plural}/{library_id}/items/{item_key}"
    req = urllib.request.Request(
        url,
        method="DELETE",
        headers={
            "Zotero-API-Key": api_key,
            "Zotero-API-Version": "3",
            "If-Unmodified-Since-Version": str(version),
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            return {
                "status": resp.status,
                "last_modified_version": resp.headers.get("Last-Modified-Version"),
                "body": resp.read().decode("utf-8", errors="replace"),
            }
    except urllib.error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        return {"status": exc.code, "body": body}


def _get_existing_linked_attachment(
    api_base: str,
    api_key: str,
    library_id: str,
    library_type: str,
    action: dict[str, Any],
) -> dict[str, Any] | None:
    payload = action.get("payload") or {}
    expected_path = payload.get("path")
    parent_key = payload.get("parentItem") or action.get("zotero_item_key")
    if not expected_path or not parent_key:
        return None
    children = _get_item_children(api_base, api_key, library_id, library_type, str(parent_key))
    if not (200 <= int(children["status"]) < 300) or not isinstance(children["body"], list):
        raise RuntimeError(f"failed to inspect Zotero children for {parent_key}: HTTP {children['status']}")
    for child in children["body"]:
        data = child.get("data") if isinstance(child, dict) else None
        if not isinstance(data, dict):
            continue
        if (
            data.get("itemType") == "attachment"
            and data.get("linkMode") == "linked_file"
            and data.get("path") == expected_path
        ):
            return {
                "brain_path": action.get("brain_path"),
                "zotero_parent_item_key": action.get("zotero_item_key"),
                "zotero_attachment_item_key": data.get("key") or child.get("key"),
                "path": expected_path,
            }
    return None


def apply_plan(
    plan: dict[str, Any],
    api_base: str = DEFAULT_API_BASE,
    batch_size: int = 50,
    max_create: int | None = None,
) -> dict[str, Any]:
    if max_create is not None and max_create < 1:
        raise ValueError("--max-create must be >= 1")
    api_key, library_id, library_type = _webapi_credentials(plan)
    plan_validation = validate_plan_for_apply(plan)
    if not plan_validation["ok"]:
        raise ValueError("unsafe Zotero Web API plan: " + ", ".join(plan_validation["errors"][:10]))
    actions = [a for a in plan.get("actions", []) if a.get("action") == "webapi_create_linked_file_attachment"]
    to_create: list[dict[str, Any]] = []
    skipped_existing: list[dict[str, Any]] = []
    preflight_failures: list[dict[str, Any]] = []
    for action in actions:
        try:
            existing = _get_existing_linked_attachment(api_base, api_key, library_id, library_type, action)
        except RuntimeError as exc:
            preflight_failures.append({
                "brain_path": action.get("brain_path"),
                "zotero_parent_item_key": action.get("zotero_item_key"),
                "error": str(exc),
            })
            continue
        if existing:
            skipped_existing.append(existing)
        else:
            to_create.append(action)

    deferred: list[dict[str, Any]] = []
    if max_create is not None and len(to_create) > max_create:
        deferred = to_create[max_create:]
        to_create = to_create[:max_create]

    batches = []
    created: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = list(preflight_failures)
    for start in range(0, len(to_create), batch_size):
        chunk = to_create[start:start + batch_size]
        result = _post_items(api_base, api_key, library_id, library_type, [a["payload"] for a in chunk])
        batches.append({"start": start, "count": len(chunk), "status": result["status"]})
        body = result["body"]
        if not (200 <= int(result["status"]) < 300) or not isinstance(body, dict):
            failures.append({"start": start, "count": len(chunk), "status": result["status"], "body": body})
            continue
        successful = body.get("successful") or body.get("success") or {}
        failed = body.get("failed") or {}
        for idx, action in enumerate(chunk):
            rec = successful.get(str(idx)) if isinstance(successful, dict) else None
            if rec:
                created.append({
                    "brain_path": action["brain_path"],
                    "zotero_parent_item_key": action["zotero_item_key"],
                    "zotero_attachment_item_key": rec.get("key"),
                    "version": rec.get("version") or (rec.get("data") or {}).get("version"),
                    "rollback_action": "delete_created_attachment_item",
                })
            else:
                failures.append({
                    "brain_path": action["brain_path"],
                    "zotero_parent_item_key": action["zotero_item_key"],
                    "status": result["status"],
                    "body": failed.get(str(idx)) if isinstance(failed, dict) else body,
                })
        time.sleep(0.25)
    return {
        "ok": not failures and len(created) == len(to_create),
        "complete": not deferred and not failures and len(created) == len(to_create),
        "created": created,
        "skipped_existing": skipped_existing,
        "deferred": [
            {
                "brain_path": action.get("brain_path"),
                "zotero_parent_item_key": action.get("zotero_item_key"),
                "path": (action.get("payload") or {}).get("path"),
                "reason": "max_create_limit",
            }
            for action in deferred
        ],
        "failures": failures,
        "batches": batches,
        "plan_validation": plan_validation,
        "summary": {
            "planned": len(actions),
            "created": len(created),
            "skipped_existing": len(skipped_existing),
            "deferred": len(deferred),
            "failures": len(failures),
            "max_create": max_create,
            "writes_limited": bool(deferred),
        },
        "next_step": "Run m4_zotero.py --reconcile-only after Zotero desktop syncs the new linked-file children.",
    }


def preflight_plan(plan: dict[str, Any], api_base: str = DEFAULT_API_BASE, require_credentials: bool = True) -> dict[str, Any]:
    plan_validation = validate_plan_for_apply(plan)
    payload: dict[str, Any] = {
        "ok": plan_validation["ok"],
        "plan_validation": plan_validation,
        "summary": {
            "planned": plan_validation["summary"]["actions"],
            "already_linked": 0,
            "to_create": 0,
            "failures": 0,
            "credential_checked": False,
        },
        "already_linked": [],
        "to_create": [],
        "failures": [],
        "writes_performed": False,
    }
    if not plan_validation["ok"]:
        return payload
    try:
        api_key, library_id, library_type = _webapi_credentials(plan)
    except ValueError as exc:
        if require_credentials:
            raise
        payload["credential_error"] = str(exc)
        return payload
    payload["summary"]["credential_checked"] = True
    actions = [a for a in plan.get("actions", []) if a.get("action") == "webapi_create_linked_file_attachment"]
    for action in actions:
        try:
            existing = _get_existing_linked_attachment(api_base, api_key, library_id, library_type, action)
        except RuntimeError as exc:
            payload["failures"].append({
                "brain_path": action.get("brain_path"),
                "zotero_parent_item_key": action.get("zotero_item_key"),
                "error": str(exc),
            })
            continue
        if existing:
            payload["already_linked"].append(existing)
        else:
            payload["to_create"].append({
                "brain_path": action.get("brain_path"),
                "zotero_parent_item_key": action.get("zotero_item_key"),
                "path": (action.get("payload") or {}).get("path"),
            })
    payload["summary"]["already_linked"] = len(payload["already_linked"])
    payload["summary"]["to_create"] = len(payload["to_create"])
    payload["summary"]["failures"] = len(payload["failures"])
    payload["ok"] = payload["ok"] and not payload["failures"]
    return payload


def rollback_apply_log(apply_log: dict[str, Any], plan: dict[str, Any] | None = None, api_base: str = DEFAULT_API_BASE) -> dict[str, Any]:
    api_key, library_id, library_type = _webapi_credentials(plan or {})
    deleted: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    for rec in apply_log.get("created", []):
        item_key = rec.get("zotero_attachment_item_key")
        version = rec.get("version")
        if not item_key or version is None:
            failures.append({
                "brain_path": rec.get("brain_path"),
                "zotero_attachment_item_key": item_key,
                "reason": "missing_attachment_key_or_version",
            })
            continue
        result = _delete_item(api_base, api_key, library_id, library_type, str(item_key), int(version))
        if int(result["status"]) == 204:
            deleted.append({
                "brain_path": rec.get("brain_path"),
                "zotero_parent_item_key": rec.get("zotero_parent_item_key"),
                "zotero_attachment_item_key": item_key,
                "version": int(version),
                "last_modified_version": result.get("last_modified_version"),
            })
        else:
            failures.append({
                "brain_path": rec.get("brain_path"),
                "zotero_attachment_item_key": item_key,
                "version": version,
                "status": result["status"],
                "body": result.get("body"),
            })
        time.sleep(0.25)
    for rec in apply_log.get("skipped_existing", []):
        skipped.append({
            "brain_path": rec.get("brain_path"),
            "zotero_parent_item_key": rec.get("zotero_parent_item_key"),
            "zotero_attachment_item_key": rec.get("zotero_attachment_item_key"),
            "reason": "not_created_by_apply_log",
        })
    return {
        "ok": not failures,
        "deleted": deleted,
        "skipped_existing": skipped,
        "failures": failures,
        "summary": {
            "created_in_apply_log": len(apply_log.get("created", [])),
            "deleted": len(deleted),
            "skipped_existing": len(skipped),
            "failures": len(failures),
        },
        "next_step": "Run m4_zotero.py --reconcile-only after Zotero desktop syncs the rollback.",
    }


def main() -> int:
    parser = ic.parser("M4 Zotero linked-file Web API repair")
    parser.add_argument("--plan", action="store_true")
    parser.add_argument("--preflight", action="store_true")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--rollback", action="store_true")
    parser.add_argument("--no-credentials-ok", action="store_true")
    parser.add_argument("--approved-plan", type=Path)
    parser.add_argument("--approved-apply-log", type=Path)
    parser.add_argument("--reconcile-log", type=Path)
    parser.add_argument("--zotero-db-copy", type=Path)
    parser.add_argument("--api-base", default=DEFAULT_API_BASE)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--max-create", type=int)
    args = parser.parse_args()

    brain_root = ic.resolve_path(args.brain_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    stateful_modes = [args.apply, args.rollback, args.preflight]
    if sum(1 for flag in stateful_modes if flag) > 1:
        print("--preflight, --apply, and --rollback are mutually exclusive", file=sys.stderr)
        return 2
    if args.preflight:
        if not args.approved_plan:
            print("--preflight requires --approved-plan", file=sys.stderr)
            return 2
        plan = ic.read_json(args.approved_plan)
        try:
            payload = preflight_plan(plan, args.api_base, require_credentials=not args.no_credentials_ok)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ic.write_json(run_dir / "M4-zotero-linked-file-webapi-preflight-log.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    if args.rollback:
        if not args.approved_apply_log:
            print("--rollback requires --approved-apply-log", file=sys.stderr)
            return 2
        apply_log = ic.read_json(args.approved_apply_log)
        plan = ic.read_json(args.approved_plan) if args.approved_plan else None
        try:
            payload = rollback_apply_log(apply_log, plan, args.api_base)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ic.write_json(run_dir / "M4-zotero-linked-file-webapi-rollback-log.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1
    if args.apply:
        if not args.approved_plan:
            print("--apply requires --approved-plan", file=sys.stderr)
            return 2
        plan = ic.read_json(args.approved_plan)
        try:
            payload = apply_plan(plan, args.api_base, batch_size=args.batch_size, max_create=args.max_create)
        except ValueError as exc:
            print(str(exc), file=sys.stderr)
            return 2
        ic.write_json(run_dir / "M4-zotero-linked-file-webapi-apply-log.json", payload)
        print(json.dumps(payload, ensure_ascii=False, indent=2))
        return 0 if payload.get("ok") else 1

    if not args.plan:
        args.plan = True
    plan = build_plan(brain_root, run_dir, args.reconcile_log, args.zotero_db_copy)
    out = run_dir / "zotero-linked-file-webapi-plan.json"
    ic.write_json(out, plan)
    summary = {
        "ok": plan["summary"]["planned_linked_file_attachments"] > 0,
        "plan": str(out),
        "summary": plan["summary"],
        "zotero_library": plan.get("zotero_library"),
        "zotero_profile": plan.get("zotero_profile"),
        "requires_env": plan.get("requires_env"),
        "plan_validation": validate_plan_for_apply(plan),
    }
    ic.write_json(run_dir / "M4-zotero-linked-file-webapi-log.json", summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0 if summary["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
