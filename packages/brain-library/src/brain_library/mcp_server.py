# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for brain library index diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import doctor, resolve_brain_root, scan_library, summarize_docpacks
from .indexer import index_status, query_index

PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "brain-library"
SERVER_VERSION = "0.1.0"

JsonObject = dict[str, Any]


def _negotiate_protocol_version(client_version: Any) -> str:
    if isinstance(client_version, str) and client_version:
        return client_version
    return PROTOCOL_VERSION


class ToolError(Exception):
    """A tool-level failure returned as an MCP tool error."""

    def __init__(self, message: str, *, data: JsonObject | None = None):
        super().__init__(message)
        self.data = data or {}


def _json_dumps(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def _tool_result(data: JsonObject, *, is_error: bool = False) -> JsonObject:
    text = json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True)
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
        "additionalProperties": False,
    }


def _path_argument(arguments: JsonObject, key: str, *, required: bool = True) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"missing required argument: {key}")
        return None
    return Path(str(raw)).expanduser().resolve()


def _brain_root_argument(arguments: JsonObject) -> Path:
    root = resolve_brain_root(_path_argument(arguments, "root", required=False))
    if root is None:
        raise ToolError("brain root not found; pass root or set BRAIN_ROOT")
    if not root.is_dir():
        raise ToolError("brain root is not a directory", data={"root": str(root)})
    return root


