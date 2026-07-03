#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only stdio MCP-style wrapper for run-09 command surfaces.

This is intentionally small and local-only. It supports JSON-lines messages for
tests and simple MCP smoke checks:
  {"jsonrpc":"2.0","id":1,"method":"tools/list"}
  {"jsonrpc":"2.0","id":2,"method":"tools/call","params":{"name":"vault_list","arguments":{...}}}
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = Path(os.environ.get("RTIME_ASSISTANT_STATE_DIR", "~/.local/state/rtime-assistant")).expanduser()
AUDIT_LOG = Path(os.environ.get("RTIME_MCP_AUDIT_LOG", STATE_DIR / "rtime-tools-mcp-audit.jsonl"))


def load_script_module(filename: str, module_name: str):
    spec = importlib.util.spec_from_file_location(module_name, ROOT / "scripts" / filename)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load {filename}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


VAULT = load_script_module("rtime-vault.py", "rtime_vault_cli")
ZOTERO = load_script_module("rtime-zotero.py", "rtime_zotero_cli")


def audit(tool: str, arguments: dict[str, Any], status: str, error: str | None = None) -> None:
    AUDIT_LOG.parent.mkdir(parents=True, exist_ok=True)
    record = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "tool": tool,
        "status": status,
        "error": error,
        "argument_keys": sorted(arguments.keys()),
    }
    with AUDIT_LOG.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + "\n")


def tool_assistant_chat(args: dict[str, Any]) -> dict[str, Any]:
    message = str(args.get("message") or "")
    if args.get("dry_run", False):
        sys.path.insert(0, str(ROOT / "apps" / "assistant-gateway"))
        import rtime_chat  # type: ignore

        body = rtime_chat.build_body(
            message,
            pdf=args.get("pdf"),
            page=args.get("page"),
            note_path=args.get("note_path"),
            selection=args.get("selection"),
            task_mode=args.get("task_mode") or "ask",
            stream=bool(args.get("stream", False)),
            conversation_id=args.get("conversation_id"),
            history=args.get("history"),
        )
        return {"backend": "rtime_chat", "dry_run": True, "request_body": body}
    cmd = [sys.executable, str(ROOT / "apps" / "assistant-gateway" / "rtime_chat.py"), "--json"]
    if args.get("pdf"):
        cmd.extend(["--pdf", str(args["pdf"])])
    if args.get("page"):
        cmd.extend(["--page", str(args["page"])])
    if args.get("note_path"):
        cmd.extend(["--note", str(args["note_path"])])
    if args.get("task_mode"):
        cmd.extend(["--task", str(args["task_mode"])])
    if args.get("conversation_id"):
        cmd.extend(["--conversation", str(args["conversation_id"])])
    cmd.append(message)
    completed = subprocess.run(cmd, check=False, text=True, capture_output=True, timeout=float(args.get("timeout", 120)))
    return {"backend": "rtime_chat", "returncode": completed.returncode, "stdout": completed.stdout, "stderr": completed.stderr}


def tool_vault_resolve(args: dict[str, Any]) -> dict[str, Any]:
    return VAULT.resolve_pdf(
        str(args["query"]),
        Path(args.get("brain_root") or VAULT.DEFAULT_BRAIN_ROOT).expanduser().resolve(),
    )


def tool_vault_list(args: dict[str, Any]) -> dict[str, Any]:
    return VAULT.list_entries(
        str(args["presentation_dir"]),
        Path(args.get("vault_root") or VAULT.DEFAULT_VAULT_ROOT).expanduser().resolve(),
    )


def _zotero_args(command: str, args: dict[str, Any]) -> argparse.Namespace:
    ns = argparse.Namespace(
        command=command,
        endpoint=args.get("endpoint") or ZOTERO.DEFAULT_BBT_URL,
        timeout=float(args.get("timeout", 5)),
        fixture=Path(args["fixture"]).expanduser() if args.get("fixture") else None,
    )
    if command == "citekey":
        ns.citekey = str(args["citekey"])
    elif command == "search":
        ns.query = str(args["query"])
    return ns


