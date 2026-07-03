# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Unified library access gateway MCP stdio server.

Clones the rtime read-tool MCP idiom (handle_message / negotiate / serve_stdio /
tools / call_tool) and exposes a curated ``lib.*`` namespace. Every ``tools/call``
routes through the central gate, dispatches to the matching read CLI or one of
the three narrow-write executables, redacts the subprocess output, and records
one metadata-only audit line.
"""

from __future__ import annotations

import contextlib
import http.server
import json
import os
import select
import socket
import socketserver
import sys
import threading
import time
from typing import Any, Sequence

from . import dispatch as dispatch_mod
from . import gate as gate_mod
from .cli import doctor as cli_doctor

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "rtime-library-gateway"
SERVER_VERSION = "0.1.0"

JsonObject = dict[str, Any]

# Methods implemented in-process (no subprocess, no brain access).
_INPROCESS = {"lib.doctor", "lib.status", "lib.policy", "lib.audit", "lib.preview",
              "lib.annotate", "lib.edit", "lib.revert", "lib.revisions",
              "lib.move", "lib.retire", "lib.restore"}


# --- MCP wire tool names -------------------------------------------------------
# The Anthropic API (claude.ai connectors, Claude Desktop) validates every tool
# name against ``^[a-zA-Z0-9_-]{1,64}$`` and rejects the request if any name
# fails — dots are NOT allowed. Our canonical method ids are dotted
# (``lib.search``, ``lib.settings.reminder_list``); they are the single source of
# truth shared by gate.py, dispatch.py, the audit log, and the CLI, so we keep
# them dotted internally. At the MCP boundary only, we advertise an API-valid
# WIRE name (dots -> underscores) in ``tools/list`` and translate it back to the
# canonical dotted id on ``tools/call``. Claude Code, which sanitizes dotted
# names on its own side, is unaffected; the claude.ai/Desktop connector path now
# receives valid names. ``_canonical_method`` also passes a dotted name through
# unchanged, so any caller that still sends the dotted form keeps working.
def _wire_name(method: str) -> str:
    return method.replace(".", "_")


_CANONICAL_FROM_WIRE: dict[str, str] = {_wire_name(m): m for m in gate_mod.METHOD_TIERS}
# Fail fast at import if the dot->underscore encoding ever stops being injective
# (two canonical ids collapsing to the same wire name would silently mis-route).
assert len(_CANONICAL_FROM_WIRE) == len(gate_mod.METHOD_TIERS), (
    "tool-name wire encoding is not injective: two methods collide after . -> _"
)


def _canonical_method(name: str) -> str:
    """Map an MCP wire tool name back to the canonical dotted method id.

    Accepts either the wire form (``lib_search``) or the canonical dotted form
    (``lib.search``); an unknown name is returned unchanged so the gate reports
    it as ``unknown method`` exactly as before.
    """
    return _CANONICAL_FROM_WIRE.get(name, name)


def _negotiate_protocol_version(client_version: Any) -> str:
    if isinstance(client_version, str) and client_version:
        return client_version
    return PROTOCOL_VERSION


class ToolError(Exception):
    """A tool-level failure returned as an MCP tool error."""


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _tool_result(data: JsonObject, *, is_error: bool = False, render_text: str | None = None) -> JsonObject:
    text = render_text if render_text is not None else json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
    return {
        "content": [{"type": "text", "text": text}],
        "structuredContent": data,
        "isError": is_error,
    }


def _response(request_id: Any, result: JsonObject) -> JsonObject:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str, data: Any = None) -> JsonObject:
    error: JsonObject = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _schema(properties: JsonObject, required: list[str] | None = None) -> JsonObject:
    return {
        "type": "object",
        "properties": properties,
        "required": required or [],
        "additionalProperties": True,
    }


_OP = lambda values, default: {  # noqa: E731 - compact schema helper
    "type": "string",
    "enum": sorted(values),
    "description": f"operation; defaults to {default}",
}
_ROOT = {"type": "string", "description": "optional root path (rejected if it escapes brain root or names personal-data)"}
_REQUEST = {"type": "string", "description": "user request or task text"}


class RtimeLibraryGatewayMCP:
    # Set by _maybe_prewarm() at serve_stdio startup; serializes the background
    # warm thread against the first real lib.search so the embedder loads once.
    _search_lock: "threading.Lock | None" = None

    def tools(self) -> list[JsonObject]:
        return [
            {
                "name": "lib.doctor",
                "title": "Library Gateway Doctor",
                "description": "In-process self-check: resolved roots, policy load, underlying CLI importability. Never reads the brain.",
                "inputSchema": _schema({}),
            },
            {
                "name": "lib.status",
                "title": "Library Status",
                "description": "Aggregate doctor across the curated read surfaces through the gate, without reading sensitive bodies. Pass quick=true for a fast (sub-200ms) liveness check of just the search-index surface.",
                "inputSchema": _schema(
                    {"quick": {"type": "boolean", "description": "OPTIONAL — probe only the library/index surface for a fast liveness check (default false = full multi-surface probe)"}}
                ),
            },
            {
                "name": "lib.search",
                "title": "Library Search",
                "description": (
                    "Search the rtime brain knowledge library (BM25 lexical + semantic-vector hybrid, "
                    "Chinese-tokenized). Use this whenever the user wants to look something up in their "
                    "library / brain / 知识库 / 资料 — it finds which document a concept or topic lives in, "
                    "matching both exact wording (BM25) and meaning/synonyms (vectors). Returns "
                    "brain-relative paths + snippets."
                ),
                "inputSchema": _schema(
                    {
                        "query": {"type": "string", "description": "search query (keywords / concept; Chinese ok)"},
                        "limit": {"type": "integer", "description": "max hits (default 10)"},
                        "mode": {"type": "string", "enum": ["bm25", "vector", "hybrid"], "description": "OPTIONAL — retrieval mode; default hybrid (BM25+semantic) when the index has vectors, else bm25. Use 'bm25' for exact-wording lookups, 'vector' for pure semantic."},
                        "suffix": {"type": "string", "description": "OPTIONAL — restrict to one file type, e.g. md, pdf, bib"},
                        "path_prefix": {"type": "string", "description": "OPTIONAL — restrict to a brain-relative subtree, e.g. knowledge/courses"},
                        "title_only": {"type": "boolean", "description": "OPTIONAL — match the query against document titles only (BM25)"},
                        "doc_type": {"type": "string", "description": "OPTIONAL — filter by frontmatter type, e.g. ustc-program, ustc-notice"},
                        "dept": {"type": "string", "description": "OPTIONAL — filter by department / institution"},
                        "category": {"type": "string", "description": "OPTIONAL — filter by category, e.g. 规章制度, 通知公告"},
                        "date_from": {"type": "string", "description": "OPTIONAL — min publish_date YYYY-MM-DD (inclusive)"},
                        "date_to": {"type": "string", "description": "OPTIONAL — max publish_date YYYY-MM-DD (inclusive)"},
                        "order_by": {"type": "string", "description": "OPTIONAL — 'relevance' (default) or 'date' (newest first)"},
                        "index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index (default). Only set to point at a non-standard SQLite index path."},
                    },
                    required=["query"],
                ),
            },
            {
                "name": "lib.courses",
                "title": "Curriculum Course Query",
                "description": (
                    "Structured query over USTC 培养方案 course rows (code/name/credits/学时/module/"
                    "required/program/grade). Use for precise curriculum questions full-text search "
                    "can't answer: which majors require a course (code=PHYS1001B), a major's course "
                    "list (dept+grade), heavy courses (min_credits). Returns structured rows."
                ),
                "inputSchema": _schema(
                    {
                        "code": {"type": "string", "description": "exact course code, e.g. PHYS1001B — finds every program listing it"},
                        "name_like": {"type": "string", "description": "OPTIONAL — course name substring"},
                        "dept": {"type": "string", "description": "OPTIONAL — filter by department"},
                        "grade": {"type": "string", "description": "OPTIONAL — filter by grade/year, e.g. 2023"},
                        "min_credits": {"type": "number", "description": "OPTIONAL — minimum credits"},
                        "required_only": {"type": "boolean", "description": "OPTIONAL — required (必修) courses only"},
                        "program_path": {"type": "string", "description": "OPTIONAL — exact program note path"},
                        "limit": {"type": "integer", "description": "max rows (default 200)"},
                        "index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index"},
                    }
                ),
            },
            {
                "name": "lib.get",
                "title": "Library Index Status",
                "description": "Read the brain index metadata (document count, tokenizer, when it was built). Use to understand what the search index covers.",
                "inputSchema": _schema(
                    {"index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index"}},
                    required=[],
                ),
            },
            {
                "name": "lib.read",
                "title": "Read Library File",
                "description": (
                    "Read the text of one brain-relative file (md/txt/bib/code/etc.), optionally a "
                    "line window. Use after lib.search returns a path to read the actual content. "
                    "Binary/oversized files are summarized, not dumped. personal-data is readable "
                    "(single-owner deployment; path gating is an off-by-default policy switch)."
                ),
                "inputSchema": _schema(
                    {
                        "path": {"type": "string", "description": "brain-relative file path (e.g. knowledge/foo/bar.md)"},
                        "offset": {"type": "integer", "description": "1-based start line (default 1)"},
                        "limit": {"type": "integer", "description": "max lines to return (0 = all, capped by byte budget)"},
                    },
                    required=["path"],
                ),
            },
            {
                "name": "lib.tree",
                "title": "List Library Directory",
                "description": (
                    "List one directory level under the brain (names, dir/file, suffix, size, "
                    "DocPack flag). Use to browse the library structure. personal-data is browsable "
                    "(single-owner deployment; path gating is an off-by-default policy switch)."
                ),
                "inputSchema": _schema(
                    {
                        "path": {"type": "string", "description": "brain-relative directory (omit/empty = brain root)"},
                    }
                ),
            },
            {
                "name": "lib.stat",
                "title": "Stat Library Path",
                "description": (
                    "Metadata for one brain-relative path: kind, suffix, size, mtime, and whether "
                    "it is in the search index. Use to check if a file exists / is indexed before reading."
                ),
                "inputSchema": _schema(
                    {
                        "path": {"type": "string", "description": "brain-relative file or directory path"},
                        "index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index"},
                    },
                    required=["path"],
                ),
            },
            {
                "name": "lib.recent",
                "title": "Recently Changed Documents",
                "description": (
                    "List the most recently modified indexed documents (newest first), optionally "
                    "filtered by suffix or path prefix. Use to answer 'what changed lately' / "
                    "'what did I just add'."
                ),
                "inputSchema": _schema(
                    {
                        "limit": {"type": "integer", "description": "max documents (default 20)"},
                        "suffix": {"type": "string", "description": "OPTIONAL — restrict to one file type, e.g. md, pdf"},
                        "path_prefix": {"type": "string", "description": "OPTIONAL — restrict to a brain-relative subtree"},
                        "index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index"},
                    }
                ),
            },
            {
                "name": "lib.freshness",
                "title": "Index Freshness",
                "description": (
                    "Compare the search index mtime against the newest file under knowledge/ to "
                    "report whether the index is up to date (fresh) and how far it lags. Use to decide "
                    "if a reindex is needed after a write."
                ),
                "inputSchema": _schema(
                    {
                        "index": {"type": "string", "description": "OPTIONAL — omit to use the hub's maintained index"},
                    }
                ),
            },
            {
                "name": "lib.list",
                "title": "Library List",
                "description": "Summarize DocPack directories or scan a brain root (brain-library docpacks|scan).",
                "inputSchema": _schema(
                    {
                        "op": _OP({"docpacks", "scan"}, "docpacks"),
                        "root": _ROOT,
                        "sample_limit": {"type": "integer"},
                    }
                ),
            },
            {
                "name": "lib.meta",
                "title": "Library Rules (_meta)",
                "description": (
                    "Read the authoritative brain _meta rule corpus (organize/spec rules). "
                    "No name -> catalogue of rule files; name=<file> (e.g. organize-rules, "
                    "数据处理规范) -> full rule text. Read the relevant rule BEFORE any write."
                ),
                "inputSchema": _schema(
                    {
                        "root": _ROOT,
                        "name": {
                            "type": "string",
                            "description": "rule file to read in full (e.g. organize-rules); omit to list all",
                        },
                        "query": {
                            "type": "string",
                            "description": "OPTIONAL — search all rule bodies for this keyword and return matching lines",
                        },
                        "max_bytes": {"type": "integer"},
                    }
                ),
            },
            {
                "name": "lib.policy",
                "title": "Effective Gate Policy",
                "description": (
                    "Explain the gate to yourself before acting: every method, its tier (read/write), "
                    "whether it is enabled, and whether THIS client may call it — plus the redaction and "
                    "personal-data exclusion settings. In-process; reads no brain content."
                ),
                "inputSchema": _schema({}),
            },
            {
                "name": "lib.audit",
                "title": "Audit Log Summary",
                "description": (
                    "Summarize the metadata-only audit log: counts by method and decision, total "
                    "redactions, and the most recent rows. Optional since/filter_method/decision/limit. "
                    "The log holds no argument bodies, so this is a safe read."
                ),
                "inputSchema": _schema(
                    {
                        "limit": {"type": "integer", "description": "max recent rows (default 50, cap 500)"},
                        "since": {"type": "string", "description": "OPTIONAL — ISO timestamp prefix; only rows at/after it"},
                        "filter_method": {"type": "string", "description": "OPTIONAL — only this method, e.g. lib.finalize"},
                        "decision": {"type": "string", "description": "OPTIONAL — allow|deny|error"},
                    }
                ),
            },
            {
                "name": "lib.preview",
                "title": "Preview Gate Decision",
                "description": (
                    "Dry-run the gate for a target method+arguments WITHOUT executing it: returns the "
                    "tier, allow/deny decision (and reason on deny), and a safe shape of the command "
                    "that would run (executable + flag names only, no values). gate_allows means the "
                    "GATE would permit it — not that the backend will succeed (write tools may still "
                    "need owner approval; see the note field). Never runs the backend or mutates anything."
                ),
                "inputSchema": _schema(
                    {
                        "method": {"type": "string", "description": "the target gateway method, e.g. lib.finalize"},
                        "arguments": {"type": "object", "description": "the arguments you would pass to that method"},
                    },
                    required=["method"],
                ),
            },
            {
                "name": "lib.course-intake",
                "title": "Course Folder Ingest",
                "description": (
                    "Ingest a course folder staged under brain/_inbox into knowledge/courses/<id> "
                    "with automatic classify into slides/lectures/exercises/exams/solutions/references "
                    "(+ sha256 dedup, PDF text-layer md, materials_index, pdf-manifest). Same owner gate "
                    "as lib.finalize: op=plan previews the classification + confirmation_questions + a "
                    "plan_sha; the OWNER approves out-of-band (rtime-course-intake approve <sha>, NOT a "
                    "gateway method); op=apply runs the ingest and rebuilds the index. Originals are "
                    "copied, never moved. Read the _meta course rules (lib.meta) first."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "src": {"type": "string", "description": "brain/_inbox-relative course folder (op=plan)"},
                        "course_id": {"type": "string", "description": "english-lowercase-hyphen course id, e.g. optics (op=plan)"},
                        "course_title": {"type": "string", "description": "course title, e.g. 光学 (op=plan)"},
                        "notify": {"type": "boolean", "description": "send the owner a one-tap Feishu approve card (op=plan)"},
                        "plan_sha": {"type": "string", "description": "the owner-approved plan_sha (op=apply)"},
                        "no_reindex": {"type": "boolean", "description": "skip the index rebuild (op=apply); the rebuild is incremental (reuses unchanged docs, ~seconds), so normally leave it on"},
                        "dry_run": {"type": "boolean"},
                    }
                ),
            },
            {
                "name": "lib.docpack",
                "title": "DocPack Tools",
                "description": "Run brain-docpack audit/select-samples/validate/doctor.",
                "inputSchema": _schema(
                    {
                        "op": _OP({"audit", "select-samples", "validate", "doctor"}, "doctor"),
                        "path": {"type": "string", "description": "brain/knowledge root or DocPack directory"},
                    }
                ),
            },
            {
                "name": "lib.citation",
                "title": "Citation Tools",
                "description": "Run brain-citation scan/panel/doctor.",
                "inputSchema": _schema({"op": _OP({"scan", "panel", "doctor"}, "panel"), "root": _ROOT}),
            },
            {
                "name": "lib.hub",
                "title": "Hub Connector",
                "description": "Run rtime-hub-connector panel/scan/contacts/doctor (contacts forces output redaction).",
                "inputSchema": _schema(
                    {"op": _OP({"panel", "scan", "contacts", "doctor"}, "panel"), "hub_root": _ROOT}
                ),
            },
            {
                "name": "lib.context",
                "title": "Context Planner",
                "description": "Run rtime-context doctor/plan/pack/explain.",
                "inputSchema": _schema(
                    {"op": _OP({"doctor", "plan", "pack", "explain"}, "doctor"), "request": _REQUEST}
                ),
            },
            {
                "name": "lib.profile",
                "title": "Profile Tools",
                "description": "Run rtime-profile panel/scan/plan/doctor.",
                "inputSchema": _schema(
                    {"op": _OP({"panel", "scan", "plan", "doctor"}, "panel"), "request": _REQUEST}
                ),
            },
            {
                "name": "lib.review",
                "title": "Review Console",
                "description": "Run rtime-review panel/audits/run-logs/tooling/doctor.",
                "inputSchema": _schema(
                    {
                        "op": _OP({"panel", "audits", "run-logs", "tooling", "doctor"}, "panel"),
                        "path": {"type": "string", "description": "JSONL run log path (run-logs op)"},
                    }
                ),
            },
            {
                "name": "lib.automation",
                "title": "Automation Diagnostics",
                "description": "Run rtime-automation panel/reminders/health/doctor (read side only; never plans writes).",
                "inputSchema": _schema({"op": _OP({"panel", "reminders", "health", "doctor"}, "panel")}),
            },
            {
                "name": "lib.runtime",
                "title": "Runtime Diagnostics",
                "description": "Run rtime-runtime doctor or run-log summary.",
                "inputSchema": _schema(
                    {
                        "op": _OP({"doctor", "run-log-summary"}, "doctor"),
                        "path": {"type": "string", "description": "run-log JSONL path"},
                    }
                ),
            },
            # --- writes: the three narrow settings tools ---
            {
                "name": "lib.settings.context_source_list",
                "title": "Context Sources: List",
                "description": "List dynamic context-source registry metadata.",
                "inputSchema": _schema(
                    {"status": {"type": "string"}, "limit": {"type": "integer"}, "manifest": {"type": "string"}}
                ),
            },
            {
                "name": "lib.settings.context_source_check",
                "title": "Context Sources: Check",
                "description": "Validate context-source metadata and paths.",
                "inputSchema": _schema({"manifest": {"type": "string"}}),
            },
            {
                "name": "lib.settings.context_source_add",
                "title": "Context Sources: Add",
                "description": "Add an active context source (source_path validated; personal-data rejected).",
                "inputSchema": _schema(
                    {
                        "id": {"type": "string"},
                        "kind": {"type": "string"},
                        "title": {"type": "string"},
                        "source_path": {"type": "string", "description": "brain-relative path; never personal-data/absolute"},
                        "tags": {"type": "string"},
                        "priority": {"type": "integer"},
                        "status": {"type": "string"},
                        "active_from": {"type": "string"},
                        "expires": {"type": "string"},
                        "max_chars": {"type": "integer"},
                        "dry_run": {"type": "boolean"},
                    },
                    required=["id", "kind", "title", "source_path"],
                ),
            },
            {
                "name": "lib.settings.context_source_deactivate",
                "title": "Context Sources: Deactivate",
                "description": "Mark a context source inactive or cancelled.",
                "inputSchema": _schema(
                    {
                        "id": {"type": "string"},
                        "status": {"type": "string", "enum": ["inactive", "cancelled"]},
                        "reason": {"type": "string"},
                        "dry_run": {"type": "boolean"},
                    },
                    required=["id"],
                ),
            },
            {
                "name": "lib.settings.memory_candidate_add",
                "title": "Memory Candidate: Add",
                "description": "Write one memory review-queue candidate. Claim text travels via stdin and is never logged; entry is forced to library-gateway.",
                "inputSchema": _schema(
                    {
                        "claim": {"type": "string", "description": "candidate claim text (sent via stdin, never echoed)"},
                        "scope": {"type": "string"},
                        "kind": {"type": "string"},
                        "source": {"type": "string"},
                        "sensitivity": {"type": "string", "enum": ["normal", "sensitive"]},
                        "expires_days": {"type": "integer"},
                        "dry_run": {"type": "boolean"},
                    },
                    required=["claim"],
                ),
            },
            {
                "name": "lib.settings.reminder_register",
                "title": "Reminder: Register",
                "description": "Append a pending Feishu reminder (metadata-only echo; message/target not returned).",
                "inputSchema": _schema(
                    {
                        "due": {"type": "string"},
                        "message": {"type": "string"},
                        "mode": {"type": "string", "enum": ["notify", "wake"]},
                        "repeat": {"type": "string", "enum": ["none", "hourly", "daily", "weekly"]},
                        "prompt": {"type": "string"},
                        "cwd": {"type": "string"},
                        "model": {"type": "string"},
                        "permission_mode": {"type": "string"},
                        "id": {"type": "string"},
                        "dry_run": {"type": "boolean"},
                    },
                    required=["due", "message"],
                ),
            },
            {
                "name": "lib.settings.reminder_list",
                "title": "Reminder: List",
                "description": "List reminder metadata without private bodies.",
                "inputSchema": _schema({"status": {"type": "string"}, "limit": {"type": "integer"}}),
            },
            {
                "name": "lib.settings.reminder_cancel",
                "title": "Reminder: Cancel",
                "description": "Cancel a reminder by id.",
                "inputSchema": _schema({"id": {"type": "string"}}, required=["id"]),
            },
            # --- brain-content DIRECT write (H M1): frontmatter-only annotate ---
            {
                "name": "lib.annotate",
                "title": "Annotate Frontmatter (owner/super-admin)",
                "description": (
                    "Edit ONLY the frontmatter contract fields (status/review_after/"
                    "superseded_by/source/tags) of one brain markdown file. Two-phase: "
                    "op=plan returns a per-field diff + confirm_token (writes nothing); "
                    "op=apply requires the token (stale after any file change), snapshots "
                    "the previous content into _revisions/ (chain.jsonl, revertable), "
                    "bumps version automatically, never touches the body, and syncs the "
                    "index metadata columns. Denied on scoped instances — grantees "
                    "contribute via lib.contribute instead."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "path": {"type": "string", "description": "brain-relative markdown path"},
                        "changes": {
                            "type": "object",
                            "description": "field -> new value; empty string deletes the field",
                            "additionalProperties": {"type": "string"},
                        },
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["path", "changes"],
                ),
            },
            # --- brain-content DIRECT write (H M2): edit body / revisions / revert ---
            {
                "name": "lib.edit",
                "title": "Edit Body (owner/super-admin)",
                "description": (
                    "Replace the BODY of one brain markdown file (frontmatter kept "
                    "byte-for-byte except version, which auto-bumps). Two-phase: op=plan "
                    "returns a unified diff + confirm_token + contract warnings (writes "
                    "nothing); op=apply requires the token (stale after any file change), "
                    "snapshots the previous content into _revisions/ (revertable), and syncs "
                    "index metadata. NOTE: body change makes the search embedding stale until "
                    "reindex (index_embedding_stale=true). Denied on scoped instances."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "path": {"type": "string", "description": "brain-relative markdown path"},
                        "new_body": {"type": "string", "description": "the full new body (replaces everything after frontmatter)"},
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["path", "new_body"],
                ),
            },
            {
                "name": "lib.revisions",
                "title": "List Revisions",
                "description": (
                    "Read the revision chain for one brain file: every annotate/edit/revert "
                    "with version, verb, timestamp, actor, sha-before/after and snapshot name. "
                    "Read-only. Use a snapshot name with lib.revert to roll back."
                ),
                "inputSchema": _schema(
                    {"path": {"type": "string", "description": "brain-relative markdown path"}},
                    required=["path"],
                ),
            },
            {
                "name": "lib.revert",
                "title": "Revert to Revision (owner/super-admin)",
                "description": (
                    "Roll a brain file back to a stored revision snapshot (frontmatter + body "
                    "restored, version moves FORWARD — the revert itself is recorded in the "
                    "chain). Two-phase: op=plan returns the current→reverted diff + "
                    "confirm_token; op=apply requires the token. snapshot is a name from "
                    "lib.revisions. Denied on scoped instances."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "path": {"type": "string", "description": "brain-relative markdown path"},
                        "snapshot": {"type": "string", "description": "snapshot name from lib.revisions (e.g. 000001.md)"},
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["path", "snapshot"],
                ),
            },
            # --- brain-content DIRECT write (H M3): move/retire/restore ---
            {
                "name": "lib.move",
                "title": "Move / Rename File (owner/super-admin)",
                "description": (
                    "Move or rename one brain file. Two-phase: op=plan runs a "
                    "reference-integrity scan (which knowledge/ files link to it via "
                    "[[slug]] or superseded_by) and returns affected_refs + confirm_token "
                    "(writes nothing); op=apply requires the token (stale after any source "
                    "change), snapshots the original into _revisions/ (verb=move), moves the "
                    "file to to_path, and leaves a TOMBSTONE at from_path (status: moved, "
                    "moved_to) so lib.read on the old path returns the redirect. to_path must "
                    "not already exist (no overwrite). Removes the old path from the index; "
                    "the new path is picked up on the next incremental reindex "
                    "(index_rebuild_needed=true). Denied on scoped instances."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "from_path": {"type": "string", "description": "brain-relative source path"},
                        "to_path": {"type": "string", "description": "brain-relative destination path (must not exist)"},
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["from_path", "to_path"],
                ),
            },
            {
                "name": "lib.retire",
                "title": "Retire (Soft-Delete) File (owner/super-admin)",
                "description": (
                    "Soft-delete one brain file: move it into _archive/<original path> "
                    "(fully preserved, restorable) and leave a TOMBSTONE at the original path "
                    "(status: retired). Two-phase: op=plan returns archived_to + affected_refs "
                    "+ confirm_token (writes nothing); op=apply requires the token, snapshots "
                    "the content into _revisions/ (verb=retire), archives + tombstones, and "
                    "removes the path from the search index so it no longer matches. Recover "
                    "with lib.restore. Denied on scoped instances."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "path": {"type": "string", "description": "brain-relative path to retire"},
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["path"],
                ),
            },
            {
                "name": "lib.restore",
                "title": "Restore Retired File (owner/super-admin)",
                "description": (
                    "Undo a retire: move the archived copy from _archive/ back to its original "
                    "path (which must be a retire tombstone or absent). Two-phase: op=plan "
                    "returns restored_from + confirm_token; op=apply requires the token, "
                    "snapshots (verb=restore), and puts the file back. The new path is picked "
                    "up on the next incremental reindex. Denied on scoped instances."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "path": {"type": "string", "description": "brain-relative original path to restore"},
                        "confirm_token": {"type": "string", "description": "from op=plan (apply only)"},
                    },
                    required=["path"],
                ),
            },
            # --- brain-content write: stage a note into the intake inbox ---
            {
                "name": "lib.contribute",
                "title": "Contribute Note to Brain Inbox",
                "description": (
                    "Stage an agent-authored note into brain/_inbox/agent (the sanctioned intake "
                    "entry) for the owner to curate. It is NEVER filed into knowledge/ and never "
                    "finalized here. op=plan previews (writes nothing) and returns confirmation "
                    "questions; op=stage writes the note + an intake ticket. Sensitive material is "
                    "refused. The note body travels via stdin and is never logged."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "stage"}, "plan"),
                        "title": {"type": "string", "description": "short note title"},
                        "text": {"type": "string", "description": "note body (sent via stdin, never echoed/logged)"},
                        "note": {"type": "string", "description": "provenance/source note (e.g. which session/task)"},
                        "tags": {"type": "string", "description": "comma-separated tags"},
                        "dry_run": {"type": "boolean"},
                    },
                    required=["title", "text"],
                ),
            },
            {
                "name": "lib.finalize",
                "title": "Finalize Inbox Item into Knowledge",
                "description": (
                    "Promote a staged brain/_inbox item — a single file, a .zip bundle, or a "
                    "directory — into knowledge/. The other half of lib.contribute, NEVER automatic: "
                    "op=plan (writes nothing) classifies, proposes the dest + a per-file parse plan + "
                    "inventory, and returns a plan_sha + confirmation_questions; the OWNER approves "
                    "out-of-band (rtime-finalize approve <sha>, NOT a gateway method); op=apply "
                    "requires that approved plan_sha, then moves/extracts into knowledge/, writes "
                    "searchable md (PDF text→md, scanned PDF→OCR when ocr=true, Office→extract), "
                    "writes a catalog for bundles, rebuilds the index, and drafts an Obsidian entry "
                    "only for course items. Read the _meta rules (lib.meta) first to pick the dest."
                ),
                "inputSchema": _schema(
                    {
                        "op": _OP({"plan", "apply"}, "plan"),
                        "inbox": {"type": "string", "description": "brain/_inbox-relative path of the staged file/zip/dir (op=plan)"},
                        "dest": {"type": "string", "description": "knowledge-relative destination dir, e.g. knowledge/templates/latex (op=plan)"},
                        "name": {"type": "string", "description": "bundle subdir name under dest for zip/dir (op=plan; defaults to a slug)"},
                        "summary": {"type": "string", "description": "one-line catalog description, recommended for bundles (op=plan)"},
                        "notify": {"type": "boolean", "description": "ping the owner via Feishu that this plan awaits approval (op=plan)"},
                        "plan_sha": {"type": "string", "description": "the owner-approved plan_sha from op=plan (op=apply)"},
                        "ocr": {"type": "boolean", "description": "OCR scanned PDFs into flat searchable md — slow (op=apply)"},
                        "docpack": {"type": "boolean", "description": "scanned PDFs -> structured DocPack (page images + OCR'd content.md) (op=apply)"},
                        "no_reindex": {"type": "boolean", "description": "skip the index rebuild (op=apply); the rebuild is incremental (reuses unchanged docs, ~seconds), so normally leave it on"},
                        "dry_run": {"type": "boolean"},
                    }
                ),
            },
            # --- long-task isolation: submit a job / query its status ---
            {
                "name": "lib.jobs.submit",
                "title": "Submit a Background Job",
                "description": (
                    "Enqueue a long task to run in a SEPARATE worker instead of blocking the chat "
                    "entry, then poll lib.jobs.get for the result. Use for heavy work — "
                    "type='index-rebuild' (rebuild the search index; params {brain_root?, incremental?}) "
                    "or type='course-intake-apply' (apply an OWNER-APPROVED course ingest off the chat "
                    "entry; params {plan_sha, no_reindex?}). type='echo' is a no-op pipeline self-test. "
                    "Submitting does NOT grant approval: a course-intake-apply job still needs an "
                    "owner-approved plan_sha that the worker re-checks. A worker must be running for the "
                    "job to execute; the existing synchronous lib.course-intake / lib.finalize paths "
                    "remain available as before."
                ),
                "inputSchema": _schema(
                    {
                        "type": {
                            "type": "string",
                            # KEEP IN SYNC: packages/rtime-jobs/src/rtime_jobs/handlers.py (HANDLERS).
                            # This enum is an advisory hint for clients; the authoritative
                            # validation is rtime-jobs-submit checking known_types(). Add a
                            # handler there -> add it here so clients can discover it.
                            "enum": ["echo", "index-rebuild", "course-intake-apply"],
                            "description": "the job type to enqueue",
                        },
                        "params": {
                            "type": "object",
                            "description": "type-specific params (sent via stdin, never logged); e.g. {\"plan_sha\": \"...\"}",
                        },
                    },
                    required=["type"],
                ),
            },
            {
                "name": "lib.jobs.get",
                "title": "Get Job Status / Result",
                "description": (
                    "Read one job by id: its status (pending/running/succeeded/failed), result on "
                    "success, error on failure, plus timing/attempts. Use to poll a job submitted with "
                    "lib.jobs.submit."
                ),
                "inputSchema": _schema(
                    {"id": {"type": "string", "description": "the job id returned by lib.jobs.submit"}},
                    required=["id"],
                ),
            },
            {
                "name": "lib.jobs.list",
                "title": "List Background Jobs",
                "description": (
                    "List recent jobs (most recent first) with per-status counts, optionally filtered "
                    "by status. Use to see what is queued / running / done."
                ),
                "inputSchema": _schema(
                    {
                        "status": {"type": "string", "enum": ["pending", "running", "succeeded", "failed"], "description": "OPTIONAL — only this status"},
                        "limit": {"type": "integer", "description": "max rows (default 50)"},
                    }
                ),
            },
        ]

    def handle_message(self, message: JsonObject) -> JsonObject | None:
        if not isinstance(message, dict):
            return _error_response(None, -32600, "Invalid Request")
        request_id = message.get("id")
        method = message.get("method")
        has_id = "id" in message
        if not isinstance(method, str):
            if has_id:
                return _error_response(request_id, -32600, "Invalid Request")
            return None
        if method == "notifications/initialized":
            return None
        if method == "initialize":
            params = message.get("params", {})
            client_version = params.get("protocolVersion") if isinstance(params, dict) else None
            client_info = params.get("clientInfo") if isinstance(params, dict) else None
            if isinstance(client_info, dict) and isinstance(client_info.get("name"), str):
                self._client_id = client_info["name"]
            protocol_version = _negotiate_protocol_version(client_version)
            return _response(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "Rtime Library Gateway",
                        "version": SERVER_VERSION,
                        "description": "Unified library access gateway: one permission gate and audit over rtime read tools and the narrow-write tools (settings + contribute).",
                    },
                    "instructions": (
                        "Use lib.* to read the rtime brain library and assistant data behind one "
                        "permission gate with metadata-only audit. "
                        "LIBRARY LAYOUT (read before deciding where things go): 'brain' (THIS library, normally "
                        "a shared server-side root) is reached by client devices through a mount/sync view and is "
                        "NOT the Obsidian vault. 'brain-notes' is a SEPARATE, sibling folder for hand-written notes. "
                        "DEFAULT ROUTING: bulk/source material, institutional data, crawled resources and large "
                        "files go into brain/knowledge/ (this library) — NOT brain-notes; only hand-written "
                        "notes (course/paper/daily notes, MOCs) belong in brain-notes. Consequence: content "
                        "placed in brain is searchable and servable by the assistant but does NOT sync to "
                        "devices and does NOT appear in Obsidian unless you separately add a small index/MOC "
                        "note to brain-notes. Authoritative detail: _meta/使用指南.md. "
                        "This is a single-owner library: "
                        "lib.search and the index cover the FULL library including personal-data; "
                        "as of 2026-06-19 this single-owner deployment is fully open — output is NOT "
                        "redacted (redact_sensitive=false) and personal-data paths are readable "
                        "(excluded_top_dirs=[]). Redaction and personal-data gating are policy "
                        "switches re-enablable without code changes. "
                        "Writes are narrow and audited: lib.settings.* (context sources, memory "
                        "candidates, reminders), lib.contribute (stage a note into brain/_inbox for "
                        "owner curation — never filed into knowledge/), and lib.finalize (promote an "
                        "_inbox item into knowledge/, but only with an owner-issued token; plan→owner "
                        "approve→apply). Read the rules first with lib.meta before any write."
                    ),
                },
            )
        if method == "ping":
            return _response(request_id, {})
        if method == "tools/list":
            # Advertise API-valid wire names (dots -> underscores); the dotted
            # definitions in self.tools() stay the canonical source of truth.
            tools = self.tools()
            for tool in tools:
                tool["name"] = _wire_name(tool["name"])
            return _response(request_id, {"tools": tools})
        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                return _error_response(request_id, -32602, "Invalid params")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error_response(request_id, -32602, "Invalid params")
            # The wire name (lib_search) maps back to the canonical id (lib.search);
            # a dotted name passes through unchanged for backward compatibility.
            method_name = _canonical_method(name)
            try:
                data = self.invoke(method_name, arguments, client_id=getattr(self, "_client_id", "default"))
                return _response(
                    request_id,
                    _tool_result(
                        data,
                        is_error=data.get("ok") is False,
                        render_text=getattr(self, "_render_text", None),
                    ),
                )
            except ToolError as exc:
                return _response(request_id, _tool_result({"ok": False, "error": str(exc)}, is_error=True))
            except Exception as exc:  # pragma: no cover - defensive server guard
                return _response(request_id, _tool_result({"ok": False, "error": str(exc)}, is_error=True))
        if method == "shutdown":
            return _response(request_id, {})
        if has_id:
            return _error_response(request_id, -32601, f"Method not found: {method}")
        return None

    def invoke(self, method: str, arguments: JsonObject, *, client_id: str = "default") -> JsonObject:
        """Run one gateway method: gate -> dispatch -> redact -> audit.

        Used by both the MCP ``tools/call`` path and the CLI ``call`` command.
        """
        # Resolving the policy can fail CLOSED: an explicitly named policy that is
        # missing/unreadable/malformed raises GateError (never degrades to a wider
        # default — see gate.load_policy). Surface it as a clean ToolError so a
        # broken scoped policy denies every call rather than crashing the loop or
        # (worse) serving the full library.
        try:
            policy = gate_mod.load_policy()
        except gate_mod.GateError as exc:
            raise ToolError(str(exc)) from exc
        brain_root = dispatch_mod.brain_root()
        started = time.monotonic()
        decision = "allow"
        exit_code: int | None = None
        redacted = 0
        tier = gate_mod.method_tier(policy, method) or "read"
        self._render_text = None
        try:
            try:
                gate_mod.enforce(method, arguments, client_id, policy=policy, brain_root=brain_root)
            except gate_mod.GateError as exc:
                decision = "deny"
                raise ToolError(str(exc)) from exc

            if method in _INPROCESS:
                data, exit_code = self._inprocess(method, arguments, client_id)
                return data

            if method == "lib.search":
                # Fast path: query the BM25 index IN-PROCESS so jieba is loaded once
                # and stays warm for the session, instead of reloading (~1s) per call
                # in a fresh subprocess. Redaction mirrors the dispatch path below.
                try:
                    parsed, exit_code = self._search_inprocess(arguments)
                except Exception as exc:  # never crash the session on a search error
                    decision = "error"
                    exit_code, parsed = 1, {"ok": False, "error": f"search failed: {exc}"}
                redact = bool(policy.get("redact_sensitive", True))
                pii = bool(policy.get("redact_student_pii", False))
                raw_text = json.dumps(parsed, ensure_ascii=False, indent=2, sort_keys=True)
                redacted_text, n_text = gate_mod.redact_output(raw_text, redact=redact, pii=pii)
                structured, n_struct = gate_mod.redact_json(parsed, redact=redact, pii=pii)
                redacted = n_text + n_struct
                data = dict(structured)
                data.setdefault("method", method)
                data.setdefault("tier", tier)
                if redacted:
                    data["redacted_line_count"] = redacted
                self._render_text = redacted_text if redacted else None
                # Scope belt (P5 阶段0) then excluded-dir hide; either mutation
                # re-renders the text payload so it matches the filtered structure.
                filtered = self._scope_filter_results(method, data, policy)
                filtered += self._maybe_hide_excluded(method, data, policy)
                if filtered:
                    self._render_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
                return data

            try:
                target = dispatch_mod.build_target(method, arguments)
            except dispatch_mod.GateError as exc:
                decision = "deny"
                raise ToolError(str(exc)) from exc

            redact = bool(policy.get("redact_sensitive", True))
            pii = bool(policy.get("redact_student_pii", False))
            try:
                parsed, exit_code, raw_stdout = dispatch_mod.run_cli(target)
            except dispatch_mod.GateError as exc:
                decision = "error"
                raise ToolError(str(exc)) from exc
            except subprocess_timeout() as exc:  # pragma: no cover - timing dependent
                decision = "error"
                raise ToolError(f"tool timed out: {method}") from exc

            redacted_text, redacted = gate_mod.redact_output(
                raw_stdout, force=target.redact_force, redact=redact, pii=pii
            )
            structured, structured_redacted = gate_mod.redact_json(
                parsed, force=target.redact_force, redact=redact, pii=pii
            )
            redacted += structured_redacted
            data = dict(structured)
            data.setdefault("method", method)
            data.setdefault("tier", tier)
            if redacted:
                data["redacted_line_count"] = redacted
            self._render_text = redacted_text if redacted else None
            # Scope belt (P5 阶段0) then excluded-dir hide; either mutation
            # re-renders the text payload so it matches the filtered structure.
            filtered = self._scope_filter_results(method, data, policy)
            filtered += self._maybe_hide_excluded(method, data, policy)
            # lib.get (H2): trim full-library aggregate metadata under a scope.
            trimmed = self._scope_trim_get(method, data, policy)
            if filtered or trimmed:
                self._render_text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
            return data
        finally:
            gate_mod.record_audit(
                method=method,
                client_id=client_id,
                tier=tier,
                decision=decision,
                exit_code=exit_code,
                duration_ms=int((time.monotonic() - started) * 1000),
                arguments=arguments,
                redacted_line_count=redacted,
                policy=policy,
            )

    def _inprocess(
        self, method: str, arguments: JsonObject, client_id: str
    ) -> tuple[JsonObject, int]:
        if method == "lib.doctor":
            data = cli_doctor()
            data.setdefault("method", method)
            return data, 0
        if method == "lib.status":
            return self._status(client_id, quick=bool(arguments.get("quick"))), 0
        if method == "lib.policy":
            return self._policy_report(client_id), 0
        if method == "lib.audit":
            return self._audit_report(arguments), 0
        if method == "lib.preview":
            return self._preview(arguments, client_id), 0
        if method == "lib.annotate":
            return self._annotate(arguments, client_id), 0
        if method == "lib.edit":
            return self._edit(arguments, client_id), 0
        if method == "lib.revert":
            return self._revert(arguments, client_id), 0
        if method == "lib.revisions":
            return self._revisions(arguments), 0
        if method == "lib.move":
            return self._move(arguments, client_id), 0
        if method == "lib.retire":
            return self._retire(arguments, client_id), 0
        if method == "lib.restore":
            return self._restore(arguments, client_id), 0
        raise ToolError(f"no in-process handler for {method}")  # pragma: no cover

    def _sync_index_meta(self, root, rel_path: str) -> JsonObject:
        """apply 成功后同步索引元数据列(单行 UPDATE,零重嵌入)。失败不回滚文件,
        夜巡 drift 兜底。返回 {ok, reason?}。"""
        from pathlib import Path as _Path

        from brain_library.indexer import update_meta_columns

        try:
            index = _Path(dispatch_mod.default_index())
            if not index.exists():
                return {"ok": False, "reason": "no_index"}
            return update_meta_columns(index, root, rel_path)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"sync_failed: {exc}"}

    def _annotate(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M1 lib.annotate(frontmatter-only 直写,plan/apply 两段式,in-process)。

        写四件套:brain_library.annotate 做 合同校验+修订快照+原子落盘,这里补第四件
        ——apply 成功后同步索引元数据列(update_meta_columns,单行 UPDATE,零重嵌入)。
        scoped 实例到不了这里(gate.SCOPE_DENIED_WRITE_METHODS 先拒)。"""
        from brain_library import annotate as annotate_mod
        from brain_library.indexer import update_meta_columns

        op = str(arguments.get("op") or "plan")
        rel_path = str(arguments.get("path") or "")
        changes = arguments.get("changes")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = annotate_mod.plan_annotate(root, rel_path, changes)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = annotate_mod.apply_annotate(
                root, rel_path, changes, token, actor=client_id
            )
            if data.get("ok"):
                from pathlib import Path as _Path

                try:
                    index = _Path(dispatch_mod.default_index())
                    sync = (
                        update_meta_columns(index, root, rel_path)
                        if index.exists()
                        else {"ok": False, "reason": "no_index"}
                    )
                except Exception as exc:  # noqa: BLE001 — 索引同步失败不回滚文件,夜巡drift兜底
                    sync = {"ok": False, "reason": f"sync_failed: {exc}"}
                data["index_synced"] = bool(sync.get("ok"))
                if not sync.get("ok"):
                    data["index_sync_reason"] = str(sync.get("reason"))
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.annotate")
        return data

    def _edit(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M2 lib.edit(改正文,plan/apply 两段式,in-process)。

        apply 成功同步索引元数据列;但正文变动使搜索**嵌入过时**(update_meta_columns
        零重嵌入,增量索引写入路径是后续档),故对 body 变动动词标 index_embedding_stale
        提示需重索引。scoped 实例到不了这里(SCOPE_DENIED_WRITE_METHODS 先拒)。"""
        from brain_library import edit as edit_mod

        op = str(arguments.get("op") or "plan")
        rel_path = str(arguments.get("path") or "")
        new_body = arguments.get("new_body")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = edit_mod.plan_edit(root, rel_path, new_body)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = edit_mod.apply_edit(root, rel_path, new_body, token, actor=client_id)
            if data.get("ok"):
                sync = self._sync_index_meta(root, rel_path)
                data["index_synced"] = bool(sync.get("ok"))
                if not sync.get("ok"):
                    data["index_sync_reason"] = str(sync.get("reason"))
                data["index_embedding_stale"] = True  # 正文变→嵌入过时,需重索引
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.edit")
        return data

    def _revert(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M2 lib.revert(回滚到某修订快照,plan/apply 两段式,in-process)。"""
        from brain_library import edit as edit_mod

        op = str(arguments.get("op") or "plan")
        rel_path = str(arguments.get("path") or "")
        snapshot = str(arguments.get("snapshot") or "")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = edit_mod.plan_revert(root, rel_path, snapshot)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = edit_mod.apply_revert(root, rel_path, snapshot, token, actor=client_id)
            if data.get("ok"):
                sync = self._sync_index_meta(root, rel_path)
                data["index_synced"] = bool(sync.get("ok"))
                if not sync.get("ok"):
                    data["index_sync_reason"] = str(sync.get("reason"))
                data["index_embedding_stale"] = True  # 回滚可换正文→嵌入过时
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.revert")
        return data

    def _revisions(self, arguments: JsonObject) -> JsonObject:
        """H M2 lib.revisions(列某路径修订链,纯读)。"""
        from brain_library import edit as edit_mod

        rel_path = str(arguments.get("path") or "")
        data = edit_mod.list_revisions(dispatch_mod.brain_root(), rel_path)
        data.setdefault("method", "lib.revisions")
        return data

    def _remove_index_path(self, rel_path: str) -> JsonObject:
        """H M3:apply 成功后从索引移除该路径的行(retire/move-away 的索引一致性)。
        失败不回滚文件(与 annotate 同纹理,夜巡 drift 兜底)。返回 {ok, reason?/removed}。"""
        from pathlib import Path as _Path

        from brain_library.indexer import remove_from_index

        try:
            index = _Path(dispatch_mod.default_index())
            if not index.exists():
                return {"ok": False, "reason": "no_index"}
            return remove_from_index(index, rel_path)
        except Exception as exc:  # noqa: BLE001
            return {"ok": False, "reason": f"remove_failed: {exc}"}

    def _move(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M3 lib.move(移动/重命名,plan/apply 两段式,in-process)。

        apply 成功后:旧路径变墓碑(非知识)→从索引移除旧 path 行;新路径由后续增量
        重建收录(标 index_rebuild_needed 提示,与 edit 的 embedding_stale 同一姿态)。
        scoped 实例到不了这里(SCOPE_DENIED_WRITE_METHODS 先拒)。"""
        from brain_library import maintain as maintain_mod

        op = str(arguments.get("op") or "plan")
        from_path = str(arguments.get("from_path") or "")
        to_path = str(arguments.get("to_path") or "")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = maintain_mod.plan_move(root, from_path, to_path)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = maintain_mod.apply_move(root, from_path, to_path, token, actor=client_id)
            if data.get("ok"):
                removed = self._remove_index_path(from_path)
                data["index_synced"] = bool(removed.get("ok"))
                if not removed.get("ok"):
                    data["index_sync_reason"] = str(removed.get("reason"))
                data["index_rebuild_needed"] = True  # 新路径待增量重建收录
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.move")
        return data

    def _retire(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M3 lib.retire(软删到 _archive/,plan/apply 两段式,in-process)。

        apply 成功后从索引移除该路径行(检索不再命中);归档文件完整保留=可 lib.restore。"""
        from brain_library import maintain as maintain_mod

        op = str(arguments.get("op") or "plan")
        rel_path = str(arguments.get("path") or "")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = maintain_mod.plan_retire(root, rel_path)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = maintain_mod.apply_retire(root, rel_path, token, actor=client_id)
            if data.get("ok"):
                removed = self._remove_index_path(rel_path)
                data["index_synced"] = bool(removed.get("ok"))
                if not removed.get("ok"):
                    data["index_sync_reason"] = str(removed.get("reason"))
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.retire")
        return data

    def _restore(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """H M3 lib.restore(把退役文件从 _archive/ 恢复回原路径,plan/apply 两段式)。

        恢复后原路径重新是知识内容,由后续增量重建收录(标 index_rebuild_needed)。"""
        from brain_library import maintain as maintain_mod

        op = str(arguments.get("op") or "plan")
        rel_path = str(arguments.get("path") or "")
        root = dispatch_mod.brain_root()
        if op == "plan":
            data = maintain_mod.plan_restore(root, rel_path)
        elif op == "apply":
            token = str(arguments.get("confirm_token") or "")
            data = maintain_mod.apply_restore(root, rel_path, token, actor=client_id)
            if data.get("ok"):
                data["index_rebuild_needed"] = True  # 恢复的路径待增量重建收录
        else:
            data = {"ok": False, "errors": [f"unknown op: {op} (plan|apply)"]}
        data.setdefault("method", "lib.restore")
        return data

    def _policy_report(self, client_id: str) -> JsonObject:
        """Explain the effective gate to the caller: which methods exist, their
        tier, whether they are enabled, and whether *this* client may call them.
        Read-only and self-describing — an agent can ask "what am I allowed to do"
        before attempting a write that the gate would reject."""
        policy = gate_mod.load_policy()
        methods: JsonObject = {}
        for name in sorted(gate_mod.METHOD_TIERS):
            entry = gate_mod._method_entry(policy, name)
            methods[name] = {
                "tier": gate_mod.method_tier(policy, name) or "read",
                "enabled": entry.get("enabled") is not False,
                "allowed_for_you": gate_mod._client_allows(policy, name, client_id),
            }
        return {
            "ok": True,
            "method": "lib.policy",
            "client_id": client_id,
            "default_read": str(policy.get("default_read", "allow")),
            "default_write": str(policy.get("default_write", "allow")),
            "redact_sensitive": bool(policy.get("redact_sensitive", True)),
            "redact_student_pii": bool(policy.get("redact_student_pii", False)),
            "excluded_top_dirs": sorted(gate_mod._excluded_top_dirs(policy)),
            "hide_excluded_in_results": bool(policy.get("hide_excluded_in_results", False)),
            # Non-empty = this gateway instance serves only these brain subtrees
            # (subset read scope); empty = the full library.
            "allowed_path_prefixes": gate_mod._allowed_path_prefixes(policy),
            "method_count": len(methods),
            "methods": methods,
        }

    def _audit_report(self, arguments: JsonObject) -> JsonObject:
        """Summarise the metadata-only audit log: counts by method/decision plus
        the most recent N rows. Honours optional ``since`` (ISO prefix), ``method``,
        and ``decision`` filters. The log never holds argument bodies, so this is
        safe to expose as a read tool."""
        policy = gate_mod.load_policy()
        path = gate_mod._audit_log_path(policy)
        if path is None or not path.is_file():
            return {
                "ok": True,
                "method": "lib.audit",
                "total_matched": 0,
                "note": "no audit log configured or none written yet",
            }
        since = arguments.get("since") if isinstance(arguments.get("since"), str) else None
        method_filter = (
            arguments.get("filter_method")
            if isinstance(arguments.get("filter_method"), str)
            else None
        )
        # The audit log stores canonical dotted ids; accept a wire-form filter too.
        if method_filter is not None:
            method_filter = _canonical_method(method_filter)
        decision_filter = (
            arguments.get("decision") if isinstance(arguments.get("decision"), str) else None
        )
        limit = arguments.get("limit", 50)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            limit = 50
        limit = min(limit, 500)
        by_method: dict[str, int] = {}
        by_decision: dict[str, int] = {}
        redacted_total = 0
        rows: list[JsonObject] = []
        total = 0
        try:
            lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError as exc:
            return {"ok": False, "method": "lib.audit", "error": f"cannot read audit log: {exc}"}
        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if since and str(rec.get("ts", "")) < since:
                continue
            if method_filter and rec.get("method") != method_filter:
                continue
            if decision_filter and rec.get("decision") != decision_filter:
                continue
            total += 1
            m = str(rec.get("method"))
            d = str(rec.get("decision"))
            by_method[m] = by_method.get(m, 0) + 1
            by_decision[d] = by_decision.get(d, 0) + 1
            rc = rec.get("redacted_line_count")
            redacted_total += rc if isinstance(rc, int) else 0
            rows.append(
                {
                    "ts": rec.get("ts"),
                    "method": rec.get("method"),
                    "decision": rec.get("decision"),
                    "exit_code": rec.get("exit_code"),
                    "duration_ms": rec.get("duration_ms"),
                    "client_id": rec.get("client_id"),
                }
            )
        return {
            "ok": True,
            "method": "lib.audit",
            "total_matched": total,
            "by_method": by_method,
            "by_decision": by_decision,
            "redacted_line_total": redacted_total,
            "recent": rows[-limit:],
        }

    def _preview(self, arguments: JsonObject, client_id: str) -> JsonObject:
        """Dry-run the gate for a *target* method+arguments without executing it.
        Returns the tier, the allow/deny decision (and reason on deny), and — when
        allowed and dispatched to a subprocess — a SAFE shape of the command that
        would run (executable + flag names only, no argument values). ``gate_allows``
        means the gate would permit the call, NOT that the backend will succeed
        (write tools may still need owner approval; see ``note``). Never runs the
        backend or mutates anything."""
        target_method = arguments.get("method")
        if not isinstance(target_method, str) or not target_method:
            return {"ok": False, "method": "lib.preview", "error": "preview requires target 'method'"}
        # Accept the wire form (lib_read) as well as the canonical dotted id.
        target_method = _canonical_method(target_method)
        target_args = arguments.get("arguments")
        if not isinstance(target_args, dict):
            target_args = {}
        policy = gate_mod.load_policy()
        brain_root = dispatch_mod.brain_root()
        tier = gate_mod.method_tier(policy, target_method)
        if tier is None:
            return {
                "ok": True,
                "method": "lib.preview",
                "target_method": target_method,
                "tier": None,
                "decision": "deny",
                "reason": f"unknown method: {target_method}",
                "target": None,
                "gate_allows": False,
                "note": None,
            }
        decision = "allow"
        reason: str | None = None
        try:
            gate_mod.enforce(
                target_method, target_args, client_id, policy=policy, brain_root=brain_root
            )
        except gate_mod.GateError as exc:
            decision = "deny"
            reason = str(exc)
        target_shape: JsonObject | None = None
        if decision == "allow" and target_method not in _INPROCESS and target_method != "lib.search":
            try:
                t = dispatch_mod.build_target(target_method, target_args)
                # Show the SHAPE of the command without leaking argument values:
                # the executable basename and the flag names only (tokens that
                # start with "-"). Values — which may be sensitive paths/filenames
                # — are never echoed back, so no blanket redaction is needed.
                argv = [str(a) for a in t.argv]
                executable = os.path.basename(argv[0]) if argv else None
                flags = sorted({a for a in argv[1:] if a.startswith("-")})
                target_shape = {
                    "executable": executable,
                    "flags": flags,
                    "argv_token_count": len(argv),
                    "package": t.package,
                    "has_stdin": t.stdin is not None,
                    "timeout_s": t.timeout,
                }
            except dispatch_mod.GateError as exc:
                decision = "deny"
                reason = str(exc)
        # gate_allows means ONLY that the permission gate would let the call run —
        # NOT that the backend would succeed. Write tools additionally require an
        # owner-approved token at apply time, which the gate does not check; flag
        # that so the caller does not read gate_allows as "this will succeed".
        note = None
        if decision == "allow" and tier == "write":
            note = (
                "gate allows this call; write tools may still require a separate "
                "owner approval at apply time, which the gate does not check"
            )
        return {
            "ok": True,
            "method": "lib.preview",
            "target_method": target_method,
            "tier": tier,
            "decision": decision,
            "reason": reason,
            "target": target_shape,
            "gate_allows": decision == "allow",
            "note": note,
        }

    def _scope_filter_results(self, method: str, data: JsonObject, policy: JsonObject) -> int:
        """Defense-in-depth scope filter for enumerable read results (P5 阶段0).

        The primary scope confinement is the ``path_prefix LIKE 'scope/%'`` pushdown
        that ``gate._apply_read_scope`` injects — out-of-scope rows never enter the
        result set at the index layer (proven by test_invoke_search_results_are_
        confined_to_scope). This is the BELT to that pushdown's SUSPENDERS: a cheap
        post-filter that drops any returned row whose path is not inside
        ``allowed_path_prefixes``, so a mis-injected prefix, a backend bug, or a
        future code path that forgot the pushdown can never surface an out-of-scope
        row. It is O(returned-rows) (a handful, ``limit`` default 10) — a string
        prefix check on the already-confined result set, NOT a re-scan of the index,
        so it adds no measurable retrieval latency.

        No-op when the scope is empty (single-owner full-library default) or the
        method is not row-returning. It is INDEPENDENT of ``hide_excluded_in_results``
        (which only strips excluded TOP dirs like personal-data): a scoped consumer's
        excluded dirs are a subset ceiling, while this drops anything outside the
        allowed subtrees entirely. Returns the number of rows removed.
        """
        if method not in ("lib.search", "lib.recent"):
            return 0
        prefixes = gate_mod._allowed_path_prefixes(policy)
        if not prefixes:
            return 0
        usable = gate_mod._usable_scope_prefixes(prefixes)
        removed = 0
        for key, count_key in (("results", "result_count"), ("documents", "count")):
            rows = data.get(key)
            if not isinstance(rows, list):
                continue
            kept = []
            for r in rows:
                path = str(r.get("path", "")).replace("\\", "/") if isinstance(r, dict) else ""
                # A non-empty scope with no usable prefix fails closed (drop all);
                # a row with no path cannot be proven in-scope, so drop it too.
                if path and usable and gate_mod._in_scope(gate_mod._scope_norm(path), usable):
                    kept.append(r)
                else:
                    removed += 1
            data[key] = kept
            if count_key in data:
                data[count_key] = len(kept)
        if removed:
            data["scope_filtered_count"] = removed
        return removed

    # Fields of lib.get (= ``index status``) that reveal the whole-library shape:
    # the brain ROOT filesystem path, aggregate document/fts/vector counts, and the
    # raw meta blob (which repeats root + counts). A scoped consumer must not learn
    # the full-library size or the brain root path (P5 阶段0 / H2). We KEEP the
    # non-sensitive liveness/config fields (ok, index, schema_version, tokenizer,
    # created_at, embed_model, embed_dim, has_vectors, errors) so legitimate scoped
    # use — "is the index present, fresh, vector-capable?" — still works.
    _GET_SENSITIVE_FIELDS = ("root", "document_count", "fts_count", "vector_count", "meta")

    def _scope_trim_get(self, method: str, data: JsonObject, policy: JsonObject) -> int:
        """Trim lib.get's full-library aggregate under a non-empty scope (H2).

        lib.get stays scope-EXEMPT (a scoped consumer may check index liveness),
        but its raw result leaks the brain root path and full-library counts. Under
        a scope, strip those fields; the ``index``-reject in the gate already forces
        the gateway's own default index, so this trims exactly the whole-library
        metadata surface. No-op outside a scope. Returns fields removed."""
        if method != "lib.get" or not isinstance(data, dict):
            return 0
        if not gate_mod._allowed_path_prefixes(policy):
            return 0
        removed = 0
        for key in self._GET_SENSITIVE_FIELDS:
            if key in data:
                del data[key]
                removed += 1
        if removed:
            data["scope_trimmed"] = True
        return removed

    def _maybe_hide_excluded(self, method: str, data: JsonObject, policy: JsonObject) -> int:
        """Reserved, OFF-by-default content filter. When policy
        ``hide_excluded_in_results`` is true, drop rows under an excluded top dir
        (personal-data) from lib.search / lib.recent results so agents never see
        even their paths/titles. Default false = NO change: this is a single-owner
        library and the owner reads the brain directly, not through the gateway, so
        the full library stays visible by default. Flip the policy field to engage.
        Returns the number of rows removed (0 when the switch is off / nothing to do).
        """
        if method not in ("lib.search", "lib.recent"):
            return 0
        if not bool(policy.get("hide_excluded_in_results", False)):
            return 0
        excluded = {e.lower() for e in gate_mod._excluded_top_dirs(policy)}
        if not excluded:
            return 0
        removed = 0
        for key, count_key in (("results", "result_count"), ("documents", "count")):
            rows = data.get(key)
            if not isinstance(rows, list):
                continue
            kept = []
            for r in rows:
                path = str(r.get("path", "")).replace("\\", "/").lower() if isinstance(r, dict) else ""
                top = path.split("/", 1)[0]
                if top in excluded:
                    removed += 1
                else:
                    kept.append(r)
            data[key] = kept
            if count_key in data:
                data[count_key] = len(kept)
        if removed:
            data["excluded_hidden_count"] = removed
        return removed

    def _brain_indexer(self):
        """Lazy-import ``brain_library.indexer`` once and keep it (and its jieba
        dict) warm for the process lifetime, so repeated lib.search calls in a
        session do not reload jieba (~1s) per query."""
        mod = getattr(self, "_indexer_mod", None)
        if mod is None:
            import sys

            src = str(dispatch_mod.repo_root() / "packages" / "brain-library" / "src")
            if src not in sys.path:
                sys.path.insert(0, src)
            from brain_library import indexer as mod  # noqa: PLC0415

            self._indexer_mod = mod
        return mod

    def _search_inprocess(self, arguments: JsonObject) -> tuple[JsonObject, int]:
        """Run a search query in-process (warm jieba + warm embedder). Returns
        ``(result, exit_code)``.

        Must accept the SAME arguments as the subprocess ``_build_search`` path:
        the metadata filters (doc_type/dept/category/date_from/date_to/order_by) and
        ``mode`` (bm25/vector/hybrid). This warm path is what agents actually hit, so
        anything missing here is silently ignored regardless of the CLI/dispatch wiring.
        Running in-process also keeps the embedding model loaded once for the session
        (get_embedder caches), so hybrid queries don't reload it per call.
        """
        from pathlib import Path

        def _opt_str(key: str) -> str | None:
            value = arguments.get(key)
            return value if isinstance(value, str) and value else None

        query = arguments.get("query")
        if not isinstance(query, str) or not query.strip():
            return {"ok": False, "error": "query is required"}, 1
        index = _opt_str("index") or dispatch_mod.default_index()
        limit = arguments.get("limit", 10)
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            limit = 10
        order_by = arguments.get("order_by")
        order_by = order_by if order_by in ("relevance", "date") else "relevance"
        # Serialize against the startup pre-warm thread so jieba/embedder load
        # exactly once (get_embedder caches but is not load-locked). Normal
        # requests are processed serially in the main thread, so this lock only
        # ever contends warm-thread vs. the first real query — no throughput cost.
        lock = getattr(self, "_search_lock", None) or contextlib.nullcontext()
        with lock:
            result = self._brain_indexer().query_index(
                Path(index),
                query,
                limit=limit,
                suffix=_opt_str("suffix"),
                path_prefix=_opt_str("path_prefix"),
                title_only=bool(arguments.get("title_only")),
                doc_type=_opt_str("doc_type"),
                dept=_opt_str("dept"),
                category=_opt_str("category"),
                date_from=_opt_str("date_from"),
                date_to=_opt_str("date_to"),
                order_by=order_by,
                mode=_opt_str("mode"),
            )
        if isinstance(result, dict):
            result.setdefault("served_inprocess", True)
        return result, (0 if isinstance(result, dict) and result.get("ok") else 1)

    def _status(self, client_id: str, quick: bool = False) -> JsonObject:
        """Aggregate each read surface through the gate.

        ``ok`` means every surface is RESPONSIVE (returned a structured result
        without raising). A surface that responds with its own ``ok:false`` — a
        citation audit finding gaps, or a data source simply not configured on this
        host — is reported as ``degraded``, not a gateway failure. Only a surface
        that raises/crashes counts as ``broken``.

        ``quick=True`` probes only the ``library`` surface (the search index that
        agents actually depend on). The full probe spawns a subprocess per surface
        (~9 on ARM ≈ near a second); the quick path is a sub-200ms liveness check.
        """
        surfaces = {
            # Liveness probe must stay cheap: op=scan walks the entire brain
            # root (multi-GB, >120s) and exceeds the 60s dispatch timeout,
            # falsely marking the library surface "broken". op=docpacks is
            # bounded and still exercises the brain_library CLI through the gate.
            "library": {"method": "lib.list", "args": {"op": "docpacks"}},
            "docpack": {"method": "lib.docpack", "args": {"op": "doctor"}},
            "citation": {"method": "lib.citation", "args": {"op": "doctor"}},
            "hub": {"method": "lib.hub", "args": {"op": "doctor"}},
            "context": {"method": "lib.context", "args": {"op": "doctor"}},
            "profile": {"method": "lib.profile", "args": {"op": "doctor"}},
            "review": {"method": "lib.review", "args": {"op": "doctor"}},
            "automation": {"method": "lib.automation", "args": {"op": "doctor"}},
            "runtime": {"method": "lib.runtime", "args": {"op": "doctor"}},
        }
        if quick:
            surfaces = {"library": surfaces["library"]}
        results: JsonObject = {}
        broken: list[str] = []
        degraded: list[str] = []
        for name, spec in surfaces.items():
            try:
                data = self.invoke(spec["method"], dict(spec["args"]), client_id=client_id)
                ok = bool(data.get("ok", False))
                results[name] = {"ok": ok, "responsive": True}
                if not ok:
                    degraded.append(name)
            except Exception as exc:  # a surface that raises is broken, not just degraded
                results[name] = {"ok": False, "responsive": False, "error": str(exc)[:200]}
                broken.append(name)
        return {
            "ok": not broken,
            "server": SERVER_NAME,
            "quick": quick,
            "surfaces": results,
            "broken": broken,
            "degraded": degraded,
            "privacy": {"sensitive_bodies_read": False},
        }

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        """Compatibility shim mirroring the read-tool MCP idiom.

        Normalizes the wire name the same way the live ``tools/call`` branch does,
        so the shim stays a correct boundary no matter who calls it. (It is unused
        today — ``handle_message`` dispatches inline — but the sibling read-tool
        servers route ``tools/call`` through their ``call_tool``, so keeping the
        translation here prevents a latent wire-name bypass if this gateway is
        ever realigned to that idiom.)
        """
        method_name = _canonical_method(name)
        data = self.invoke(method_name, arguments, client_id=getattr(self, "_client_id", "default"))
        return _tool_result(data, render_text=getattr(self, "_render_text", None))


def subprocess_timeout() -> type[BaseException]:
    import subprocess

    return subprocess.TimeoutExpired


def _env_bool(name: str, default: str) -> bool:
    """Parse an env var as a boolean, matching the runtime's shared convention
    (apps/assistant-gateway/_common.py ``env_bool``): {1, true, yes, on} = True,
    everything else = False, case-insensitive. ``default`` is the string used when
    the var is unset (so the default is parsed the same way, no special-casing).

    This replaces an earlier ``== '0'`` bare-string check for PREWARM that only
    recognised the literal ``'0'`` as OFF — ``false`` / ``no`` / ``off`` all stayed
    ON, a foot-gun on the memory-tight ARM host (each prewarm pins an ONNX embedder).
    """
    return os.environ.get(name, default).strip().lower() in {"1", "true", "yes", "on"}


def _idle_timeout_seconds() -> float:
    """Stdin idle timeout in seconds (env ``RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT``).

    A half-open/dead client (ssh connection silently dropped, client SIGKILLed)
    never sends EOF, so plain ``for line in sys.stdin`` blocks forever and the
    process leaks — observed as dozens of stale ``sshd@notty`` mcp_server
    processes accumulating on the host. Exiting after this many idle seconds
    bounds the leak. Live clients send requests/pings well inside the window;
    a value <= 0 disables the guard (back to blocking iteration).
    """
    raw = os.environ.get("RTIME_LIBRARY_GATEWAY_IDLE_TIMEOUT", "1800")
    try:
        return float(raw)
    except ValueError:
        return 1800.0


def _handle_line(server: RtimeLibraryGatewayMCP, line: str) -> None:
    raw = line.strip()
    if not raw:
        return
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(_json_dumps(_error_response(None, -32700, f"Parse error: {exc.msg}")), flush=True)
        return
    response = server.handle_message(message)
    if response is not None:
        print(_json_dumps(response), flush=True)


def _maybe_prewarm(server: RtimeLibraryGatewayMCP) -> None:
    """Load jieba + the embedding model in a background thread at startup so the
    first hybrid ``lib.search`` of a session isn't a ~1.4s cold load (the embedder
    is an ONNX model that's slow to load on the orangepi ARM host). The warm query
    runs through ``_search_inprocess`` so it primes the exact warm path real queries
    hit. Serialized with real queries via ``_search_lock`` so the model loads once.

    Best-effort: default ON; disabled with ``RTIME_LIBRARY_GATEWAY_PREWARM`` set to
    a falsy value (0 / false / no / off, case-insensitive); any failure (e.g. no
    index built on this host) is swallowed — a missing warm-up must never break
    serving.
    """
    if not _env_bool("RTIME_LIBRARY_GATEWAY_PREWARM", "1"):
        return
    server._search_lock = threading.Lock()

    def _warm() -> None:
        try:
            server._search_inprocess({"query": "预热", "limit": 1})
        except Exception:  # noqa: BLE001 — warm-up is best-effort
            pass

    threading.Thread(target=_warm, name="lib-gateway-prewarm", daemon=True).start()


def serve_stdio(server: RtimeLibraryGatewayMCP | None = None) -> int:
    server = server or RtimeLibraryGatewayMCP()
    _maybe_prewarm(server)
    idle_timeout = _idle_timeout_seconds()
    try:
        fd = sys.stdin.fileno()
    except (AttributeError, ValueError, OSError):
        fd = None
    # Plain blocking iteration when stdin has no pollable fd (tests, captured
    # pipes) or the idle guard is disabled.
    if fd is None or idle_timeout <= 0:
        for line in sys.stdin:
            _handle_line(server, line)
        return 0
    # select() on the raw fd lets a silently-dead client time out instead of
    # blocking forever; os.read avoids the buffered-iterator pitfall where
    # batched messages sit in Python's buffer (invisible to select).
    buffer = b""
    while True:
        ready, _, _ = select.select([fd], [], [], idle_timeout)
        if not ready:
            break  # idle window elapsed -> assume dead/half-open client, exit
        chunk = os.read(fd, 65536)
        if not chunk:
            break  # EOF: client closed stdin
        buffer += chunk
        while b"\n" in buffer:
            line, buffer = buffer.split(b"\n", 1)
            _handle_line(server, line.decode("utf-8", "replace"))
    return 0


def _handle_line_sock(server: RtimeLibraryGatewayMCP, line: str, conn: socket.socket) -> None:
    raw = line.strip()
    if not raw:
        return
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        conn.sendall(
            (_json_dumps(_error_response(None, -32700, f"Parse error: {exc.msg}")) + "\n").encode()
        )
        return
    response = server.handle_message(message)
    if response is not None:
        conn.sendall((_json_dumps(response) + "\n").encode())


def _serve_one_connection(server: RtimeLibraryGatewayMCP, conn: socket.socket) -> None:
    """Handle one MCP client (one `socat`-bridged `claude` process) until it closes.
    Newline-delimited JSON-RPC over the socket, same wire format as serve_stdio."""
    with contextlib.closing(conn):
        buffer = b""
        while True:
            try:
                chunk = conn.recv(65536)
            except OSError:
                break
            if not chunk:
                break  # client closed (claude exited)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                _handle_line_sock(server, line.decode("utf-8", "replace"), conn)


def serve_unix_socket(socket_path: str, server: RtimeLibraryGatewayMCP | None = None) -> int:
    """Persistent warm daemon: one process, jieba/embedder loaded ONCE (``_maybe_prewarm``),
    serving many short-lived per-message ``claude`` connections over a unix socket. Each
    bridge's CLI connects via ``socat - UNIX-CONNECT:<socket_path>`` so the cold-start
    (jieba ~1s + embedder) is paid once at boot, not per message. Connections are served
    sequentially (``_search_lock`` already serializes searches); single-owner use only.
    """
    server = server or RtimeLibraryGatewayMCP()
    _maybe_prewarm(server)
    parent = os.path.dirname(socket_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    with contextlib.suppress(FileNotFoundError):
        os.unlink(socket_path)  # clear a stale socket from a prior run
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(socket_path)
    with contextlib.suppress(OSError):
        os.chmod(socket_path, 0o600)  # owner-only
    sock.listen(8)
    try:
        while True:
            try:
                conn, _ = sock.accept()
            except OSError:
                break
            _serve_one_connection(server, conn)
    finally:
        sock.close()
        with contextlib.suppress(FileNotFoundError):
            os.unlink(socket_path)
    return 0


class _ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True


def serve_http(host: str, port: int, server: RtimeLibraryGatewayMCP | None = None) -> int:
    """Persistent warm daemon over the MCP *streamable-HTTP* transport — the transport the
    ``claude`` CLI connects to natively (``{"type":"http","url":...}``), no socat. One warm
    process (jieba/embedder loaded once) serves many per-message CLI connections. Each POST
    carries one JSON-RPC message → handled by the shared ``handle_message`` → JSON response;
    notifications (no response) get 202. ``_search_lock`` (a threading lock) serializes the
    search across request threads."""
    gw = server or RtimeLibraryGatewayMCP()
    _maybe_prewarm(gw)

    class Handler(http.server.BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, *args: object) -> None:  # silence access logging
            pass

        def _write(self, code: int, body: bytes = b"", ctype: str = "application/json") -> None:
            self.send_response(code)
            if body:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if body:
                self.wfile.write(body)

        def do_POST(self) -> None:
            try:
                length = int(self.headers.get("Content-Length", 0))
                raw = self.rfile.read(length) if length else b""
                message = json.loads(raw)
            except Exception as exc:
                self._write(400, _json_dumps(_error_response(None, -32700, f"Parse error: {exc}")).encode())
                return
            try:
                response = gw.handle_message(message)
            except Exception as exc:  # noqa: BLE001 — never 500 the CLI; return JSON-RPC error
                mid = message.get("id") if isinstance(message, dict) else None
                self._write(200, _json_dumps(_error_response(mid, -32603, str(exc))).encode())
                return
            if response is None:
                self._write(202)  # notification, no body
            else:
                self._write(200, _json_dumps(response).encode())

        def do_GET(self) -> None:  # no server->client SSE stream needed for our tools
            self._write(405)

    httpd = _ThreadingHTTPServer((host, port), Handler)
    try:
        httpd.serve_forever()
    finally:
        httpd.server_close()
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    def _flag(name: str, env: str) -> str:
        if name in args:
            i = args.index(name)
            if i + 1 < len(args):
                return args[i + 1]
        return os.environ.get(env, "").strip()

    http_port = _flag("--http-port", "RTIME_LIBRARY_GATEWAY_HTTP_PORT")
    if http_port:
        host = os.environ.get("RTIME_LIBRARY_GATEWAY_HTTP_HOST", "127.0.0.1").strip()
        return serve_http(host, int(http_port))
    socket_path = _flag("--unix-socket", "RTIME_LIBRARY_GATEWAY_SOCKET")
    if socket_path:
        return serve_unix_socket(socket_path)
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
