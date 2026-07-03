# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Central permission gate and audit for the rtime library gateway.

This is the single concentration point for permission decisions. Every gateway
method call (allowed or denied) passes through :func:`enforce` before any
subprocess runs, and every subprocess stdout passes through :func:`redact_output`
afterwards. The audit log records metadata only -- never argument bodies, claim
text, reminder messages, or targets.

The personal-data exclusion, the safe path resolver, and the sensitive-line
regex are ported from ``apps/assistant-gateway/gateway.py`` so the gateway shares
the runtime's defense-in-depth posture even though it never imports it.
"""

from __future__ import annotations

import json
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

JsonObject = dict[str, Any]

# Ported from apps/assistant-gateway/gateway.py (EXCLUDED_TOP_DIRS).
EXCLUDED_TOP_DIRS = {"personal-data"}

# Ported from apps/assistant-gateway/gateway.py (SENSITIVE_TEXT_RE ~111-115).
SENSITIVE_TEXT_RE = re.compile(
    r"(open[_-]?id|union[_-]?id|tenant[_-]?key|app[_-]?secret|api[_-]?key|token|secret|credential|"
    r"password|passwd|私钥|密钥|验证码|身份证|银行卡|target\s*:|ou_[A-Za-z0-9_-]{8,})",
    re.IGNORECASE,
)

REDACTED_LINE = "[redacted sensitive line]"

# 在校学生 PII 的 INLINE 脱敏(A3 决策 1:owner 要求"从数据库/网关层收紧,不靠提示词")。
# 与 SENSITIVE_TEXT_RE 的整行替换不同,这里只抹匹配的 token(学号/政治面貌值/手机/邮箱/
# 身份证),答案其余内容保留可读。由 policy 开关 ``redact_student_pii`` 控制:
#   - 默认 False(内测阶段完全开放,行为不变);
#   - 翻 True 即对所有输出文本做 PII 抹除(studentunion 之类对外实例的收紧姿态)。
# 这是网关输出层的确定性过滤,不依赖模型提示词,面板可管(policy 字段)。
# CJK 相邻处 \b 失效(中文也是 \w,无边界),改用"非字母数字"环视,中英文都稳。
# 顺序:身份证(18位)先于手机(11位),避免 18 位串被手机模式先吃掉一截。
PII_REDACTED = "***"
STUDENT_PII_SUBS: tuple[tuple[re.Pattern[str], str], ...] = (
    # 政治面貌:标签+值 -> 只抹值,值取到标点/空白为止(不贪吃后面的字段)
    (re.compile(r"(政治面貌)\s*[:：]\s*[^\s,，。;；、]+"), r"\1：" + PII_REDACTED),
    # USTC 学号:两位院系字母 + 8 位数字(PB00000001 / SA.. / BA.. 等)
    (re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{8}(?![A-Za-z0-9])"), PII_REDACTED),
    # 18 位身份证(17 位 + 校验位 X/数字)
    (re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)"), PII_REDACTED),
    # 中国大陆手机号
    (re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)"), PII_REDACTED),
    # 邮箱(个人邮箱;机构公开邮箱也抹,收紧优先)
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), PII_REDACTED),
)


def redact_student_pii_text(text: str) -> tuple[str, int]:
    """INLINE 抹除在校学生 PII token,返回 (新文本, 抹除次数)。纯函数。"""
    if not text:
        return text, 0
    total = 0
    for pattern, repl in STUDENT_PII_SUBS:
        text, n = pattern.subn(repl, text)
        total += n
    return text, total

# Arguments that name a brain-content path/root and therefore must be validated
# against the personal-data exclusion (and brain-root containment when relative)
# before any subprocess runs.
#
# ``index`` is deliberately NOT here: the BM25 index is a derived cache that lives
# *outside* the brain root by design (``~/.local/state/rtime-assistant/...``), so
# subjecting it to brain-root containment would reject every real index path. It
# is a machine-local cache the user controls, not brain content, so it is exempt
# from the path gate (it is also resolved from ``BRAIN_LIBRARY_INDEX`` by default).
PATH_LIKE_KEYS = ("path", "root", "source_path", "docpack", "log_path", "inbox", "dest", "src",
                  "from_path", "to_path")

# Arguments that name a brain-relative *prefix* used for LIKE-style index filtering
# (lib.search / lib.recent ``path_prefix``). These need a DIFFERENT check from
# PATH_LIKE_KEYS: a prefix matches by string, so "personal" would match the rows
# under "personal-data/" even though "personal" is not itself an excluded dir name.
# _check_path_prefix handles that prefix semantics; a plain path-component check
# (safe_brain_path) is NOT sufficient here.
PREFIX_LIKE_KEYS = ("path_prefix",)

# --- subset read scope (``allowed_path_prefixes``) --------------------------------
#
# A second consumer (e.g. a student-union bot) gets its own gateway PROCESS with its
# own policy file and port — isolation is process-level, NOT per-client-name (the
# MCP client_id is self-reported and unauthenticated). Such a policy sets
# ``allowed_path_prefixes`` to a non-empty list of brain-relative subtree prefixes;
# the gate then confines every read to those subtrees. An empty/missing list keeps
# the historical full-library behavior (the single-owner deployment).

# Read methods that never read brain content by path — in-process self-describing
# surfaces (doctor/policy/status/preview/audit) and index-metadata-only lib.get —
# stay callable under a scope. lib.status probes other surfaces THROUGH invoke(),
# so its inner calls are scope-gated individually; lib.preview only dry-runs the
# gate (with scope applied) and never executes a backend.
SCOPE_EXEMPT_METHODS = frozenset(
    {"lib.doctor", "lib.policy", "lib.status", "lib.preview", "lib.audit", "lib.get"}
)

# Enumerable read methods whose *omitted* subtree argument means "the whole
# library". Under a non-empty scope the gate must not let that default through:
# with exactly ONE allowed prefix it is injected; with several, the caller must
# pick one explicitly (see _apply_read_scope for why we reject instead of merging).
SCOPE_INJECT_KEYS: dict[str, str] = {
    "lib.search": "path_prefix",  # LIKE-prefix filter (in-process AND subprocess path)
    "lib.recent": "path_prefix",  # LIKE-prefix filter
    "lib.tree": "path",  # brain-relative dir; omitted = brain root listing
    "lib.list": "root",  # scan/docpacks root; omitted = whole brain root
}

# Non-injectable read methods whose backend builder ACTUALLY CONSUMES a brain-path
# argument to constrain what it reads (the value(s) that must land inside the scope
# for the read to be genuinely confined). Keys here are the exact argument names the
# corresponding ``dispatch._build_*`` consumes as a brain-relative path/root. A
# scoped call to one of these is allowed only if it carries one of these keys with
# an in-scope value (validated below); the value is what confines the read.
#
# CRITICAL (defense against a decoy path): a read method NOT in this map and NOT in
# SCOPE_INJECT_KEYS is a full-library aggregate whose builder IGNORES any path
# argument (lib.freshness/courses/context/profile/automation/jobs.*). Passing it an
# in-scope-looking ``path`` does NOT constrain it — it would still read the whole
# library. Such methods CANNOT be confined by scope and are therefore DENIED under
# a non-empty scope (see _apply_read_scope), so the scope layer stands on its own
# and never relies on the client allow-list to backstop these. Kept in sync with
# dispatch._build_* (a static test asserts the classification against the builders).
# NOTE: every key listed here MUST also be in PATH_LIKE_KEYS so the downstream
# per-key scope validation (the PATH_LIKE loop in _apply_read_scope) actually
# confirms the consumed value is in-scope. A key the builder consumes but that no
# scope check validates would be a decoy hole of its own; a static test asserts
# ``all keys ⊆ PATH_LIKE_KEYS``. (This is why lib.hub is NOT here: it reads the
# rtime-hub store via ``root``/``hub_root``, NOT a brain subtree — it cannot be
# confined to a brain scope and is denied like the other non-constrainable reads.)
SCOPE_CONSTRAINABLE_KEYS: dict[str, tuple[str, ...]] = {
    "lib.read": ("path",),
    "lib.stat": ("path",),
    "lib.docpack": ("path",),
    "lib.review": ("path",),
    "lib.runtime": ("path",),
    "lib.meta": ("root",),
    "lib.citation": ("root",),
}

# The method/tier table is the single source of truth shared by the gate and the
# dispatcher. Read methods may only enter READ_DISPATCH; write methods may only
# enter WRITE_DISPATCH (dispatch.py asserts the key sets are disjoint).
METHOD_TIERS: dict[str, str] = {
    # read
    "lib.doctor": "read",
    "lib.search": "read",
    "lib.courses": "read",
    "lib.get": "read",
    "lib.read": "read",
    "lib.tree": "read",
    "lib.stat": "read",
    "lib.recent": "read",
    "lib.freshness": "read",
    "lib.policy": "read",
    "lib.audit": "read",
    "lib.preview": "read",
    "lib.list": "read",
    "lib.meta": "read",
    "lib.docpack": "read",
    "lib.citation": "read",
    "lib.hub": "read",
    "lib.context": "read",
    "lib.profile": "read",
    "lib.review": "read",
    "lib.automation": "read",
    "lib.runtime": "read",
    "lib.status": "read",
    # read: query the local job queue (status/result of deferred long tasks)
    "lib.jobs.get": "read",
    "lib.jobs.list": "read",
    # write (deploy/bin narrow tools)
    "lib.settings.context_source_list": "write",
    "lib.settings.context_source_check": "write",
    "lib.settings.context_source_add": "write",
    "lib.settings.context_source_deactivate": "write",
    "lib.settings.memory_candidate_add": "write",
    "lib.settings.reminder_register": "write",
    "lib.settings.reminder_list": "write",
    "lib.settings.reminder_cancel": "write",
    # brain-content write: stage a note into _inbox via deploy/bin/rtime-contribute
    "lib.contribute": "write",
    # brain-content write: finalize a staged _inbox item into knowledge/ (owner-token
    # gated) via deploy/bin/rtime-finalize. Only plan/apply are reachable here; the
    # owner-only `approve` step is intentionally NOT a gateway method.
    "lib.finalize": "write",
    # brain-content write: ingest a course folder from _inbox into knowledge/courses/<id>
    # with auto slides/lectures/exams classify (owner-token gated) via rtime-course-intake.
    "lib.course-intake": "write",
    # local-state write: enqueue a deferred long task into the machine-local job
    # queue via deploy/bin/rtime-jobs-submit. Writes NO brain content and grants no
    # new authority — a queued course-intake-apply still carries an owner-approved
    # plan_sha that the worker re-checks. The heavy work runs in a separate worker.
    "lib.jobs.submit": "write",
    # brain-content DIRECT write (H M1): annotate frontmatter-only, two-phase
    # plan/apply, in-process (brain_library.annotate). 超管实例(owner/开发助手)专用;
    # scoped(非空 allowed_path_prefixes)一律拒 —— 见 SCOPE_DENIED_WRITE_METHODS。
    "lib.annotate": "write",
    # brain-content DIRECT write (H M2): edit body / revert to a revision, two-phase
    # plan/apply, in-process (brain_library.edit). 同 annotate:超管专用,scoped 一律拒。
    "lib.edit": "write",
    "lib.revert": "write",
    # revision chain read (H M2): 纯读某路径的修订链(brain_library.edit.list_revisions)。
    "lib.revisions": "read",
    # brain-content DIRECT write (H M3): move/rename with reference-integrity scan +
    # tombstone redirect, and retire (soft-delete into _archive/) + restore, two-phase
    # plan/apply, in-process (brain_library.maintain). 同 edit:超管专用,scoped 一律拒。
    "lib.move": "write",
    "lib.retire": "write",
    "lib.restore": "write",
}

# Direct brain-content writes are NEVER available under a subset scope: a grantee
# (e.g. the studentunion instance) contributes via lib.contribute -> owner review,
# it does not edit library files in place. Enforced in enforce() regardless of the
# scoped policy's default_write, so a mis-generated policy cannot fail open.
SCOPE_DENIED_WRITE_METHODS = frozenset(
    {"lib.annotate", "lib.edit", "lib.revert", "lib.move", "lib.retire", "lib.restore"}
)


_DEFAULT_POLICY: JsonObject = {
    "schema_version": 1,
    "default_read": "allow",
    "default_write": "allow",
    "excluded_top_dirs": sorted(EXCLUDED_TOP_DIRS),
    "redact_sensitive": True,
    # A3 决策 1(owner):在校学生 PII(学号/政治面貌/手机/邮箱/身份证)的输出层 INLINE
    # 抹除开关。默认 False = 内测阶段完全开放(应请求可查同学);翻 True = 收紧(studentunion
    # 之类对外实例)。网关层控制,不靠模型提示词,面板可管。
    "redact_student_pii": False,
    # Reserved, OFF by default: when true, lib.search / lib.recent results drop rows
    # under an excluded top dir (personal-data) so agents never see their paths/titles.
    # Off here keeps the full library visible (single-owner; owner reads brain directly).
    "hide_excluded_in_results": False,
    # Non-empty = confine every read to these brain-relative subtrees (see the
    # SCOPE_* tables above). Empty = full library (single-owner default).
    "allowed_path_prefixes": [],
    "audit_log": "{STATE}/rtime-library-gateway/audit.jsonl",
    "methods": {name: {"tier": tier, "enabled": True} for name, tier in METHOD_TIERS.items()},
    "clients": {"default": {"allow": ["*"]}},
}


class GateError(Exception):
    """A permission/validation failure surfaced as an MCP tool error."""


class PolicyDenied(GateError):
    """The active policy denied this method or argument."""


def load_policy() -> JsonObject:
    """Resolve the active policy.

    Order: ``RTIME_LIBRARY_GATEWAY_POLICY`` env -> repository default at
    ``packages/rtime-library-gateway/policy/library-gateway-policy.json`` ->
    builtin ``_DEFAULT_POLICY`` (so the gateway works with zero configuration).

    FAIL-CLOSED on an EXPLICITLY named policy (env ``RTIME_LIBRARY_GATEWAY_POLICY``):
    if that file is missing / unreadable / malformed / not a JSON object, this
    raises :class:`GateError` rather than falling through to a wider default. The
    scoped 8781 public gateway explicitly names studentunion-policy.json; silently
    degrading a broken scoped policy to the full-library owner default (empty
    ``allowed_path_prefixes``) would serve the WHOLE library — a privacy fail-open.
    A security policy that is explicitly configured must never be downgraded to a
    broader one; a load failure there is fatal. The candidate fallback chain
    (repo default -> builtin) applies ONLY when NO policy was explicitly named
    (the single-owner zero-config case)."""
    raw = os.environ.get("RTIME_LIBRARY_GATEWAY_POLICY")
    if raw:
        # Explicitly named -> load exactly this file or fail closed. No fallback.
        named = Path(raw).expanduser()
        try:
            text = named.read_text(encoding="utf-8")
        except OSError as exc:
            raise GateError(
                f"named policy file cannot be read (fail-closed, no fallback): {raw}: {exc}"
            ) from exc
        try:
            loaded = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GateError(
                f"named policy file is not valid JSON (fail-closed, no fallback): {raw}: {exc}"
            ) from exc
        if not isinstance(loaded, dict):
            raise GateError(
                f"named policy file is not a JSON object (fail-closed, no fallback): {raw}"
            )
        return loaded
    # No explicit policy named -> the single-owner zero-config path may fall back
    # from the repo default to the builtin _DEFAULT_POLICY.
    repo_default = Path(__file__).resolve().parents[2] / "policy" / "library-gateway-policy.json"
    try:
        loaded = json.loads(repo_default.read_text(encoding="utf-8"))
        if isinstance(loaded, dict):
            return loaded
    except (OSError, json.JSONDecodeError):
        pass
    return json.loads(json.dumps(_DEFAULT_POLICY))


def _method_entry(policy: JsonObject, method: str) -> JsonObject:
    methods = policy.get("methods")
    if isinstance(methods, dict):
        entry = methods.get(method)
        if isinstance(entry, dict):
            return entry
    return {}


def method_tier(policy: JsonObject, method: str) -> str | None:
    entry = _method_entry(policy, method)
    tier = entry.get("tier")
    if isinstance(tier, str) and tier:
        return tier
    return METHOD_TIERS.get(method)


def _excluded_top_dirs(policy: JsonObject) -> set[str]:
    raw = policy.get("excluded_top_dirs")
    # An explicit list is authoritative, INCLUDING an empty one: a single-owner
    # deployment that wants nothing gated sets ``"excluded_top_dirs": []`` and must
    # get an empty set back (open everything), not the built-in default. Only a
    # missing / non-list value falls back to the built-in EXCLUDED_TOP_DIRS.
    if isinstance(raw, list):
        return {str(item) for item in raw if str(item)}
    return set(EXCLUDED_TOP_DIRS)


def safe_brain_path(
    raw: str,
    brain_root: Path,
    *,
    excluded_top_dirs: set[str] | None = None,
    require_exists: bool = False,
) -> Path | None:
    """Resolve ``raw`` against ``brain_root`` and reject escapes/personal-data.

    Ported from ``apps/assistant-gateway/gateway.py`` (safe_brain_path ~376-400).
    Returns ``None`` when the path is external, escapes the brain root, lands in
    an excluded top directory (personal-data), or -- when ``require_exists`` --
    does not exist. Absolute inputs are kept only if they already resolve under
    the brain root.
    """
    excluded = excluded_top_dirs if excluded_top_dirs is not None else set(EXCLUDED_TOP_DIRS)
    if not raw or raw.startswith(("http://", "https://", "zotero://")):
        return None
    raw = raw.strip()
    try:
        resolved = (brain_root / raw).resolve()
        root = brain_root.resolve()
    except OSError:
        return None
    if not resolved.is_relative_to(root):
        return None
    rel = resolved.relative_to(root)
    if rel.parts and _is_excluded_top(rel.parts[0], excluded):
        return None
    if require_exists and not resolved.exists():
        return None
    return resolved


def _is_excluded_top(part: str, excluded: set[str]) -> bool:
    """Case-insensitive membership check for an excluded top directory.

    The exclusion must hold on case-insensitive filesystems (Windows/macOS, where
    this code is developed): ``Personal-Data`` and ``PERSONAL-DATA`` resolve to the
    same on-disk directory as ``personal-data`` and must be rejected identically."""
    p = part.lower()
    return any(p == e.lower() for e in excluded)


def _path_basenames(arguments: JsonObject) -> list[str]:
    names: list[str] = []
    for key in PATH_LIKE_KEYS:
        value = arguments.get(key)
        if isinstance(value, str) and value.strip():
            names.append(Path(value.strip()).name)
    return names


def _check_paths(policy: JsonObject, arguments: JsonObject, brain_root: Path) -> None:
    """Reject any path-like argument that escapes the brain root or names
    personal-data. This runs *before* the subprocess as defense in depth with
    each underlying tool's own ``_safe_source_path`` self-check."""
    excluded = _excluded_top_dirs(policy)
    for key in PATH_LIKE_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if text.startswith(("http://", "https://", "zotero://")):
            raise PolicyDenied(f"external paths are not allowed: {key}")
        # Absolute paths under the brain root are tolerated; everything else that
        # is absolute is rejected (matches the narrow write tools' contract).
        candidate = Path(text)
        if candidate.is_absolute():
            try:
                resolved = candidate.resolve()
                root = brain_root.resolve()
            except OSError as exc:
                raise PolicyDenied(f"path cannot be resolved: {key}: {exc}") from exc
            if not resolved.is_relative_to(root):
                raise PolicyDenied(f"absolute paths are not allowed: {key}")
            rel = resolved.relative_to(root)
            if rel.parts and _is_excluded_top(rel.parts[0], excluded):
                raise PolicyDenied(f"personal-data is not allowed: {key}")
            continue
        if safe_brain_path(text, brain_root, excluded_top_dirs=excluded) is None:
            raise PolicyDenied(f"path is not allowed (escape or personal-data): {key}")


