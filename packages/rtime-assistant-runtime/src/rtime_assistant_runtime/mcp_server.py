# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for rtime-assistant runtime diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import (
    check_docker_prod,
    check_templates,
    doctor,
    find_repo_root,
    summarize_run_log,
    tail_run_log,
)


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "rtime-assistant-runtime"
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


def _repo_root_from_args(arguments: JsonObject) -> Path:
    raw = arguments.get("repo_root") or os.environ.get("RTIME_ASSISTANT_ROOT")
    if raw:
        return Path(str(raw)).expanduser().resolve()
    return find_repo_root()


def _path_argument(arguments: JsonObject, key: str, *, required: bool = True) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        if required:
            raise ToolError(f"missing required argument: {key}")
        return None
    return Path(str(raw)).expanduser().resolve()


class RtimeRuntimeMCP:
    def tools(self) -> list[JsonObject]:
        common_repo = {
            "repo_root": {
                "type": "string",
                "description": "Optional rtime-assistant repository root.",
            }
        }
        run_log_path = {
            "path": {
                "type": "string",
                "description": "External runtime JSONL run-log path. Defaults to RTIME_ASSISTANT_RUN_LOG.",
            }
        }
        return [
            {
                "name": "runtime.doctor",
                "title": "Runtime Doctor",
                "description": "Read-only check of runtime files, templates, and default log path.",
                "inputSchema": _schema(common_repo),
            },
            {
                "name": "runtime.templates_check",
                "title": "Runtime Template Check",
                "description": "Check systemd service/timer templates without deploying them.",
                "inputSchema": _schema(common_repo),
            },
            {
                "name": "runtime.docker_prod_check",
                "title": "Runtime Docker Production Check",
                "description": (
                    "Read-only check of production Compose files, .dockerignore, bridge "
                    "simulation entry, env template, helper, docs, systemd wrapper, and "
                    "optional env-file key coverage without running Docker."
                ),
                "inputSchema": _schema(
                    {
                        **common_repo,
                        "env_file": {
                            "type": "string",
                            "description": (
                                "Optional server env file. Values are never returned; only key "
                                "names and permissions are inspected."
                            ),
                        },
                    }
                ),
            },
            {
                "name": "runtime.run_log_summary",
                "title": "Runtime Run Log Summary",
                "description": "Summarize an external runtime JSONL run log with redaction.",
                "inputSchema": _schema(run_log_path),
            },
            {
                "name": "runtime.run_log_tail",
                "title": "Runtime Run Log Tail",
                "description": "Return the last redacted records from an external runtime JSONL run log.",
                "inputSchema": _schema(
                    {
                        **run_log_path,
                        "limit": {
                            "type": "integer",
                            "minimum": 1,
                            "description": "Number of records to return.",
                        },
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
            protocol_version = _negotiate_protocol_version(client_version)
            return _response(
                request_id,
                {
                    "protocolVersion": protocol_version,
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {
                        "name": SERVER_NAME,
                        "title": "Rtime Assistant Runtime",
                        "version": SERVER_VERSION,
                        "description": "Read-only runtime diagnostics for rtime-assistant.",
                    },
                    "instructions": (
                        "Use read-only runtime tools for diagnostics only. This server does not "
                        "deploy, restart services, send messages, or read secrets."
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
            "runtime.doctor": self._tool_doctor,
            "runtime.templates_check": self._tool_templates_check,
            "runtime.docker_prod_check": self._tool_docker_prod_check,
            "runtime.run_log_summary": self._tool_run_log_summary,
            "runtime.run_log_tail": self._tool_run_log_tail,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"runtime-mcp-{uuid.uuid4().hex}"
        started = time.monotonic()
        status = "ok"
        failure_reason = ""
        try:
            data = handler(arguments)
            if data.get("ok") is False:
                status = "failed"
                failure_reason = ",".join(str(item) for item in data.get("errors", []))[:300]
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
        return doctor(_repo_root_from_args(arguments))

    def _tool_templates_check(self, arguments: JsonObject) -> JsonObject:
        return check_templates(_repo_root_from_args(arguments))

    def _tool_docker_prod_check(self, arguments: JsonObject) -> JsonObject:
        env_file = _path_argument(arguments, "env_file", required=False)
        return check_docker_prod(_repo_root_from_args(arguments), env_file)

    def _tool_run_log_summary(self, arguments: JsonObject) -> JsonObject:
        path = _path_argument(arguments, "path", required=False)
        if path is None:
            from .cli import _default_run_log_path

            path = _default_run_log_path()
        return summarize_run_log(path)

    def _tool_run_log_tail(self, arguments: JsonObject) -> JsonObject:
        path = _path_argument(arguments, "path", required=False)
        if path is None:
            from .cli import _default_run_log_path

            path = _default_run_log_path()
        limit = int(arguments.get("limit", 5))
        return tail_run_log(path, limit)

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
        log_path_raw = os.environ.get("RTIME_RUNTIME_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("repo_root", "path", "env_file")
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


def serve_stdio(server: RtimeRuntimeMCP | None = None) -> int:
    server = server or RtimeRuntimeMCP()
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
