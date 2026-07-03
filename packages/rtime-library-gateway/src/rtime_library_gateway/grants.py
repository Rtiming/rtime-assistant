# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J6 库共享:grant 一等对象 + 由 grant 生成网关 policy(config-and-access §五)。

行业依据(调研):AWS RAM resource share(可命名/可整体吊销)、GitHub fine-grained PAT
(默认无访问、显式白名单、强制过期)、Postgres RLS(USING 读 vs WITH CHECK 写正交)、
Google Drive Commenter(只读+可提建议=只读+投稿)。

现状缺口:8781 学生会 scope 是散在配置里的静态 policy 文件——没有"一个可整体吊销的
对象"、没有申请-批准留痕、没有到期、没有 owner 视角"谁能碰我"清单。J6 把它固化成
grant 一等对象,并让网关 policy **由 grant 生成**(替代手写),从此:
- 一次共享 = 一个 grant(可整体 revoke,像删 RAM share);
- scope 从"路径前缀白名单"升级为"{前缀: 读/投稿位}"(读写正交);
- 写永远是"投稿+审核"(lib.contribute→_inbox→owner finalize),外部对 knowledge/ 零直接写;
- 有效期字段就位(owner 定默认无限,但 write/contribute 建议按学期设,研究口径)。

纯 stdlib、无 IO(ledger 读写在 CLI/调用方)。运行身份别旁路:被授实例仍以受限身份跑
(独立进程 + 本 policy),grant 只决定 policy 内容,不改"网关是唯一入口"。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

GRANT_SCHEMA_VERSION = 1

STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"

# 外部被授权方能调的**读**方法(与手写 studentunion-policy.json 的 allow 集一致)。
# 写方法一律 deny;唯一的"写"是 lib.contribute(投稿到 _inbox,owner 审核后 finalize)。
_READ_METHODS = (
    "lib.doctor",
    "lib.policy",
    "lib.search",
    "lib.read",
    "lib.tree",
    "lib.stat",
    "lib.recent",
    "lib.list",
)
_CONTRIBUTE_METHOD = "lib.contribute"
# 恒 deny 的写/危险方法(即使某方法未来新增,allow 是白名单=未列即拒,这里是显式化)。
# lib.contribute 不在这里:它按 grant 是否授予 contribute 动态进 allow 或 deny。
_DENY_METHODS = (
    "lib.settings.*",
    "lib.finalize",
    "lib.course-intake",
    "lib.jobs.*",
    "lib.annotate",
    # H M2 直接写:改正文/回滚,超管专用(与 annotate 同,scoped 恒 deny)。
    "lib.edit",
    "lib.revert",
    # H M3 直接写:移动/重命名、软删归档、恢复,超管专用(scoped 恒 deny)。
    "lib.move",
    "lib.retire",
    "lib.restore",
)


@dataclass(frozen=True)
class GrantScope:
    """一条资源子集授权:一个 brain 相对前缀 + 读/投稿位(读写正交,Postgres RLS 口径)。

    ``read``:能读该前缀子树(默认 True——共享的意义)。
    ``contribute``:能往该前缀投稿(写=提议,进 _inbox 走 owner 审核;默认 False=只读)。
    绝不授予直接写(annotate/finalize)——那是 owner/超管的事。
    """

    prefix: str
    read: bool = True
    contribute: bool = False


