# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Command-line surface for the rtime library gateway.

``doctor`` is an in-process self-check (resolved roots, policy load, which
underlying CLIs import) that never reads the brain. ``policy-show`` prints the
active policy. ``call`` runs one gateway method end-to-end (gate -> dispatch ->
redact -> audit) for manual use.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
from typing import Any, Sequence

from . import dispatch as dispatch_mod
from . import gate as gate_mod

JsonObject = dict[str, Any]

# Module name -> package src dir name, used only to report importability without
# importing (so doctor stays side-effect free and brain-free).
_UNDERLYING_MODULES = {
    "brain_library.cli": "brain-library",
    "brain_docpack.cli": "brain-docpack",
    "brain_citation.cli": "brain-citation",
    "rtime_hub_connector.cli": "rtime-hub-connector",
    "rtime_context.cli": "rtime-context",
    "rtime_profile.cli": "rtime-profile",
    "rtime_review.cli": "rtime-review",
    "rtime_automation.cli": "rtime-automation",
    "rtime_assistant_runtime.cli": "rtime-assistant-runtime",
}


def _json_print(data: JsonObject) -> None:
    print(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True))


def _module_importable(module: str, package: str) -> bool:
    src = dispatch_mod.repo_root() / "packages" / package / "src"
    if (src / module.split(".")[0]).is_dir():
        return (src / module.replace(".", "/")).with_suffix(".py").is_file()
    try:
        return importlib.util.find_spec(module) is not None
    except (ImportError, ValueError, ModuleNotFoundError):
        return False


def doctor() -> JsonObject:
    policy_error = ""
    try:
        policy = gate_mod.load_policy()
    except Exception as exc:  # pragma: no cover - defensive
        policy = {}
        policy_error = str(exc)
    methods = policy.get("methods", {}) if isinstance(policy, dict) else {}
    cli_status = {
        module: _module_importable(module, package)
        for module, package in _UNDERLYING_MODULES.items()
    }
    write_executables = {
        name: (dispatch_mod.repo_root() / "deploy" / "bin" / name).is_file()
        for name in dispatch_mod.WRITE_EXECUTABLES
    }
    disjoint = dispatch_mod.READ_DISPATCH.keys().isdisjoint(dispatch_mod.WRITE_DISPATCH.keys())
    risks: list[str] = []
    if policy_error:
        risks.append("policy_load_failed")
    if not disjoint:
        risks.append("dispatch_tables_overlap")
    return {
        "ok": not risks,
        "server": "rtime-library-gateway",
        "roots": {
            "repo_root": str(dispatch_mod.repo_root()),
            "brain_root": str(dispatch_mod.brain_root()),
            "hub_root": str(dispatch_mod.hub_root()),
            "reminders_path": str(dispatch_mod.reminders_path()),
        },
        "policy": {
            "loaded": not policy_error,
            "error": policy_error,
            "method_count": len(methods) if isinstance(methods, dict) else 0,
            "default_read": policy.get("default_read") if isinstance(policy, dict) else None,
            "default_write": policy.get("default_write") if isinstance(policy, dict) else None,
            "redact_sensitive": policy.get("redact_sensitive") if isinstance(policy, dict) else None,
            "allowed_path_prefixes": (
                gate_mod._allowed_path_prefixes(policy) if isinstance(policy, dict) else []
            ),
        },
        "dispatch": {
            "read_methods": sorted(dispatch_mod.READ_DISPATCH),
            "write_methods": sorted(dispatch_mod.WRITE_DISPATCH),
            "tables_disjoint": disjoint,
            "write_executables": write_executables,
        },
        "underlying_clis": cli_status,
        "privacy": {
            "brain_read": False,
            "argument_bodies_logged": False,
        },
        "risks": risks,
    }


def policy_show() -> JsonObject:
    try:
        policy = gate_mod.load_policy()
    except Exception as exc:  # pragma: no cover - defensive
        return {"ok": False, "error": str(exc)}
    return {"ok": True, "policy": policy}


def call_method(method: str, arguments: JsonObject, client_id: str) -> JsonObject:
    from .mcp_server import RtimeLibraryGatewayMCP, ToolError

    server = RtimeLibraryGatewayMCP()
    try:
        return server.invoke(method, arguments, client_id=client_id)
    except ToolError as exc:
        return {"ok": False, "method": method, "error": str(exc)}


def _parse_args_json(raw: str | None) -> JsonObject:
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise SystemExit(f"--args-json must be valid JSON: {exc.msg}")
    if not isinstance(data, dict):
        raise SystemExit("--args-json must be a JSON object")
    return data


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rtime-library-gateway",
        description="Unified library access gateway: gate + audit over rtime read tools and the three narrow-write settings tools.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("doctor", help="in-process self-check (roots, policy, CLI importability); never reads brain")
    sub.add_parser("policy-show", help="print the active policy")

    call_parser = sub.add_parser("call", help="run one gateway method end-to-end")
    call_parser.add_argument("method", help="lib.* method name")
    call_parser.add_argument("--args-json", default="", help="JSON object of method arguments")
    call_parser.add_argument("--client", default="cli", help="client id for the gate")

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "doctor":
        data = doctor()
    elif args.command == "policy-show":
        data = policy_show()
    elif args.command == "call":
        data = call_method(args.method, _parse_args_json(args.args_json), args.client)
    else:  # pragma: no cover - argparse enforces valid commands
        raise AssertionError(args.command)
    _json_print(data)
    return 0 if data.get("ok", False) else 1


if __name__ == "__main__":
    raise SystemExit(main())