def _check_path_prefix(policy: JsonObject, arguments: JsonObject) -> None:
    """Reject a ``path_prefix`` filter that could match an excluded subtree.

    ``path_prefix`` feeds a SQL ``path LIKE 'prefix%'`` filter over the index, which
    covers the FULL library including personal-data. Because matching is by string
    prefix, a personal-data row ``personal-data/health/x.md`` is matched not only by
    ``personal-data`` but by ANY string that is a prefix of it (e.g. ``personal``),
    or by any prefix pointing inside it. A plain path-component check (PATH_LIKE_KEYS)
    would wave ``personal`` through, so this dedicated prefix-semantics check is the
    one that actually closes the enumeration hole. Case-insensitive to match the
    filesystem; also rejects absolute/external/``..`` prefixes.
    """
    excluded = {e.lower() for e in _excluded_top_dirs(policy)}
    for key in PREFIX_LIKE_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        if text.startswith(("http://", "https://", "zotero://")) or Path(text).is_absolute():
            raise PolicyDenied(f"prefix is not allowed (absolute/external): {key}")
        norm = text.replace("\\", "/").lstrip("/").lower()
        if ".." in norm.split("/"):
            raise PolicyDenied(f"prefix may not contain '..': {key}")
        for excl in excluded:
            # 'prefix%' matches an excluded subtree iff prefix is a prefix of the
            # excluded name, equals it, or points inside it.
            if excl.startswith(norm) or norm == excl or norm.startswith(excl + "/"):
                raise PolicyDenied(f"prefix may not target personal-data: {key}")