@dataclass(frozen=True)
class Grant:
    """一次库共享的一等对象(可整体吊销、可审计、有生命周期)。

    ``expires_at``:ISO8601 或 None。owner 定默认无限(None);研究建议 write/contribute
    按学期/换届设到期。到期或 status=revoked => is_active(now) False => 网关下次加载即断。
    """

    grant_id: str
    subject: str                       # 被授方标识(如 "studentunion" 实例)
    scopes: tuple[GrantScope, ...]
    granted_by: str = "owner"
    granted_at: str = ""               # ISO8601(调用方注入,本模块不取时钟)
    expires_at: str | None = None      # None = 无限(owner 默认)
    status: str = STATUS_ACTIVE
    # 输出层脱敏/排除(与手写 policy 同义,默认对外收紧)
    redact_sensitive: bool = True
    redact_student_pii: bool = True
    excluded_top_dirs: tuple[str, ...] = ("personal-data", "profile")
    hide_excluded_in_results: bool = True
    note: str = ""

    def is_active(self, now_iso: str) -> bool:
        """当前(now_iso)是否有效:status=active 且未过期。now_iso 由调用方给(不取时钟)。"""
        if self.status != STATUS_ACTIVE:
            return False
        if self.expires_at is None:
            return True
        return now_iso < self.expires_at  # ISO8601 字典序即时间序

    def read_prefixes(self) -> list[str]:
        return [s.prefix for s in self.scopes if s.read]

    def contribute_prefixes(self) -> list[str]:
        return [s.prefix for s in self.scopes if s.contribute]

    def to_dict(self) -> dict[str, Any]:
        d = {
            "schema_version": GRANT_SCHEMA_VERSION,
            "grant_id": self.grant_id,
            "subject": self.subject,
            "scopes": [{"prefix": s.prefix, "read": s.read, "contribute": s.contribute} for s in self.scopes],
            "granted_by": self.granted_by,
            "granted_at": self.granted_at,
            "expires_at": self.expires_at,
            "status": self.status,
            "redact_sensitive": self.redact_sensitive,
            "redact_student_pii": self.redact_student_pii,
            "excluded_top_dirs": list(self.excluded_top_dirs),
            "hide_excluded_in_results": self.hide_excluded_in_results,
            "note": self.note,
        }
        return d

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Grant":
        scopes = tuple(
            GrantScope(
                prefix=str(s["prefix"]),
                read=bool(s.get("read", True)),
                contribute=bool(s.get("contribute", False)),
            )
            for s in d.get("scopes", [])
        )
        return cls(
            grant_id=str(d["grant_id"]),
            subject=str(d["subject"]),
            scopes=scopes,
            granted_by=str(d.get("granted_by", "owner")),
            granted_at=str(d.get("granted_at", "")),
            expires_at=d.get("expires_at"),
            status=str(d.get("status", STATUS_ACTIVE)),
            redact_sensitive=bool(d.get("redact_sensitive", True)),
            redact_student_pii=bool(d.get("redact_student_pii", True)),
            excluded_top_dirs=tuple(d.get("excluded_top_dirs", ("personal-data", "profile"))),
            hide_excluded_in_results=bool(d.get("hide_excluded_in_results", True)),
            note=str(d.get("note", "")),
        )


def grant_to_policy(grant: Grant) -> dict[str, Any]:
    """由 grant 生成库网关 policy(替代手写 studentunion-policy.json)。

    产出与手写 scoped policy 同构:allowed_path_prefixes=读前缀;default_write=deny;
    clients.default.allow=读方法(+ lib.contribute 当任一 scope 有 contribute);
    deny=写/危险方法。redact/excluded 从 grant 带出。**网关唯一入口不变**——grant 只
    决定 policy 内容,不新增旁路。
    """
    allow = list(_READ_METHODS)
    deny = list(_DENY_METHODS)
    if grant.contribute_prefixes():
        allow.append(_CONTRIBUTE_METHOD)  # 授予投稿 => 进 allow
    else:
        deny.append(_CONTRIBUTE_METHOD)   # 未授予 => 显式 deny(与手写只读 policy 一致)
    return {
        "schema_version": 1,
        "_generated_from_grant": grant.grant_id,
        "default_read": "allow",
        "default_write": "deny",
        "excluded_top_dirs": list(grant.excluded_top_dirs),
        "redact_sensitive": grant.redact_sensitive,
        "redact_student_pii": grant.redact_student_pii,
        "hide_excluded_in_results": grant.hide_excluded_in_results,
        "allowed_path_prefixes": grant.read_prefixes(),
        "clients": {"default": {"allow": allow, "deny": deny}},
    }


# --------------------------------------------------------------------- ledger
def load_ledger(text: str) -> list[Grant]:
    """从 JSONL 文本解析 grant 台账。坏行跳过。"""
    out: list[Grant] = []
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            out.append(Grant.from_dict(json.loads(line)))
        except (ValueError, KeyError):
            continue
    return out


def dump_ledger(grants: list[Grant]) -> str:
    return "".join(json.dumps(g.to_dict(), ensure_ascii=False) + "\n" for g in grants)


def owner_audit_view(grants: list[Grant], now_iso: str) -> list[dict[str, Any]]:
    """owner 视角"谁能碰我的库"清单(= GitHub org owner view granted PATs)。无正文。"""
    return [
        {
            "grant_id": g.grant_id,
            "subject": g.subject,
            "read_prefixes": g.read_prefixes(),
            "contribute_prefixes": g.contribute_prefixes(),
            "status": g.status,
            "expires_at": g.expires_at,
            "active": g.is_active(now_iso),
        }
        for g in grants
    ]
