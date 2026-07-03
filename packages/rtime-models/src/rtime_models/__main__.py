# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""CLI for the rtime model registry.

Read-only:
  python -m rtime_models gen-bash-defaults   # print deploy/bin/model-defaults.sh
  python -m rtime_models validate            # structural sanity check (exit 1 on error)
  python -m rtime_models dump                # pretty-print the parsed registry
  python -m rtime_models probe [--provider ID] [--timeout S] [--no-net]
                                             # provider readiness (secret set? endpoint alive?)

Editing (K2 — targets registry_path(), i.e. RTIME_MODEL_REGISTRY when set; every
edit validates the merged result first and writes atomically, see manage.py):
  python -m rtime_models add-provider FILE   # FILE holds one provider JSON object ('-' = stdin)
  python -m rtime_models remove-provider ID
  python -m rtime_models set-default MODEL_OR_ALIAS   # '' = wrapper default
"""
from __future__ import annotations

import json
import sys

from . import alias_map, load_registry, providers, registry_path, render_bash_defaults
from .manage import (
    add_provider,
    probe_registry,
    remove_provider,
    save_registry,
    set_default_model,
    validate_registry,
)


def _print_errors(errors: list[str]) -> None:
    for err in errors:
        print(f"error: {err}", file=sys.stderr)


def _save_and_report(reg: dict, verb: str) -> int:
    path = registry_path()
    save_registry(reg, path)
    load_registry(force_reload=True)
    print(f"{verb}: OK -> {path}")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    command = args[0] if args else "validate"

    if command == "gen-bash-defaults":
        sys.stdout.write(render_bash_defaults())
        return 0
    if command == "dump":
        print(json.dumps(load_registry(), ensure_ascii=False, indent=2))
        return 0
    if command == "validate":
        errors = validate_registry(load_registry())
        _print_errors(errors)
        if errors:
            print(f"registry: {len(errors)} error(s)", file=sys.stderr)
            return 1
        print(f"registry: OK ({len(providers())} providers, {len(alias_map())} aliases)")
        return 0

    if command == "probe":
        provider_id: str | None = None
        timeout = 3.0
        check_url = True
        rest = args[1:]
        while rest:
            flag = rest.pop(0)
            if flag == "--provider" and rest:
                provider_id = rest.pop(0)
            elif flag == "--timeout" and rest:
                timeout = float(rest.pop(0))
            elif flag == "--no-net":
                check_url = False
            else:
                print(f"unknown probe flag: {flag}", file=sys.stderr)
                return 2
        results = probe_registry(
            load_registry(), provider_id=provider_id, timeout=timeout, check_url=check_url
        )
        print(json.dumps({"results": results}, ensure_ascii=False, indent=2))
        if provider_id is not None and not results:
            print(f"error: no provider with id {provider_id!r}", file=sys.stderr)
            return 1
        return 0

    if command == "add-provider":
        if len(args) != 2:
            print("usage: add-provider FILE ('-' = stdin)", file=sys.stderr)
            return 2
        raw = sys.stdin.read() if args[1] == "-" else open(args[1], encoding="utf-8").read()
        try:
            provider_obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            print(f"error: provider JSON invalid: {exc}", file=sys.stderr)
            return 1
        merged, errors = add_provider(load_registry(force_reload=True), provider_obj)
        if merged is None:
            _print_errors(errors)
            return 1
        return _save_and_report(merged, f"add-provider {provider_obj.get('id')!r}")

    if command == "remove-provider":
        if len(args) != 2:
            print("usage: remove-provider ID", file=sys.stderr)
            return 2
        merged, errors = remove_provider(load_registry(force_reload=True), args[1])
        if merged is None:
            _print_errors(errors)
            return 1
        return _save_and_report(merged, f"remove-provider {args[1]!r}")

    if command == "set-default":
        if len(args) != 2:
            print("usage: set-default MODEL_OR_ALIAS (empty string = wrapper default)", file=sys.stderr)
            return 2
        merged, errors = set_default_model(load_registry(force_reload=True), args[1])
        if merged is None:
            _print_errors(errors)
            return 1
        return _save_and_report(merged, f"set-default {args[1]!r}")

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