def _scope_norm(text: str) -> str:
    """Normalize a scope prefix / relative path for comparison: unify slashes and
    strip the leading/trailing ones. Case is preserved (matching casefolds)."""
    return text.strip().replace("\\", "/").strip("/")


def _allowed_path_prefixes(policy: JsonObject) -> list[str]:
    """The normalized subset-read scope. Empty list = scope OFF (full library).

    Entries are normalized but NOT dropped: a junk entry (empty string, ``..``)
    simply never matches anything, so a policy whose list is non-empty but
    malformed fails CLOSED (every read denied) instead of silently reopening the
    full library."""
    raw = policy.get("allowed_path_prefixes")
    if not isinstance(raw, list):
        return []
    return [_scope_norm(str(item)) for item in raw]


def _usable_scope_prefixes(prefixes: list[str]) -> list[str]:
    return [p for p in prefixes if p and ".." not in p.split("/")]


def _in_scope(rel: str, usable_prefixes: list[str]) -> bool:
    """True iff brain-relative posix path ``rel`` equals or sits under one of the
    scope prefixes. Case-insensitive: consistent with _is_excluded_top (the check
    must hold on case-insensitive filesystems) and with SQLite's ASCII
    case-insensitive LIKE that the path_prefix filter feeds."""
    rel_l = rel.lower()
    for prefix in usable_prefixes:
        pl = prefix.lower()
        if rel_l == pl or rel_l.startswith(pl + "/"):
            return True
    return False


