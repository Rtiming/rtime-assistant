# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for dynamic context unlock planning."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import build_pack, build_plan, doctor, explain_plan


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "rtime-context"
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


def _path_argument(arguments: JsonObject, key: str) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        return None
    return Path(str(raw)).expanduser().resolve()


def _request_argument(arguments: JsonObject) -> str:
    raw = arguments.get("request")
    if not isinstance(raw, str) or not raw.strip():
        raise ToolError("missing required argument: request")
    return raw


class RtimeContextMCP:
    def tools(self) -> list[JsonObject]:
        request_args = {
            "request": {"type": "string", "description": "User request or task text."},
            "workspace": {"type": "string", "description": "Optional active workspace path."},
            "brain_root": {"type": "string", "description": "Optional brain root path."},
            "hub_root": {"type": "string", "description": "Optional rtime-hub root path."},
            "entry": {"type": "string", "description": "Entry adapter name, such as cli or feishu."},
            "allow_sensitive": {
                "type": "boolean",
                "description": "Plan sensitive metadata lanes; does not read secret bodies.",
            },
        }
        return [
            {
                "name": "context.doctor",
                "title": "Context Doctor",
                "description": "Read-only check of context planner surfaces and known roots.",
                "inputSchema": _schema(
                    {"repo_root": {"type": "string", "description": "Optional repository root."}}
                ),
            },
            {
                "name": "context.plan",
                "title": "Context Plan",
                "description": "Build a deterministic ContextUnlockPlan for a request.",
                "inputSchema": _schema(request_args, required=["request"]),
            },
            {
                "name": "context.pack",
                "title": "Context Pack",
                "description": "Build a Context Pack skeleton from the unlock plan.",
                "inputSchema": _schema(request_args, required=["request"]),
            },
            {
                "name": "context.explain",
                "title": "Explain Context",
                "description": "Explain why context lanes would be unlocked for a request.",
                "inputSchema": _schema(request_args, required=["request"]),
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
                        "title": "Rtime Context",
                        "version": SERVER_VERSION,
                        "description": "Read-only ContextUnlockPlan and Context Pack skeletons.",
                    },
                    "instructions": (
                        "Use read-only context tools for planning only. This server does not "
                        "retrieve secret file bodies, write memories, edit files, deploy, or restart services."
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
            "context.doctor": self._tool_doctor,
            "context.plan": self._tool_plan,
            "context.pack": self._tool_pack,
            "context.explain": self._tool_explain,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"context-mcp-{uuid.uuid4().hex}"
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
        repo_root = _path_argument(arguments, "repo_root")
        return doctor(repo_root)

    def _common_kwargs(self, arguments: JsonObject) -> JsonObject:
        return {
            "workspace": _path_argument(arguments, "workspace"),
            "brain_root": _path_argument(arguments, "brain_root"),
            "hub_root": _path_argument(arguments, "hub_root"),
            "entry": str(arguments.get("entry") or "mcp"),
            "allow_sensitive": bool(arguments.get("allow_sensitive", False)),
        }

    def _tool_plan(self, arguments: JsonObject) -> JsonObject:
        return build_plan(_request_argument(arguments), **self._common_kwargs(arguments))

    def _tool_pack(self, arguments: JsonObject) -> JsonObject:
        return build_pack(_request_argument(arguments), **self._common_kwargs(arguments))

    def _tool_explain(self, arguments: JsonObject) -> JsonObject:
        plan = build_plan(_request_argument(arguments), **self._common_kwargs(arguments))
        return explain_plan(plan)

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
        log_path_raw = os.environ.get("RTIME_CONTEXT_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("workspace", "brain_root", "hub_root", "repo_root")
            if key in arguments and arguments[key]
        ]
        request = arguments.get("request", "")
        record = {
            "run_id": run_id,
            "entry": "mcp-stdio",
            "tool": tool,
            "permission_tier": "read_only",
            "input_paths": input_paths,
            "request_length": len(request) if isinstance(request, str) else 0,
            "status": status,
            "duration_ms": duration_ms,
            "failure_reason": failure_reason,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def serve_stdio(server: RtimeContextMCP | None = None) -> int:
    server = server or RtimeContextMCP()
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
