# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J5 不可逆操作两段式协议(plan→approve→apply)可复用 helper。

设计: docs/design/two-phase-irreversible-2026-07.zh-CN.md;config-and-access §3.3。
行业依据(调研):Terraform plan/apply(plan 出可读变更集→apply 精确执行被批准的那份)、
MCP destructiveHint(先列将删内容再确认)。核心:**预览与执行分离 + 执行的是被批准的
那份快照**——先返回人类可读影响面 + 一个 confirm_token(绑定操作+载荷+当前状态指纹),
调用方看清后带 token 再 apply;token 因状态指纹过期(state 变了)即失效(防在陈旧预览上
误执行)。

纯 stdlib、无 IO、无第三方依赖。admin-api 用它保护不可逆操作;brain_library.annotate 与
lib.finalize 是**独立的 conforming 实现**(相同协议、各自实现,以保持各包精简依赖)。
"""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

TOKEN_LEN = 32


def _canonical(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)


def plan_token(op: str, payload: Any, fingerprint: str) -> str:
    """由(操作 + 载荷 + 当前状态指纹)派生 confirm_token。

    ``op``:操作名(如 "unset_secret")。``payload``:操作参数(如 {"path": ...})。
    ``fingerprint``:当前状态指纹(如 ETag / 目标值的哈希 / mtime_ns)——state 变了指纹变、
    token 变、旧 token 失效。确定性:同(op, payload, fingerprint)恒得同 token。
    """
    canonical = f"{op}|{_canonical(payload)}|{fingerprint}"
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:TOKEN_LEN]


def verify_token(op: str, payload: Any, fingerprint: str, token: str) -> bool:
    """常数时间校验 token 是否匹配当前(op, payload, fingerprint)。

    不匹配=token 陈旧(state 在 plan 之后变过)或伪造 => False。调用方据此拒绝 apply。
    """
    expected = plan_token(op, payload, fingerprint)
    return hmac.compare_digest(expected.encode("utf-8"), (token or "").encode("utf-8"))


class StaleTokenError(ValueError):
    """confirm_token 陈旧/缺失/不匹配(在陈旧预览上执行不可逆操作被拒)。"""


def require_token(op: str, payload: Any, fingerprint: str, token: str | None) -> None:
    """verify_token 的抛异常版(fail-closed 调用点用)。"""
    if not token or not verify_token(op, payload, fingerprint, token):
        raise StaleTokenError(
            "confirm_token 缺失或陈旧:先 plan 取预览+token,状态未变时带 token 再执行"
        )