def _scope_rel(text: str, brain_root: Path | None) -> str | None:
    """Resolve a path argument to its brain-relative posix form for scope checks.

    Mirrors _check_paths resolution (absolute kept only under the brain root;
    relative resolved against it). Returns ``None`` when the path cannot be
    proven inside the brain root — the scope check then fails CLOSED. Without a
    brain root only textual normalization of relative paths is possible;
    absolute paths are rejected outright."""
    candidate = Path(text)
    if brain_root is not None:
        try:
            root = brain_root.resolve()
            resolved = candidate.resolve() if candidate.is_absolute() else (brain_root / text).resolve()
        except OSError:
            return None
        if not resolved.is_relative_to(root):
            return None
        return resolved.relative_to(root).as_posix()
    if candidate.is_absolute():
        return None
    norm = _scope_norm(text)
    if ".." in norm.split("/"):
        return None
    return norm


def _apply_read_scope(
    policy: JsonObject, method: str, arguments: JsonObject, brain_root: Path | None
) -> None:
    """Enforce (and where needed inject) the subset read scope for one read call.

    No-op when ``allowed_path_prefixes`` is empty (full backward compatibility).
    Under a non-empty scope, for read methods:

    - every provided path-like argument must resolve inside a scope prefix;
    - every provided ``path_prefix`` must equal or sit under a scope prefix
      (a prefix that merely string-prefixes a scope prefix, like ``knowledge``,
      would LIKE-match rows outside the scope and is denied);
    - a ``path_prefix`` exactly at a scope boundary is pinned with a trailing
      slash, because SQL ``LIKE 'p%'`` would otherwise also match a SIBLING dir
      sharing the name prefix (``knowledge/activities`` matches
      ``knowledge/activities-2026/...``);
    - enumerable methods (SCOPE_INJECT_KEYS) whose subtree argument is omitted
      get the single scope prefix injected; with SEVERAL prefixes the call is
      denied with the allowed list in the message. We deny rather than fan out
      one query per prefix and merge: the search runs through two code paths
      (in-process query_index and the subprocess CLI — a known drift trap) and
      BM25/RRF scores from separate queries are not comparable, so a merge
      would be both duplicated and unprincipled. Callers just pass one prefix.
    - other read methods called with NO path argument at all are denied: their
      implicit default root (brain root, knowledge/, _meta, the full index...)
      is outside the caller's subtree, and the gate cannot narrow them.

    Write methods are NOT scope-checked here (enforce only calls this for the
    read tier): a scoped deployment denies writes wholesale via policy
    (``default_write: deny`` + client deny globs), which is a stronger cut.

    NOTE: injection MUTATES ``arguments`` in place — that is the single point
    through which both the in-process lib.search path and every subprocess
    builder receive the narrowed argument.
    """
    prefixes = _allowed_path_prefixes(policy)
    if not prefixes:
        return

    # index-reject (P5 阶段0 / H1): under a non-empty scope, the ONLY backstop
    # confining reads to the scope is the path_prefix LIKE filter, and that filter
    # follows whichever index the query names. ``index`` is deliberately exempt
    # from PATH_LIKE_KEYS (it is a derived cache outside the brain root, see the
    # module docstring), so nothing else validates it. A scoped caller that can
    # reach any full-library index file could therefore re-point ``index`` at it
    # and read the whole library outside its path_prefix. Fail closed: a scoped
    # caller may NOT name the index at all — the gateway forces its own
    # server-side default_index(). This runs BEFORE the SCOPE_EXEMPT_METHODS
    # early-return so lib.get (which also accepts ``index``, gap H2) is covered too.
    idx = arguments.get("index")
    if isinstance(idx, str) and idx.strip():
        raise PolicyDenied(
            "scoped gateway: caller may not specify an index; the gateway uses "
            "its own server-side default index for every scoped read"
        )

    if method in SCOPE_EXEMPT_METHODS:
        return
    usable = _usable_scope_prefixes(prefixes)
    scope_desc = ", ".join(usable) if usable else "<no usable prefixes: scope denies all reads>"

    inject_key = SCOPE_INJECT_KEYS.get(method)
    if inject_key is not None:
        current = arguments.get(inject_key)
        if not isinstance(current, str) or not current.strip():
            if len(usable) != 1:
                raise PolicyDenied(
                    f"scoped gateway: {method} needs an explicit {inject_key} inside the "
                    f"allowed scope; allowed prefixes: {scope_desc}"
                )
            prefix = usable[0]
            if inject_key == "root":
                # lib.list's root is handed to the backend as a filesystem path
                # (relative would resolve against the subprocess CWD, not the
                # brain root) -> inject the absolute subtree under the brain root.
                if brain_root is None:
                    raise PolicyDenied(
                        f"scoped gateway: cannot scope {method} without a brain root"
                    )
                arguments[inject_key] = str(brain_root / prefix)
            elif inject_key == "path_prefix":
                # Trailing slash pins the LIKE pattern to the subtree (see docstring).
                arguments[inject_key] = prefix + "/"
            else:  # brain-relative dir (lib.tree)
                arguments[inject_key] = prefix
    else:
        # A non-injectable read is confinable ONLY if its backend builder actually
        # consumes a brain-path argument to constrain the read. Look at the keys the
        # builder consumes (SCOPE_CONSTRAINABLE_KEYS), not "any path-like string in
        # the arguments": a method whose builder ignores path (full-library
        # aggregate: freshness/courses/context/profile/automation/jobs.*) would run
        # the whole library even with an in-scope-looking decoy ``path``. Such
        # methods cannot be scoped and are DENIED — the scope layer confines every
        # read on its own, without relying on the client allow-list to catch them.
        constrainable = SCOPE_CONSTRAINABLE_KEYS.get(method)
        if not constrainable:
            raise PolicyDenied(
                f"scoped gateway: {method} cannot be confined to the allowed scope "
                f"(it reads a full-library aggregate and ignores any path argument); "
                f"denied under scope (allowed prefixes: {scope_desc})"
            )
        provided = any(
            isinstance(arguments.get(key), str) and str(arguments.get(key)).strip()
            for key in constrainable
        )
        if not provided:
            raise PolicyDenied(
                f"scoped gateway: {method} needs an in-scope "
                f"{' or '.join(constrainable)} argument to confine the read "
                f"(allowed prefixes: {scope_desc})"
            )

    for key in PREFIX_LIKE_KEYS:
        raw = arguments.get(key)
        if not isinstance(raw, str) or not raw.strip():
            continue
        text = raw.strip()
        if text.startswith(("http://", "https://", "zotero://")) or Path(text).is_absolute():
            raise PolicyDenied(
                f"scoped gateway: {key} must be a brain-relative prefix inside the "
                f"allowed scope ({scope_desc})"
            )
        norm = _scope_norm(text)
        if not norm or ".." in norm.split("/") or not _in_scope(norm, usable):
            raise PolicyDenied(
                f"scoped gateway: {key} is outside the allowed scope ({scope_desc})"
            )
        if any(norm.lower() == p.lower() for p in usable):
            arguments[key] = norm + "/"

    for key in PATH_LIKE_KEYS:
        value = arguments.get(key)
        if not isinstance(value, str) or not value.strip():
            continue
        text = value.strip()
        if text.startswith(("http://", "https://", "zotero://")):
            continue  # _check_paths rejects external URLs with its own message
        rel = _scope_rel(text, brain_root)
        if rel is None or not _in_scope(rel, usable):
            raise PolicyDenied(
                f"scoped gateway: {key} is outside the allowed scope ({scope_desc})"
            )


