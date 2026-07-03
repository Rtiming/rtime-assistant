#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Memory card schema validator (docs/memory-loop.zh-CN.md section 2).

Usage:
  python3 memory_schema.py validate <file-or-dir> [...]
Stdout: one JSON object {ok, checked, errors, warnings}. Exit 0 ok, 1 errors.

Stdlib only. Frontmatter parser supports scalars, quoted strings and inline
lists — the subset the card schema uses.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

TYPES = {"memory-card", "hypothesis", "feedback"}
CONFIDENCE = {"user-stated", "source-backed", "inferred"}
LAYERS = {"trait", "situational"}
SENSITIVITY = {"normal", "sensitive"}
HYPOTHESIS_STATUS = {"testing", "confirmed", "rejected"}
DATE = re.compile(r"^\d{4}-\d{2}-\d{2}$")
DIRECTIVE = re.compile(r"永远|必须|一律|绝不")

REQUIRED_COMMON = ("type", "claim", "source", "observed_at")


def parse_frontmatter(text: str) -> tuple[dict, str | None]:
    """Return (frontmatter, error). Supports `k: v`, quoted v, inline [a, b]."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, "missing frontmatter block"
    fm: dict = {}
    closed = False
    for line in lines[1:]:
        if line.strip() == "---":
            closed = True
            break
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if ":" not in line:
            return fm, f"unparseable line: {line.strip()!r}"
        key, _, value = line.partition(":")
        key, value = key.strip(), value.strip()
        if value.startswith("[") and value.endswith("]"):
            inner = value[1:-1].strip()
            fm[key] = [v.strip().strip("'\"") for v in inner.split(",") if v.strip()] if inner else []
        elif value.startswith("{"):
            try:
                fm[key] = json.loads(value.replace("'", '"'))
            except json.JSONDecodeError:
                fm[key] = value
        else:
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
                value = value[1:-1]
            fm[key] = value
    if not closed:
        return fm, "frontmatter not closed with ---"
    return fm, None


def validate_card(path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one card file."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return [f"unreadable: {exc}"], []
    fm, parse_err = parse_frontmatter(text)
    if parse_err:
        return [parse_err], []

    card_type = fm.get("type")
    if card_type not in TYPES:
        errors.append(f"type must be one of {sorted(TYPES)}, got {card_type!r}")
        return errors, warnings

    for key in REQUIRED_COMMON:
        if not fm.get(key):
            errors.append(f"missing required field: {key}")
    if fm.get("observed_at") and not DATE.match(str(fm["observed_at"])):
        errors.append(f"observed_at must be YYYY-MM-DD, got {fm['observed_at']!r}")
    if fm.get("sensitivity", "normal") not in SENSITIVITY:
        errors.append(f"sensitivity must be one of {sorted(SENSITIVITY)}")
    if "supersedes" in fm and not isinstance(fm["supersedes"], list):
        errors.append("supersedes must be an inline list, e.g. [old-card.md]")

    if card_type == "memory-card":
        confidence = fm.get("confidence")
        if confidence not in CONFIDENCE:
            errors.append(f"confidence must be one of {sorted(CONFIDENCE)}")
        elif confidence == "inferred":
            errors.append("inferred claims must be hypothesis cards, not memory-card")
        layer = fm.get("layer")
        if layer not in LAYERS:
            errors.append(f"layer must be one of {sorted(LAYERS)}")
        elif layer == "situational":
            expires = fm.get("expires")
            if not expires:
                errors.append("situational card requires expires")
            elif not DATE.match(str(expires)):
                errors.append(f"expires must be YYYY-MM-DD, got {expires!r}")
        elif layer == "trait" and fm.get("expires"):
            errors.append("trait card must not set expires")
        if not fm.get("scope"):
            errors.append("memory-card requires scope")

    elif card_type == "hypothesis":
        if fm.get("status") not in HYPOTHESIS_STATUS:
            errors.append(f"hypothesis status must be one of {sorted(HYPOTHESIS_STATUS)}")
        confirmations = fm.get("confirmations")
        if confirmations is None or not str(confirmations).isdigit():
            errors.append("hypothesis requires integer confirmations")

    elif card_type == "feedback":
        body = text.split("---", 2)[-1] if text.count("---") >= 2 else ""
        if "下次" not in body and "how" not in body.lower():
            warnings.append("feedback card body should state how to apply next time")

    claim = str(fm.get("claim", ""))
    if DIRECTIVE.search(claim):
        warnings.append(
            "claim contains 永远/必须/一律/绝不 — behavior rules belong to CLAUDE.md "
            "via rtime-profile, not memory cards"
        )
    return errors, warnings


def main(argv: list[str]) -> int:
    if len(argv) < 3 or argv[1] != "validate":
        print(json.dumps({"ok": False, "errors": ["usage: memory_schema.py validate <path>..."]}))
        return 2
    files: list[Path] = []
    for arg in argv[2:]:
        p = Path(arg)
        if p.is_dir():
            files.extend(sorted(p.rglob("*.md")))
        elif p.exists():
            files.append(p)
        else:
            print(json.dumps({"ok": False, "errors": [f"not found: {arg}"]}))
            return 2
    all_errors: dict[str, list[str]] = {}
    all_warnings: dict[str, list[str]] = {}
    for f in files:
        if f.name == "README.md":
            continue
        errors, warnings = validate_card(f)
        if errors:
            all_errors[str(f)] = errors
        if warnings:
            all_warnings[str(f)] = warnings
    ok = not all_errors
    print(json.dumps(
        {"ok": ok, "checked": len(files), "errors": all_errors, "warnings": all_warnings},
        ensure_ascii=False,
    ))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv))
