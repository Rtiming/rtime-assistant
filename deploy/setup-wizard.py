#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K4 安装向导:读 deploy/modules.json → 选模块 → 拼 COMPOSE_PROFILES → 产实例目录。

纯 stdlib(裸机 bootstrap:向导必须在任何 venv/依赖存在之前就能跑,树莓派/新服务器
git clone 后第一条命令)。产出遵守 update.sh 的实例目录约定
(docs/instance-deploy.zh-CN.md):

  <instance>/.env                  COMPOSE_PROFILES/UPDATE_INSTANCE_NAME + 模块要点注释
  <instance>/compose.override.yml  实例差异骨架(端口/挂载自己加)
  <instance>/data/  state/         数据与状态
  <instance>/state/install.lock    INSTALL_LOCK(J8):已初始化标记,拒绝重复 init(--force 覆盖)

用法:
  python3 deploy/setup-wizard.py list [--json]
  python3 deploy/setup-wizard.py plan --instance DIR --modules id1,id2 [--json]
  python3 deploy/setup-wizard.py init --instance DIR --modules id1,id2 [--name N] [--force] [--json]
  (init 不带 --modules 且在 TTY 上 → 交互式勾选;非 TTY 必须显式 --modules)

选模块给 module id(deploy/modules.json 里的);depends_on 闭包自动带上;
core(optional=false)恒含不用选。向导只写文件不碰 docker;起服务用
docker compose -f <repo>/compose.prod.yml -f <instance>/compose.override.yml \
  --env-file <instance>/.env -p rtime-<name> up -d
之后的升级/回滚走 deploy/update.sh。完整校验(config_module/docs 对账)在 venv 建好后
跑 python -m rtime_admin_core.modules_cli doctor。
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
LOCK_NAME = "install.lock"


def load_modules(manifest_path: Path) -> list[dict]:
    data = json.loads(manifest_path.read_text(encoding="utf-8"))
    mods = data.get("modules")
    if not isinstance(mods, list):
        raise SystemExit(f"错误: {manifest_path} 不是合法 manifest(缺 modules 数组)")
    ids = [m.get("id") for m in mods]
    if len(ids) != len(set(ids)):
        raise SystemExit("错误: manifest 有重复 module id")
    return mods


def compose_profiles_available(compose_path: Path) -> set[str]:
    """compose 文件里声明过的 profiles(与 modules_cli._compose_profiles 同口径)。"""
    if not compose_path.is_file():
        return set()
    out: set[str] = set()
    for m in re.finditer(r'profiles:\s*\[([^\]]*)\]', compose_path.read_text(encoding="utf-8")):
        out.update(re.findall(r'["\']([a-zA-Z0-9_-]+)["\']', m.group(1)))
    return out


def resolve_selection(mods: list[dict], wanted: list[str]) -> tuple[list[dict], list[str]]:
    """选中的 optional 模块 + depends_on 闭包(含被依赖的 optional);core 恒含。
    返回 (选中模块列表(含core), 错误列表)。"""
    by_id = {m["id"]: m for m in mods}
    errors = [w for w in wanted if w not in by_id]
    if errors:
        return [], [f"未知 module id: {w}(用 list 子命令看全部)" for w in errors]
    selected: set[str] = set()
    stack = list(wanted)
    while stack:
        mid = stack.pop()
        if mid in selected:
            continue
        selected.add(mid)
        stack.extend(by_id[mid].get("depends_on", []))
    for m in mods:  # core 恒含
        if not m.get("optional", True):
            selected.add(m["id"])
    ordered = [m for m in mods if m["id"] in selected]
    return ordered, []


def plan(mods_selected: list[dict], *, available_profiles: set[str]) -> dict:
    profiles = sorted(
        {m["compose_profile"] for m in mods_selected if m.get("compose_profile")}
    )
    missing = [p for p in profiles if p not in available_profiles]
    return {
        "modules": [m["id"] for m in mods_selected],
        "compose_profiles": profiles,
        "profile_issues": [f"compose 里没有 profile {p!r}" for p in missing],
        "next_steps": [
            {
                "module": m["id"],
                "title": m.get("title", m["id"]),
                "setup_notes": m.get("setup_notes", ""),
                "docs": m.get("docs"),
            }
            for m in mods_selected
        ],
    }


def render_env(name: str, profiles: list[str], mods_selected: list[dict]) -> str:
    lines = [
        "# rtime-assistant 实例配置(setup-wizard 生成;compose --env-file 用)",
        "# 起服务/升级见 docs/instance-deploy.zh-CN.md 与 deploy/update.sh 头注。",
        f"UPDATE_INSTANCE_NAME={name}",
        f"COMPOSE_PROFILES={','.join(profiles)}",
        "",
        "# --- 每个所选模块的装配要点(来自 deploy/modules.json setup_notes) ---",
    ]
    for m in mods_selected:
        note = (m.get("setup_notes") or "").replace("\n", " ")
        lines.append(f"# [{m['id']}] {m.get('title', '')}")
        if note:
            lines.append(f"#   {note}")
        if m.get("docs"):
            lines.append(f"#   文档: {m['docs']}")
    lines += [
        "",
        "# --- 在下方补齐各模块的实际 env(参考 deploy/env/*.example 与各模块文档) ---",
        "",
    ]
    return "\n".join(lines)