def _client_allows(policy: JsonObject, method: str, client_id: str) -> bool:
    clients = policy.get("clients")
    if not isinstance(clients, dict):
        return True
    rule = clients.get(client_id)
    if not isinstance(rule, dict):
        rule = clients.get("default")
    if not isinstance(rule, dict):
        return True
    deny = rule.get("deny")
    if isinstance(deny, list) and _glob_match(method, deny):
        return False
    allow = rule.get("allow")
    if isinstance(allow, list) and allow:
        return _glob_match(method, allow)
    return True


def _glob_match(method: str, patterns: list[Any]) -> bool:
    for pattern in patterns:
        if not isinstance(pattern, str) or not pattern:
            continue
        if pattern == "*":
            return True
        if pattern.endswith(".*") and method.startswith(pattern[:-1]):
            return True
        if pattern == method:
            return True
    return False


def enforce(
    method: str,
    arguments: JsonObject,
    client_id: str,
    *,
    policy: JsonObject | None = None,
    brain_root: Path | None = None,
) -> JsonObject:
    """Run the central permission checks for one method call.

    Returns a decision descriptor ``{"tier": str, "client_id": str}`` when the
    call is allowed, or raises :class:`GateError`/:class:`PolicyDenied` when it
    is not. Order mirrors apps/assistant-gateway/gateway.py:
    (1) method exists + tier lookup, (2) enabled flag, (3) tier switch,
    (4) client allow/deny, (5) subset read scope (may INJECT a narrowed
    path/path_prefix/root into ``arguments`` — see :func:`_apply_read_scope`),
    (6) personal-data path exclusion. The scope step runs BEFORE the exclusion
    checks so injected values are themselves re-validated (a policy that scopes
    into an excluded dir fails closed).
    """
    policy = policy if policy is not None else load_policy()
    tier = method_tier(policy, method)
    if tier is None:
        raise GateError(f"unknown method: {method}")

    entry = _method_entry(policy, method)
    if entry.get("enabled") is False:
        raise PolicyDenied(f"method disabled by policy: {method}")

    if tier == "read":
        if str(policy.get("default_read", "allow")).lower() == "deny":
            raise PolicyDenied("read tier disabled by policy")
    elif tier == "write":
        if str(policy.get("default_write", "allow")).lower() == "deny":
            raise PolicyDenied("write tier disabled by policy")
    else:
        raise GateError(f"unknown tier for method {method}: {tier}")

    if not _client_allows(policy, method, client_id):
        raise PolicyDenied(f"client not permitted for method: {client_id} -> {method}")

    if method in SCOPE_DENIED_WRITE_METHODS and _allowed_path_prefixes(policy):
        raise PolicyDenied(
            f"direct brain write denied under scoped policy: {method} "
            "(grantee writes go through lib.contribute -> owner review)"
        )

    if tier == "read":
        _apply_read_scope(policy, method, arguments, brain_root)

    if brain_root is not None:
        _check_paths(policy, arguments, brain_root)
    _check_path_prefix(policy, arguments)

    return {"tier": tier, "client_id": client_id}


