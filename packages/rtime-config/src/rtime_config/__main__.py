# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""CLI: render a settings model's schema to Markdown.

    python -m rtime_config <module:Model> [--title T] [--out PATH]

Imports ``module``, pulls ``Model``, and prints (or writes) the Markdown table.
Component packages call this from a test to keep docs/config/<name>.md in lockstep
with the model; a golden test then fails if either drifts without review.
"""

from __future__ import annotations

import argparse
import importlib
import sys
from pathlib import Path

from .schema_doc import schema_to_markdown


def render(target: str, *, title: str | None) -> str:
    module_name, _, attr = target.partition(":")
    if not attr:
        raise SystemExit(f"expected <module:Model>, got {target!r}")
    model = getattr(importlib.import_module(module_name), attr)
    env_prefix = ""
    try:
        env_prefix = model.model_config.get("env_prefix", "") or ""
    except Exception:  # pragma: no cover - defensive
        pass
    schema = model.model_json_schema(by_alias=False)
    return schema_to_markdown(schema, title=title or attr, env_prefix=env_prefix)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="rtime-config-doc")
    parser.add_argument("target", help="import path as module:Model")
    parser.add_argument("--title", default=None, help="document H1 title")
    parser.add_argument("--out", default=None, help="write here instead of stdout")
    args = parser.parse_args(argv)
    md = render(args.target, title=args.title)
    if args.out:
        Path(args.out).write_text(md, encoding="utf-8")
    else:
        sys.stdout.write(md)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
