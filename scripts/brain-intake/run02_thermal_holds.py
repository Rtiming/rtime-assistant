#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Diagnose run-01 thermal holds for run-02."""

from __future__ import annotations

import argparse
import csv
import json
import subprocess
from pathlib import Path
from typing import Any

import intake_common as ic


def _pdf_pages(path: Path) -> int | None:
    if path.suffix.lower() != ".pdf" or not path.exists():
        return None
    proc = subprocess.run(["pdfinfo", str(path)], check=False, capture_output=True, text=True)
    if proc.returncode != 0:
        return None
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            try:
                return int(line.split(":", 1)[1].strip())
            except ValueError:
                return None
    return None


def _mtime(path: Path) -> float | None:
    return path.stat().st_mtime if path.exists() else None


def _archive_path(vault_root: Path, original: Path) -> Path:
    old_root = vault_root / ic.THERMAL_VAULT_REL
    archive_root = vault_root / ic.THERMAL_ARCHIVE_REL / "热力学与统计物理资料-original"
    try:
        return archive_root / original.relative_to(old_root)
    except ValueError:
        return original


def _file_record(path: Path) -> dict[str, Any]:
    exists = path.exists()
    return {
        "path": str(path),
        "exists": exists,
        "sha256": ic.sha256_file(path) if exists and path.is_file() else None,
        "bytes": path.stat().st_size if exists and path.is_file() else None,
        "mtime": _mtime(path),
        "pages": _pdf_pages(path),
    }


def diagnose(run01_dir: Path, run_dir: Path, vault_root: Path) -> dict[str, Any]:
    plan = ic.read_json(run01_dir / "registry-plan.json")
    rows: list[dict[str, Any]] = []
    for action in plan.get("actions", []):
        if action.get("action") != "hold":
            continue
        if action.get("reason") not in {"target exists with different content", "thermal target mapping missing"}:
            continue
        original_vault = Path(action["path"])
        brain = Path(action.get("target") or "")
        archived = _archive_path(vault_root, original_vault)
        vault_info = _file_record(archived)
        brain_info = _file_record(brain)
        if action.get("reason") == "thermal target mapping missing":
            rows.append(
                {
                    "status": "archived-readme",
                    "reason": action.get("reason"),
                    "original_vault_path": str(original_vault),
                    "archived_vault_path": str(archived),
                    "brain_path": None,
                    "vault_sha256": vault_info["sha256"],
                    "brain_sha256": None,
                    "vault_bytes": vault_info["bytes"],
                    "brain_bytes": None,
                    "vault_mtime": vault_info["mtime"],
                    "brain_mtime": None,
                    "vault_pages": vault_info["pages"],
                    "brain_pages": None,
                    "rollback": {"kept_archived": str(archived)},
                }
            )
            continue
        newer_and_larger = False
        if vault_info["mtime"] is not None and brain_info["mtime"] is not None:
            newer_and_larger = bool(
                vault_info["mtime"] > brain_info["mtime"]
                and (vault_info["bytes"] or 0) > (brain_info["bytes"] or 0)
            )
        status = "待用户拍板" if newer_and_larger else "resolved-by-archive"
        rows.append(
            {
                "status": status,
                "reason": action.get("reason"),
                "original_vault_path": str(original_vault),
                "archived_vault_path": str(archived),
                "brain_path": str(brain),
                "vault_sha256": vault_info["sha256"],
                "brain_sha256": brain_info["sha256"],
                "vault_bytes": vault_info["bytes"],
                "brain_bytes": brain_info["bytes"],
                "vault_mtime": vault_info["mtime"],
                "brain_mtime": brain_info["mtime"],
                "vault_pages": vault_info["pages"],
                "brain_pages": brain_info["pages"],
                "rollback": {"restore_vault_archive": str(archived), "canonical_kept": str(brain)},
            }
        )
    summary = {
        "total": len(rows),
        "resolved_by_archive": sum(1 for row in rows if row["status"] == "resolved-by-archive"),
        "archived_readme": sum(1 for row in rows if row["status"] == "archived-readme"),
        "needs_user_decision": sum(1 for row in rows if row["status"] == "待用户拍板"),
    }
    payload = {"ok": True, "run_id": ic.run_id_from_dir(run_dir), "generated_at": ic.utc_now(), "summary": summary, "items": rows}
    ic.write_json(run_dir / "R1-thermal-hold-diagnosis.json", payload)
    csv_path = run_dir / "R1-thermal-hold-diagnosis.csv"
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    fields = [
        "status",
        "archived_vault_path",
        "brain_path",
        "vault_sha256",
        "brain_sha256",
        "vault_bytes",
        "brain_bytes",
        "vault_mtime",
        "brain_mtime",
        "vault_pages",
        "brain_pages",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fields})
    needs = [row for row in rows if row["status"] == "待用户拍板"]
    md = [
        "# R1 热统hold处置诊断",
        "",
        f"- run_id: {ic.run_id_from_dir(run_dir)}",
        f"- total: {summary['total']}",
        f"- resolved-by-archive: {summary['resolved_by_archive']}",
        f"- archived-readme: {summary['archived_readme']}",
        f"- 待用户拍板: {summary['needs_user_decision']}",
        "",
        "## 结论",
        "",
        "- brain 正本保持 canonical；vault 差异版本保持在归档目录。",
        "- vault 版本同时更新更晚且更大时列入待用户拍板；其余关闭为 resolved-by-archive。",
        "- vault 热统 README.md 保持归档，不迁移。",
    ]
    if needs:
        md.extend(["", "## 待用户拍板", ""])
        for row in needs:
            md.append(f"- `{row['archived_vault_path']}` -> `{row['brain_path']}`")
    else:
        md.extend(["", "## 待用户拍板", "", "- 无"])
    ic.write_text(run_dir / "R1-thermal-hold-diagnosis.md", "\n".join(md) + "\n")
    ic.write_json(run_dir / "R1-log.json", payload)
    return payload


def main() -> int:
    p = argparse.ArgumentParser(description="Diagnose run-01 thermal holds for run-02.")
    p.add_argument("--run01-dir", type=Path, default=Path("work/pipeline/run-01"))
    p.add_argument("--run-dir", type=Path, default=Path("work/pipeline/run-02"))
    p.add_argument("--vault-root", type=Path, default=ic.DEFAULT_VAULT_ROOT)
    args = p.parse_args()
    ic.ensure_run_dir(args.run_dir)
    result = diagnose(args.run01_dir, args.run_dir, args.vault_root)
    print(json.dumps(result["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
