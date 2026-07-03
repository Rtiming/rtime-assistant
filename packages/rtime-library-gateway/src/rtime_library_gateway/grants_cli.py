# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J6 grant 台账管理 CLI(owner/平台超管用)。

owner 用它管库共享 grant 台账:列(审核视图)、看、加、吊销、生成网关 policy。
台账是 JSONL 文件(默认 ``$STATE/rtime-library-gateway/grants.jsonl``)。CLI 由 owner 在
shell 里直接跑(= 平台超管),不走 API 鉴权;RBAC 语义上 add/revoke 属超管独占能力
(config-and-access §一 SUPER_ONLY:issue/revoke token 类)。

用法:
  python -m rtime_library_gateway.grants_cli list [--all]
  python -m rtime_library_gateway.grants_cli show <grant_id>
  python -m rtime_library_gateway.grants_cli add --grant-id G --subject S --prefix P [--contribute] [--expires ISO]
  python -m rtime_library_gateway.grants_cli revoke <grant_id>
  python -m rtime_library_gateway.grants_cli gen-policy <grant_id> [--out FILE]
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from .grants import (
    STATUS_REVOKED,
    Grant,
    GrantScope,
    dump_ledger,
    grant_to_policy,
    load_ledger,
    owner_audit_view,
)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def default_ledger_path() -> Path:
    raw = os.environ.get("RTIME_LIBRARY_GRANTS_LEDGER")
    if raw:
        return Path(raw).expanduser()
    state = Path(os.environ.get("XDG_STATE_HOME", "~/.local/state")).expanduser()
    return state / "rtime-assistant" / "rtime-library-gateway" / "grants.jsonl"


class GrantLedger:
    """文件后端的 grant 台账(load/save/add/revoke/active)。"""

    def __init__(self, path: Path) -> None:
        self.path = path

    def load(self) -> list[Grant]:
        if not self.path.is_file():
            return []
        return load_ledger(self.path.read_text(encoding="utf-8"))

    def save(self, grants: list[Grant]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_name(f".{self.path.name}.tmp")
        tmp.write_text(dump_ledger(grants), encoding="utf-8")
        tmp.replace(self.path)  # 原子

    def get(self, grant_id: str) -> Grant | None:
        return next((g for g in self.load() if g.grant_id == grant_id), None)

    def add(self, grant: Grant) -> None:
        grants = self.load()
        if any(g.grant_id == grant.grant_id for g in grants):
            raise ValueError(f"grant_id 已存在: {grant.grant_id}")
        grants.append(grant)
        self.save(grants)

    def revoke(self, grant_id: str) -> bool:
        grants = self.load()
        found = False
        out: list[Grant] = []
        for g in grants:
            if g.grant_id == grant_id and g.status != STATUS_REVOKED:
                out.append(Grant.from_dict({**g.to_dict(), "status": STATUS_REVOKED}))
                found = True
            else:
                out.append(g)
        if found:
            self.save(out)
        return found


def _grant_from_args(args: argparse.Namespace) -> Grant:
    scopes = tuple(
        GrantScope(prefix=p, read=True, contribute=bool(args.contribute))
        for p in args.prefix
    )
    return Grant(
        grant_id=args.grant_id,
        subject=args.subject,
        scopes=scopes,
        granted_by=args.granted_by,
        granted_at=_now_iso(),
        expires_at=args.expires,
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="rtime_library_gateway.grants_cli",
        description="J6 库共享 grant 台账管理(owner/平台超管)。",
    )
    parser.add_argument("--ledger", type=Path, default=None, help="台账文件(默认 state 目录)")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_list = sub.add_parser("list", help="owner 审核视图:谁能碰我的库")
    p_list.add_argument("--all", action="store_true", help="含已吊销/过期(默认也显示,标 active)")

    p_show = sub.add_parser("show", help="看一个 grant 详情")
    p_show.add_argument("grant_id")

    p_add = sub.add_parser("add", help="新增 grant")
    p_add.add_argument("--grant-id", required=True)
    p_add.add_argument("--subject", required=True)
    p_add.add_argument("--prefix", action="append", required=True, help="brain 相对前缀(可多次)")
    p_add.add_argument("--contribute", action="store_true", help="授予投稿位(默认只读)")
    p_add.add_argument("--expires", default=None, help="到期 ISO8601(默认无限)")
    p_add.add_argument("--granted-by", default="owner")

    p_rev = sub.add_parser("revoke", help="吊销 grant")
    p_rev.add_argument("grant_id")

    p_gen = sub.add_parser("gen-policy", help="由 grant 生成网关 policy JSON")
    p_gen.add_argument("grant_id")
    p_gen.add_argument("--out", type=Path, default=None, help="写到文件(默认 stdout)")

    args = parser.parse_args(argv)
    ledger = GrantLedger(args.ledger or default_ledger_path())

    if args.cmd == "list":
        print(json.dumps(owner_audit_view(ledger.load(), _now_iso()), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "show":
        g = ledger.get(args.grant_id)
        if g is None:
            print(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
            return 1
        print(json.dumps(g.to_dict(), ensure_ascii=False, indent=2))
        return 0
    if args.cmd == "add":
        try:
            ledger.add(_grant_from_args(args))
        except ValueError as exc:
            print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False))
            return 1
        print(json.dumps({"ok": True, "added": args.grant_id}, ensure_ascii=False))
        return 0
    if args.cmd == "revoke":
        ok = ledger.revoke(args.grant_id)
        print(json.dumps({"ok": ok, "revoked": args.grant_id if ok else None}, ensure_ascii=False))
        return 0 if ok else 1
    if args.cmd == "gen-policy":
        g = ledger.get(args.grant_id)
        if g is None:
            print(json.dumps({"ok": False, "error": "not found"}, ensure_ascii=False))
            return 1
        policy = json.dumps(grant_to_policy(g), ensure_ascii=False, indent=2)
        if args.out:
            args.out.write_text(policy + "\n", encoding="utf-8")
            print(json.dumps({"ok": True, "wrote": str(args.out)}, ensure_ascii=False))
        else:
            print(policy)
        return 0
    parser.error(f"unknown cmd: {args.cmd}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
