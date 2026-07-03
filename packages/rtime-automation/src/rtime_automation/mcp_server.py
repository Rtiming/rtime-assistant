# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for automation and reminder diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import doctor, find_repo_root, panel, plan_automation, reminder_health, summarize_reminders


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "rtime-automation"
SERVER_VERSION = "0.1.0"

JsonObject = dict[str, Any]


def _negotiate_protocol_version(client_version: Any) -> str:
    if isinstance(client_version, str) and client_version:
        return client_version
    return PROTOCOL_VERSION


class ToolError(Exception):
    """A tool-level failure returned as an MCP tool error."""


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


def _path_arg(arguments: JsonObject, key: str) -> Path | None:
    raw = arguments.get(key)
    if raw is None or raw == "":
        return None
    return Path(str(raw)).expanduser().resolve()


def _repo_root(arguments: JsonObject) -> Path:
    root = _path_arg(arguments, "repo_root")
    if root:
        return root
    return find_repo_root()


class RtimeAutomationMCP:
    def tools(self) -> list[JsonObject]:
        root_args = {
            "repo_root": {"type": "string", "description": "Optional rtime-assistant repository root."},
            "reminders": {"type": "string", "description": "Optional reminders JSONL path."},
        }
        return [
            {
                "name": "automation.doctor",
                "title": "Automation Doctor",
                "description": "Read-only check of automation, reminder, and packaging surfaces.",
                "inputSchema": _schema(root_args),
            },
            {
                "name": "automation.reminders",
                "title": "Reminder Summary",
                "description": "Summarize reminders JSONL metadata without returning messages or targets.",
                "inputSchema": _schema(
                    {
                        "path": {"type": "string", "description": "Optional reminders JSONL path."},
                        "sample_limit": {"type": "integer", "description": "Maximum metadata samples."},
                    }
                ),
            },
            {
                "name": "automation.health",
                "title": "Reminder Health",
                "description": "Surface failed or stuck reminders without returning messages, targets, or raw error text.",
                "inputSchema": _schema(
                    {
                        "path": {"type": "string", "description": "Optional reminders JSONL path."},
                        "sample_limit": {"type": "integer", "description": "Maximum metadata samples."},
                    }
                ),
            },
            {
                "name": "automation.panel",
                "title": "Automation Panel",
                "description": "Build a review-friendly automation and reminder panel.",
                "inputSchema": _schema(
                    {
                        **root_args,
                        "sample_limit": {"type": "integer", "description": "Maximum metadata samples."},
                    }
                ),
            },
            {
                "name": "automation.plan",
                "title": "Automation Plan",
                "description": "Plan a reminder, scheduler, notification, or workflow change without writing it.",
                "inputSchema": _schema(
                    {
                        "repo_root": root_args["repo_root"],
                        "request": {"type": "string", "description": "Automation request."},
                    },
                    required=["request"],
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
                        "title": "Rtime Automation",
                        "version": SERVER_VERSION,
                        "description": "Read-only automation and reminder diagnostics.",
                    },
                    "instructions": (
                        "Use read-only automation tools for review and planning only. This server "
                        "does not send notifications, write reminders, deploy, restart services, or read secrets."
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
                return _response(request_id, self.call_tool(name, arguments))
            except ToolError as exc:
                return _response(request_id, _tool_result({"ok": False, "error": str(exc)}, is_error=True))
            except Exception as exc:  # pragma: no cover - defensive server guard
                return _response(request_id, _tool_result({"ok": False, "error": str(exc)}, is_error=True))
        if method == "shutdown":
            return _response(request_id, {})
        if has_id:
            return _error_response(request_id, -32601, f"Method not found: {method}")
        return None

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        handlers: dict[str, Callable[[JsonObject], JsonObject]] = {
            "automation.doctor": self._tool_doctor,
            "automation.reminders": self._tool_reminders,
            "automation.health": self._tool_health,
            "automation.panel": self._tool_panel,
            "automation.plan": self._tool_plan,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")
        run_id = f"automation-mcp-{uuid.uuid4().hex}"
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
            self._record_run(
                run_id=run_id,
                tool=name,
                arguments=arguments,
                status=status,
                duration_ms=int((time.monotonic() - started) * 1000),
                failure_reason=failure_reason,
            )

    def _tool_doctor(self, arguments: JsonObject) -> JsonObject:
        return doctor(_path_arg(arguments, "repo_root"), _path_arg(arguments, "reminders"))

    def _tool_reminders(self, arguments: JsonObject) -> JsonObject:
        path = _path_arg(arguments, "path")
        if path is None:
            raw_env = os.environ.get("RTIME_REMINDERS_PATH")
            if raw_env:
                path = Path(raw_env).expanduser().resolve()
            else:
                from .cli import default_reminder_path

                path = default_reminder_path()
        limit = arguments.get("sample_limit", 10)
        if not isinstance(limit, int):
            raise ToolError("sample_limit must be an integer")
        return summarize_reminders(path, sample_limit=limit)

    def _tool_health(self, arguments: JsonObject) -> JsonObject:
        path = _path_arg(arguments, "path")
        if path is None:
            raw_env = os.environ.get("RTIME_REMINDERS_PATH")
            if raw_env:
                path = Path(raw_env).expanduser().resolve()
            else:
                from .cli import default_reminder_path

                path = default_reminder_path()
        limit = arguments.get("sample_limit", 10)
        if not isinstance(limit, int):
            raise ToolError("sample_limit must be an integer")
        return reminder_health(path, sample_limit=limit)

    def _tool_panel(self, arguments: JsonObject) -> JsonObject:
        limit = arguments.get("sample_limit", 10)
        if not isinstance(limit, int):
            raise ToolError("sample_limit must be an integer")
        return panel(_repo_root(arguments), reminder_path=_path_arg(arguments, "reminders"), sample_limit=limit)

    def _tool_plan(self, arguments: JsonObject) -> JsonObject:
        request = arguments.get("request")
        if not isinstance(request, str) or not request.strip():
            raise ToolError("missing required argument: request")
        return plan_automation(request, _path_arg(arguments, "repo_root"))

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
        raw = os.environ.get("RTIME_AUTOMATION_MCP_RUN_LOG")
        if not raw:
            return
        path = Path(raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("repo_root", "reminders", "path")
            if key in arguments and arguments[key]
        ]
        record = {
            "run_id": run_id,
            "entry": "mcp-stdio",
            "tool": tool,
            "permission_tier": "read_only",
            "input_paths": input_paths,
            "request_length": len(str(arguments.get("request", ""))) if "request" in arguments else 0,
            "status": status,
            "duration_ms": duration_ms,
            "failure_reason": failure_reason,
        }
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def serve_stdio(server: RtimeAutomationMCP | None = None) -> int:
    server = server or RtimeAutomationMCP()
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