OVERRIDE_SKELETON = """# 实例差异(端口/挂载/资源限制)写这里;基座是 <repo>/compose.prod.yml。
# docker compose -f <repo>/compose.prod.yml -f 本文件 --env-file .env -p rtime-<name> up -d
services: {}
"""


def do_init(args, mods: list[dict], selected: list[dict], report: dict) -> dict:
    instance = Path(args.instance).expanduser()
    lock = instance / "state" / LOCK_NAME
    if lock.exists() and not args.force:
        raise SystemExit(
            f"错误: {lock} 已存在(实例已初始化)。重复 init 会覆盖 .env——确认要重来用 --force。"
        )
    name = args.name or instance.name
    for sub in ("data", "state"):
        (instance / sub).mkdir(parents=True, exist_ok=True)
    (instance / ".env").write_text(
        render_env(name, report["compose_profiles"], selected), encoding="utf-8"
    )
    override = instance / "compose.override.yml"
    if not override.exists():  # 不覆盖用户已写的实例差异
        override.write_text(OVERRIDE_SKELETON, encoding="utf-8")
    lock.write_text(
        json.dumps(
            {
                "ts": datetime.now(timezone.utc).isoformat(),
                "modules": report["modules"],
                "compose_profiles": report["compose_profiles"],
                "wizard": "deploy/setup-wizard.py",
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return {"instance": str(instance), "name": name, **report}


def interactive_pick(mods: list[dict]) -> list[str]:
    optional = [m for m in mods if m.get("optional", True)]
    print("可选模块(core 恒装不用选):", file=sys.stderr)
    for i, m in enumerate(optional, 1):
        print(f"  {i:2d}. {m['id']:28s} {m.get('title', '')}", file=sys.stderr)
    raw = input("输入编号(逗号分隔,回车=一个都不装): ").strip()
    picked = []
    for tok in raw.split(","):
        tok = tok.strip()
        if tok:
            idx = int(tok) - 1
            if not (0 <= idx < len(optional)):
                raise SystemExit(f"错误: 编号 {tok} 超范围")
            picked.append(optional[idx]["id"])
    return picked


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="setup-wizard", description="K4 安装向导")
    ap.add_argument("cmd", choices=["list", "plan", "init"])
    ap.add_argument("--instance", help="实例目录(plan/init)")
    ap.add_argument("--modules", default=None, help="逗号分隔的 module id")
    ap.add_argument("--name", default=None, help="compose 项目名后缀,默认实例目录名")
    ap.add_argument("--manifest", type=Path, default=REPO / "deploy" / "modules.json")
    ap.add_argument("--compose", type=Path, default=REPO / "compose.prod.yml")
    ap.add_argument("--force", action="store_true", help="已初始化实例也重新 init")
    ap.add_argument("--json", action="store_true", help="JSON 输出(给 agent/脚本)")
    args = ap.parse_args(argv)

    mods = load_modules(args.manifest)

    if args.cmd == "list":
        view = [
            {
                "id": m["id"],
                "kind": m.get("kind"),
                "title": m.get("title"),
                "optional": m.get("optional", True),
                "compose_profile": m.get("compose_profile"),
                "docs": m.get("docs"),
            }
            for m in mods
        ]
        if args.json:
            print(json.dumps({"modules": view}, ensure_ascii=False, indent=2))
        else:
            for m in view:
                flag = "可选" if m["optional"] else "恒装"
                print(f"{m['id']:28s} [{m['kind']:11s}] {flag}  {m['title']}")
        return 0

    if not args.instance:
        raise SystemExit("错误: plan/init 需要 --instance <目录>")
    if args.modules is not None:
        wanted = [s.strip() for s in args.modules.split(",") if s.strip()]
    elif args.cmd == "init" and sys.stdin.isatty():
        wanted = interactive_pick(mods)
    else:
        raise SystemExit("错误: 非交互环境必须显式 --modules id1,id2(可为空串=只装core)")

    selected, errors = resolve_selection(mods, wanted)
    if errors:
        for e in errors:
            print(f"错误: {e}", file=sys.stderr)
        return 2
    report = plan(selected, available_profiles=compose_profiles_available(args.compose))
    if report["profile_issues"]:
        for issue in report["profile_issues"]:
            print(f"错误: {issue}", file=sys.stderr)
        return 2

    if args.cmd == "plan":
        out = {"instance": args.instance, **report}
    else:
        out = do_init(args, mods, selected, report)

    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        print(f"实例: {out['instance']}")
        print(f"模块: {', '.join(out['modules'])}")
        print(f"COMPOSE_PROFILES: {','.join(out['compose_profiles']) or '(无,只有base服务)'}")
        print("接下来:")
        for step in out["next_steps"]:
            print(f"  - [{step['module']}] {step['setup_notes'] or '(无额外步骤)'}")
            if step["docs"]:
                print(f"      文档: {step['docs']}")
        if args.cmd == "init":
            print("然后: 补齐 .env → docker compose ... up -d → 之后升级走 deploy/update.sh")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
