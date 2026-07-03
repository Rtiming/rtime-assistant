# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only MCP stdio server for agent control-plane diagnostics."""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, Callable, Sequence

from .cli import (
    DEFAULT_PROFILE,
    DEFAULT_TOOLS,
    context_plan,
    doctor,
    render_mcp_config,
    runtime_snapshot,
    tooling_status,
    validation_plan,
)


PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "rtime-agent-control"
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


def _repo_root(arguments: JsonObject) -> Path:
    raw = _path_argument(arguments, "repo_root")
    if raw:
        return raw
    from .cli import find_repo_root

    return find_repo_root()


class RtimeAgentControlMCP:
    def tools(self) -> list[JsonObject]:
        repo_arg = {"repo_root": {"type": "string", "description": "Optional rtime-assistant repository root."}}
        return [
            {
                "name": "agent.doctor",
                "title": "Agent Control Doctor",
                "description": "Read-only health check of agent-control source, docs, plugin, and module metadata.",
                "inputSchema": _schema(repo_arg),
            },
            {
                "name": "agent.tooling",
                "title": "Agent Tooling Inventory",
                "description": "Summarize repository-owned package, skill, plugin, MCP, and test surfaces.",
                "inputSchema": _schema(repo_arg),
            },
            {
                "name": "agent.config_render",
                "title": "Agent MCP Config Render",
                "description": "Render standalone MCP client config JSON without writing files.",
                "inputSchema": _schema(
                    {
                        **repo_arg,
                        "profile": {"type": "string", "enum": ["mac", "orangepi"]},
                        "python": {"type": "string", "description": "Optional Python executable."},
                        "tools": {
                            "type": "array",
                            "items": {"type": "string"},
                            "description": "Optional tool names. Defaults to rtime-agent-control only.",
                        },
                        "all_tools": {
                            "type": "boolean",
                            "description": "Render all repository-owned MCP tools.",
                        },
                    }
                ),
            },
            {
                "name": "agent.validation_plan",
                "title": "Agent Validation Plan",
                "description": "Plan module validation checks without executing them.",
                "inputSchema": _schema(
                    {
                        **repo_arg,
                        "module": {"type": "string", "description": "module-submit id."},
                        "changed": {"type": "boolean", "description": "Plan checks for changed modules."},
                    }
                ),
            },
            {
                "name": "agent.context_plan",
                "title": "Agent Context Plan",
                "description": "Plan which context lanes an agent should inspect for a request.",
                "inputSchema": _schema(
                    {
                        **repo_arg,
                        "request": {"type": "string", "description": "User request text to classify."},
                    },
                    required=["request"],
                ),
            },
            {
                "name": "agent.runtime_snapshot",
                "title": "Agent Runtime Snapshot",
                "description": "Read-only snapshot of runtime source surfaces and optional run-log presence.",
                "inputSchema": _schema(
                    {
                        **repo_arg,
                        "run_log": {"type": "string", "description": "Optional runtime JSONL log path."},
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
                        "title": "Rtime Agent Control",
                        "version": SERVER_VERSION,
                        "description": "Read-only agent control-plane diagnostics for rtime-assistant.",
                    },
                    "instructions": (
                        "Use agent-control tools for planning, inventory, config rendering, and diagnostics only. "
                        "This server does not run arbitrary shell commands, write MCP config, deploy, restart "
                        "services, send messages, or expose secret values."
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
                return _response(request_id, _tool_result({"ok": False, "error": str(exc)}, is_error=True))

        if method == "shutdown":
            return _response(request_id, {})

        if has_id:
            return _error_response(request_id, -32601, f"Method not found: {method}")
        return None

    def call_tool(self, name: str, arguments: JsonObject) -> JsonObject:
        handlers: dict[str, Callable[[JsonObject], JsonObject]] = {
            "agent.doctor": self._tool_doctor,
            "agent.tooling": self._tool_tooling,
            "agent.config_render": self._tool_config_render,
            "agent.validation_plan": self._tool_validation_plan,
            "agent.context_plan": self._tool_context_plan,
            "agent.runtime_snapshot": self._tool_runtime_snapshot,
        }
        if name not in handlers:
            name = {_k.replace(".", "_"): _k for _k in handlers}.get(name, name)
        handler = handlers.get(name)
        if handler is None:
            raise ToolError(f"unknown tool: {name}")

        run_id = f"agent-control-mcp-{uuid.uuid4().hex}"
        started = time.monotonic()
        status = "ok"
        failure_reason = ""
        try:
            data = handler(arguments)
            if data.get("ok") is False:
                status = "failed"
                failure_reason = ",".join(str(item) for item in data.get("risks", []))[:300]
                if not failure_reason:
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
        return doctor(_path_argument(arguments, "repo_root"))

    def _tool_tooling(self, arguments: JsonObject) -> JsonObject:
        return tooling_status(_repo_root(arguments))

    def _tool_config_render(self, arguments: JsonObject) -> JsonObject:
        raw_tools = arguments.get("tools")
        if raw_tools is not None and not isinstance(raw_tools, list):
            raise ToolError("tools must be an array of tool names")
        tools = DEFAULT_TOOLS if bool(arguments.get("all_tools", False)) else raw_tools
        try:
            return render_mcp_config(
                _repo_root(arguments),
                profile=str(arguments.get("profile") or DEFAULT_PROFILE),
                python_bin=arguments.get("python"),
                tools=tools,
            )
        except ValueError as exc:
            raise ToolError(str(exc)) from exc

    def _tool_validation_plan(self, arguments: JsonObject) -> JsonObject:
        return validation_plan(
            _repo_root(arguments),
            module_id=str(arguments.get("module") or "agent-control"),
            changed=bool(arguments.get("changed", False)),
        )

    def _tool_context_plan(self, arguments: JsonObject) -> JsonObject:
        request = arguments.get("request")
        if not isinstance(request, str) or not request:
            raise ToolError("missing required argument: request")
        return context_plan(request, _repo_root(arguments))

    def _tool_runtime_snapshot(self, arguments: JsonObject) -> JsonObject:
        return runtime_snapshot(_repo_root(arguments), run_log=_path_argument(arguments, "run_log"))

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
        log_path_raw = os.environ.get("RTIME_AGENT_CONTROL_MCP_RUN_LOG")
        if not log_path_raw:
            return
        path = Path(log_path_raw).expanduser().resolve()
        path.parent.mkdir(parents=True, exist_ok=True)
        input_paths = [
            str(arguments[key])
            for key in ("repo_root", "run_log")
            if key in arguments and arguments[key]
        ]
        record: JsonObject = {
            "run_id": run_id,
            "entry": "mcp-stdio",
            "tool": tool,
            "permission_tier": "read_only",
            "input_paths": input_paths,
            "status": status,
            "duration_ms": duration_ms,
            "failure_reason": failure_reason,
        }
        request = arguments.get("request")
        if isinstance(request, str):
            record["request_length"] = len(request)
        tools = arguments.get("tools")
        if isinstance(tools, list):
            record["tool_count"] = len(tools)
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def serve_stdio(server: RtimeAgentControlMCP | None = None) -> int:
    server = server or RtimeAgentControlMCP()
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