class BrainLibraryMCP:
    def tools(self) -> list[JsonObject]:
        root_arg = {
            "root": {
                "type": "string",
                "description": "brain root. Defaults to BRAIN_ROOT or known Mac/orangepi mounts.",
            }
        }
        return [
            {
                "name": "library.doctor",
                "title": "Library Doctor",
                "description": "Read-only check of local brain root and repository tooling surfaces.",
                "inputSchema": _schema(root_arg),
            },
            {
                "name": "library.scan",
                "title": "Library Scan",
                "description": "Scan the brain library for Obsidian, Zotero, DocPack, and index signals.",
                "inputSchema": _schema(
                    {
                        **root_arg,
                        "max_files": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Maximum files to scan before reporting truncation.",
                        },
                        "sample_limit": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Maximum sample paths to return per section.",
                        },
                    }
                ),
            },
            {
                "name": "library.docpacks",
                "title": "Library DocPacks",
                "description": "Summarize DocPack directories and citation readiness.",
                "inputSchema": _schema(
                    {
                        **root_arg,
                        "sample_limit": {
                            "type": "integer",
                            "minimum": 0,
                            "description": "Maximum DocPack samples to return.",
                        },
                    }
                ),
            },
            {
                "name": "library.index_status",
                "title": "Library Index Status",
                "description": "Read metadata and counts from a derived SQLite/BM25 index.",
                "inputSchema": _schema(
                    {
                        "index": {
                            "type": "string",
                            "description": "Path to a derived brain-library SQLite index.",
                        }
                    },
                    required=["index"],
                ),
            },
            {
                "name": "library.index_query",
                "title": "Library Index Query",
                "description": "Query a derived index (BM25 + optional semantic-vector hybrid) without modifying it.",
                "inputSchema": _schema(
                    {
                        "index": {
                            "type": "string",
                            "description": "Path to a derived brain-library SQLite index.",
                        },
                        "query": {
                            "type": "string",
                            "description": "Search query.",
                        },
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Maximum results to return.",
                        },
                        "mode": {
                            "type": "string",
                            "enum": ["bm25", "vector", "hybrid"],
                            "description": "Retrieval mode; default hybrid if the index has vectors, else bm25.",
                        },
                    },
                    required=["index", "query"],
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
            protocol_version = _negotiate_protocol_version(client_version)
            return _response(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "Brain Library",
                        "version": SERVER_VERSION,
                        "description": "Read-only index diagnostics for the rtime brain library.",
                    },
                    "instructions": (
                        "Use read-only library tools for review and planning only. This server "
                        "does not build indexes, edit Obsidian files, or sync Zotero."
                    ),
                },
            )

        if method == "ping":
            return _response(request_id, {})

        if method == "tools/list":
            tools = self.tools()
            for _t in tools:
                _t["name"] = _t["name"].replace(".", "_")
            return _response(request_id, {"tools": tools})

        if method == "tools/call":
            params = message.get("params")
            if not isinstance(params, dict):
                return _error_response(request_id, -32602, "Invalid params")
            name = params.get("name")
            arguments = params.get("arguments", {})
            if not isinstance(name, str) or not isinstance(arguments, dict):
                return _error_response(request_id, -32602, "Invalid params")
            try:
                result = self.call_tool(name, arguments)
                return _response(request_id, result)
            except ToolError as exc:
                data = {"ok": False, "error": str(exc), **exc.data}
                return _response(request_id, _tool_result(data, is_error=True))
            except Exception as exc:  # pragma: no cover - defensive server guard
                return _response(
                    request_id,
                    _tool_result({"ok": False, "error": str(exc)}, is_error=True),
                )

        if method == "shutdown":
            return _response(request_id, {})

        if has_id:
            return _error_response(request_id, -32601, f"Method not found: {method}")
        return None

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        handlers: dict[str, Callable[[JsonObject], JsonObject]] = {
            "library.doctor": self._tool_doctor,
            "library.scan": self._tool_scan,
            "library.docpacks": self._tool_docpacks,
            "library.index_status": self._tool_index_status,
            "library.index_query": self._tool_index_query,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"library-mcp-{uuid.uuid4().hex}"
        started = time.monotonic()
        status = "ok"
        failure_reason = ""
        try:
            data = handler(arguments)
            if data.get("ok") is False:
                status = "failed"
                failure_reason = ",".join(str(item) for item in data.get("risks", []))[:300]
            data.setdefault("run_id", run_id)
            data.setdefault("tool", name)
            return _tool_result(data, is_error=status != "ok")
        except Exception as exc:
            status = "failed"
            failure_reason = str(exc)
            raise
        finally:
            duration_ms = int((time.monotonic() - started) * 1000)
            self._record_run(
                run_id=run_id,
                tool=name,
                arguments=arguments,
                status=status,
                duration_ms=duration_ms,
                failure_reason=failure_reason,
            )

    def _tool_doctor(self, arguments: JsonObject) -> JsonObject:
        root = resolve_brain_root(_path_argument(arguments, "root", required=False))
        return doctor(root)

    def _tool_scan(self, arguments: JsonObject) -> JsonObject:
        root = _brain_root_argument(arguments)
        max_files = int(arguments.get("max_files", 50000))
        sample_limit = int(arguments.get("sample_limit", 20))
        return scan_library(root, max_files=max_files, sample_limit=sample_limit)

    def _tool_docpacks(self, arguments: JsonObject) -> JsonObject:
        root = _brain_root_argument(arguments)
        sample_limit = int(arguments.get("sample_limit", 20))
        return {"ok": True, "root": str(root), **summarize_docpacks(root, sample_limit=sample_limit)}

    def _tool_index_status(self, arguments: JsonObject) -> JsonObject:
        index = _path_argument(arguments, "index")
        assert index is not None
        return index_status(index)

    def _tool_index_query(self, arguments: JsonObject) -> JsonObject:
        index = _path_argument(arguments, "index")
        assert index is not None
        query = arguments.get("query")
        if not isinstance(query, str):
            raise ToolError("missing required argument: query")
        limit = int(arguments.get("limit", 10))
        mode = arguments.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise ToolError("mode must be a string")
        return query_index(index, query, limit=limit, mode=mode)

    def _record_run(
        self,
        *,
        run_id: str,
        tool: str,
        arguments: JsonObject,
        status: str,
        duration_ms: int,
        failure_reason: str,
    ) -> None:
        log_path_raw = os.environ.get("BRAIN_LIBRARY_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("root", "index")
            if key in arguments and arguments[key]
        ]
        record = {
            "run_id": run_id,
            "entry": "mcp-stdio",
            "tool": tool,
            "permission_tier": "read_only",
            "input_paths": input_paths,
            "status": status,
            "duration_ms": duration_ms,
            "failure_reason": failure_reason,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def serve_stdio(server: BrainLibraryMCP | None = None) -> int:
    server = server or BrainLibraryMCP()
    for line in sys.stdin:
        raw = line.strip()
        if not raw:
            continue
        try:
            message = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(_json_dumps(_error_response(None, -32700, f"Parse error: {exc.msg}")), flush=True)
            continue
        response = server.handle_message(message)
        if response is not None:
            print(_json_dumps(response), flush=True)
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    del argv
    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
