# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for Obsidian/Zotero citation diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import doctor, panel, resolve_brain_root, scan_citations


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "brain-citation"
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


def _path_argument(arguments: JsonObject, key: str, *, required: bool = False) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"missing required argument: {key}")
        return None
    return Path(str(raw)).expanduser().resolve()


def _brain_root_argument(arguments: JsonObject) -> Path:
    root = resolve_brain_root(_path_argument(arguments, "root"))
    if root is None:
        raise ToolError("brain root not found; pass root or set BRAIN_ROOT")
    if not root.is_dir():
        raise ToolError("brain root is not a directory", data={"root": str(root)})
    return root


class BrainCitationMCP:
    def tools(self) -> list[JsonObject]:
        root_arg = {
            "root": {
                "type": "string",
                "description": "brain root. Defaults to BRAIN_ROOT or known Mac/orangepi mounts.",
            }
        }
        scan_options = {
            **root_arg,
            "max_files": {"type": "integer", "minimum": 1},
            "sample_limit": {"type": "integer", "minimum": 0},
        }
        return [
            {
                "name": "citation.doctor",
                "title": "Citation Doctor",
                "description": "Read-only check of citation tooling and brain root signals.",
                "inputSchema": _schema(root_arg),
            },
            {
                "name": "citation.scan",
                "title": "Citation Scan",
                "description": "Scan Markdown, BibTeX, Zotero URI, Obsidian wikilink, and DocPack citation signals.",
                "inputSchema": _schema(scan_options),
            },
            {
                "name": "citation.panel",
                "title": "Citation Panel",
                "description": "Build a review-friendly citation crosswalk panel for Obsidian/Zotero workflows.",
                "inputSchema": _schema(scan_options),
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
                        "title": "Brain Citation",
                        "version": SERVER_VERSION,
                        "description": "Read-only Obsidian/Zotero citation diagnostics.",
                    },
                    "instructions": (
                        "Use read-only citation tools for review and planning only. This server "
                        "does not write Obsidian files, sync Zotero, approve memory, or expose secrets."
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
            "citation.doctor": self._tool_doctor,
            "citation.scan": self._tool_scan,
            "citation.panel": self._tool_panel,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"citation-mcp-{uuid.uuid4().hex}"
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
        return doctor(_path_argument(arguments, "root"))

    def _tool_scan(self, arguments: JsonObject) -> JsonObject:
        root = _brain_root_argument(arguments)
        return scan_citations(
            root,
            max_files=int(arguments.get("max_files", 50000)),
            sample_limit=int(arguments.get("sample_limit", 20)),
        )

    def _tool_panel(self, arguments: JsonObject) -> JsonObject:
        root = _brain_root_argument(arguments)
        return panel(
            root,
            max_files=int(arguments.get("max_files", 50000)),
            sample_limit=int(arguments.get("sample_limit", 20)),
        )

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
        log_path_raw = os.environ.get("BRAIN_CITATION_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [str(arguments[key]) for key in ("root",) if key in arguments and arguments[key]]
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


def serve_stdio(server: BrainCitationMCP | None = None) -> int:
    server = server or BrainCitationMCP()
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
