# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K1 module doctor CLI:校验 deploy/modules.json 与现实一致 + 列模块状态。

用法:
  python -m rtime_admin_core.modules_cli doctor [--manifest deploy/modules.json] [--compose compose.prod.yml] [--profiles qq,web]
  python -m rtime_admin_core.modules_cli list   [--manifest ...]

装机向导/面板"模块"视图/开源打包也读同一 manifest(design/module-system-and-open-source)。
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

from .modules import load_manifest, manifest_report, validate_manifest
from .registry import KNOWN_MODULE_NAMES


def _repo_root() -> Path:
    # packages/rtime-admin-core/src/rtime_admin_core/modules_cli.py -> repo root
    return Path(__file__).resolve().parents[4]


def _compose_profiles(compose_path: Path) -> set[str]:
    """从 compose 文件里抽所有 service 声明的 profiles(如 profiles: ["qq"])。"""
    if not compose_path.is_file():
        return set()
    text = compose_path.read_text(encoding="utf-8")
    out: set[str] = set()
    for m in re.finditer(r'profiles:\s*\[([^\]]*)\]', text):
        for tok in re.findall(r'["\']([a-zA-Z0-9_-]+)["\']', m.group(1)):
            out.add(tok)
    return out


def main(argv: list[str] | None = None) -> int:
    root = _repo_root()
    p = argparse.ArgumentParser(prog="rtime_admin_core.modules_cli", description="K1 module doctor")
    p.add_argument("cmd", choices=["doctor", "list"])
    p.add_argument("--manifest", type=Path, default=root / "deploy" / "modules.json")
    p.add_argument("--compose", type=Path, default=root / "compose.prod.yml")
    p.add_argument("--profiles", default="", help="已启用的 COMPOSE_PROFILES(逗号分隔),标装没装")
    args = p.parse_args(argv)

    modules = load_manifest(args.manifest.read_text(encoding="utf-8"))
    known_profiles = _compose_profiles(args.compose)
    enabled = {s.strip() for s in args.profiles.split(",") if s.strip()}

    if args.cmd == "list":
        report = manifest_report(modules, [], enabled_profiles=enabled)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0

    issues = validate_manifest(
        modules,
        known_config_modules=set(KNOWN_MODULE_NAMES),
        known_profiles=known_profiles,
        docs_exists=lambda rel: (root / rel).exists(),
    )
    report = manifest_report(modules, issues, enabled_profiles=enabled)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["ok"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