def redact_output(
    text: str, *, force: bool = False, redact: bool = True, pii: bool = False
) -> tuple[str, int]:
    """Replace any line matching :data:`SENSITIVE_TEXT_RE` with a placeholder.

    ``force`` keeps redaction on even if ``redact`` is False (used for the
    ``contacts`` lane). ``pii`` additionally applies INLINE student-PII scrubbing
    (learn/political-status/phone/email/ID) — independent of ``redact``, driven by
    the policy switch ``redact_student_pii``. Returns ``(redacted_text, count)``.
    """
    if not text:
        return text, 0
    redacted = 0
    if redact or force:
        out_lines: list[str] = []
        for line in text.splitlines():
            if SENSITIVE_TEXT_RE.search(line):
                out_lines.append(REDACTED_LINE)
                redacted += 1
            else:
                out_lines.append(line)
        joined = "\n".join(out_lines)
        if text.endswith("\n"):
            joined += "\n"
        text = joined
    if pii:
        text, n = redact_student_pii_text(text)
        redacted += n
    return text, redacted


def redact_json(
    value: Any, *, force: bool = False, redact: bool = True, pii: bool = False
) -> tuple[Any, int]:
    """Redact sensitive strings inside structured JSON values.

    MCP clients receive both a rendered ``content`` block and
    ``structuredContent``. Line redaction protects the rendered block; this
    recursive pass keeps structured content from carrying the same sensitive
    strings through a different channel. ``pii`` mirrors :func:`redact_output`.
    """
    if not redact and not force and not pii:
        return value, 0
    if isinstance(value, str):
        # 敏感行整行替换(仅当 redact/force);PII token 抹除(仅当 pii)。分开算,互不吞没。
        line_count = 0
        if redact or force:
            _, line_count = redact_output(value, force=True, redact=True)
            if line_count:
                if "\n" not in value:
                    return REDACTED_LINE, line_count
                value, _ = redact_output(value, force=True, redact=True)
        if pii:
            value, pii_count = redact_student_pii_text(value)
            return value, line_count + pii_count
        return value, line_count
    if isinstance(value, list):
        total = 0
        redacted_items: list[Any] = []
        for item in value:
            redacted_item, count = redact_json(item, force=force, redact=redact, pii=pii)
            redacted_items.append(redacted_item)
            total += count
        return redacted_items, total
    if isinstance(value, dict):
        total = 0
        redacted_dict: JsonObject = {}
        for key, item in value.items():
            redacted_item, count = redact_json(item, force=force, redact=redact, pii=pii)
            redacted_dict[key] = redacted_item
            total += count
        return redacted_dict, total
    return value, 0