def tool_zotero_citekey(args: dict[str, Any]) -> dict[str, Any]:
    return ZOTERO.run_command(_zotero_args("citekey", args))


def tool_zotero_search(args: dict[str, Any]) -> dict[str, Any]:
    return ZOTERO.run_command(_zotero_args("search", args))


TOOLS: dict[str, dict[str, Any]] = {
    "assistant_chat": {
        "description": "Call apps/assistant-gateway/rtime_chat.py; use dry_run for no-network tests.",
        "handler": tool_assistant_chat,
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string"}, "dry_run": {"type": "boolean"}},
            "required": ["message"],
        },
    },
    "vault_resolve": {
        "description": "Resolve a PDF title/basename through brain _indexes/pdf-manifest.jsonl.",
        "handler": tool_vault_resolve,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
    "vault_list": {
        "description": "List visible source entries in a vault presentation directory.",
        "handler": tool_vault_list,
        "inputSchema": {
            "type": "object",
            "properties": {"presentation_dir": {"type": "string"}},
            "required": ["presentation_dir"],
        },
    },
    "zotero_citekey": {
        "description": "Read Zotero metadata and linked attachment paths by citekey.",
        "handler": tool_zotero_citekey,
        "inputSchema": {"type": "object", "properties": {"citekey": {"type": "string"}}, "required": ["citekey"]},
    },
    "zotero_search": {
        "description": "Read-only Zotero/Better BibTeX search.",
        "handler": tool_zotero_search,
        "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}, "required": ["query"]},
    },
}


def list_tools() -> list[dict[str, Any]]:
    return [
        {"name": name, "description": meta["description"], "inputSchema": meta["inputSchema"]}
        for name, meta in TOOLS.items()
    ]


def call_tool(name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    if name not in TOOLS:
        raise KeyError(f"unknown tool: {name}")
    try:
        result = TOOLS[name]["handler"](arguments)
    except Exception as exc:  # noqa: BLE001 - surface as MCP tool error
        audit(name, arguments, "error", str(exc))
        raise
    audit(name, arguments, "ok")
    return result


def jsonrpc_response(request_id: Any, result: Any = None, error: str | None = None) -> dict[str, Any]:
    if error is not None:
        return {"jsonrpc": "2.0", "id": request_id, "error": {"code": -32000, "message": error}}
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def handle_message(message: dict[str, Any]) -> dict[str, Any] | None:
    method = message.get("method")
    request_id = message.get("id")
    try:
        if method == "initialize":
            return jsonrpc_response(
                request_id,
                {
                    "protocolVersion": "2024-11-05",
                    "serverInfo": {"name": "rtime-tools", "version": "0.1.0"},
                    "capabilities": {"tools": {}},
                },
            )
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return jsonrpc_response(request_id, {"tools": list_tools()})
        if method == "tools/call":
            params = message.get("params") or {}
            name = params.get("name")
            arguments = params.get("arguments") or {}
            result = call_tool(str(name), arguments)
            return jsonrpc_response(
                request_id,
                {"content": [{"type": "text", "text": json.dumps(result, ensure_ascii=False)}], "isError": False},
            )
        return jsonrpc_response(request_id, error=f"unsupported method: {method}")
    except Exception as exc:  # noqa: BLE001 - MCP error boundary
        return jsonrpc_response(request_id, error=str(exc))


def serve_json_lines() -> int:
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            response = handle_message(msg)
        except json.JSONDecodeError as exc:
            response = jsonrpc_response(None, error=str(exc))
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), flush=True)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--list-tools", action="store_true", help="print tools JSON and exit")
    parser.add_argument("--call", help="call one tool once and exit")
    parser.add_argument("--arguments", default="{}", help="JSON arguments for --call")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.list_tools:
        print(json.dumps({"tools": list_tools()}, ensure_ascii=False, indent=2))
        return 0
    if args.call:
        result = call_tool(args.call, json.loads(args.arguments))
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    return serve_json_lines()


if __name__ == "__main__":
    raise SystemExit(main())