def _audit_log_path(policy: JsonObject) -> Path | None:
    raw = os.environ.get("RTIME_LIBRARY_GATEWAY_AUDIT_LOG")
    if not raw:
        configured = policy.get("audit_log")
        if isinstance(configured, str) and configured:
            raw = configured
    if not raw:
        return None
    state_dir = Path(
        os.environ.get("XDG_STATE_HOME", "~/.local/state")
    ).expanduser()
    return Path(raw.replace("{STATE}", str(state_dir))).expanduser()


def record_audit(
    *,
    method: str,
    client_id: str,
    tier: str,
    decision: str,
    exit_code: int | None,
    duration_ms: int,
    arguments: JsonObject,
    redacted_line_count: int,
    policy: JsonObject | None = None,
) -> JsonObject | None:
    """Append one metadata-only JSONL audit line.

    Records only: audit_id, ts, client_id, method, tier, decision, exit_code,
    duration_ms, input_path_basenames, redacted_line_count. Never argument
    bodies, claim text, reminder messages, or targets.
    """
    policy = policy if policy is not None else load_policy()
    record = {
        "audit_id": f"libgw-{uuid.uuid4().hex}",
        "ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "client_id": client_id,
        "method": method,
        "tier": tier,
        "decision": decision,
        "exit_code": exit_code,
        "duration_ms": duration_ms,
        "input_path_basenames": _path_basenames(arguments),
        "redacted_line_count": redacted_line_count,
    }
    path = _audit_log_path(policy)
    if path is None:
        return record
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")
    return record
