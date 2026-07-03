#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Validate the conservative DocPack contract.

The validator is intentionally dependency-free. It supports the JSON Schema
subset used by this repository and then applies DocPack-specific cross checks.
It never writes to the DocPack directory.
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCHEMA_DIR = ROOT / "schemas" / "docpack"


def _type_name(value: Any) -> str:
    if isinstance(value, bool):
        return "boolean"
    if isinstance(value, dict):
        return "object"
    if isinstance(value, list):
        return "array"
    if isinstance(value, int):
        return "integer"
    if isinstance(value, float):
        return "number"
    if isinstance(value, str):
        return "string"
    if value is None:
        return "null"
    return type(value).__name__


def _matches_type(value: Any, expected: str) -> bool:
    actual = _type_name(value)
    if expected == "number":
        return actual in {"integer", "number"}
    return actual == expected


def _schema_errors(value: Any, schema: dict[str, Any], path: str) -> list[str]:
    errors: list[str] = []

    expected_type = schema.get("type")
    if expected_type is not None:
        types = expected_type if isinstance(expected_type, list) else [expected_type]
        if not any(_matches_type(value, item) for item in types):
            return [f"{path}: expected {expected_type}, got {_type_name(value)}"]

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}, got {value!r}")

    if isinstance(value, str):
        min_length = schema.get("minLength")
        if min_length is not None and len(value) < min_length:
            errors.append(f"{path}: string is shorter than {min_length}")
        pattern = schema.get("pattern")
        if pattern and not re.search(pattern, value):
            errors.append(f"{path}: value does not match pattern {pattern!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        if minimum is not None and value < minimum:
            errors.append(f"{path}: value is less than {minimum}")

    if isinstance(value, dict):
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                errors.append(f"{path}.{key}: missing required field")
        properties = schema.get("properties", {})
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], f"{path}.{key}"))

    if isinstance(value, list) and "items" in schema:
        for index, item in enumerate(value):
            errors.extend(_schema_errors(item, schema["items"], f"{path}[{index}]"))

    return errors


def _load_json(path: Path) -> Any:
    with path.open(encoding="utf-8") as handle:
        return json.load(handle)


def _load_schema(name: str) -> dict[str, Any]:
    return _load_json(SCHEMA_DIR / name)


def _relative_path(docpack: Path, raw: str) -> Path:
    path = Path(raw)
    if path.is_absolute():
        return path
    return docpack / path


def _read_chunks(path: Path) -> tuple[list[dict[str, Any]], list[str]]:
    chunks: list[dict[str, Any]] = []
    errors: list[str] = []
    if not path.exists():
        return chunks, [f"{path}: missing chunks JSONL"]
    with path.open(encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            text = line.strip()
            if not text:
                continue
            try:
                record = json.loads(text)
            except json.JSONDecodeError as exc:
                errors.append(f"{path}:{line_no}: invalid JSON: {exc.msg}")
                continue
            if not isinstance(record, dict):
                errors.append(f"{path}:{line_no}: chunk record must be an object")
                continue
            chunks.append(record)
    return chunks, errors


def validate_docpack(docpack: Path) -> list[str]:
    errors: list[str] = []
    if not docpack.exists():
        return [f"{docpack}: directory does not exist"]
    if not docpack.is_dir():
        return [f"{docpack}: not a directory"]

    required_files = {
        "manifest": docpack / "manifest.json",
        "layout": docpack / "layout.json",
        "verify": docpack / "verify.json",
        "citations": docpack / "citations.json",
        "chunks": docpack / "chunks.jsonl",
    }
    for name, path in required_files.items():
        if not path.exists():
            errors.append(f"{name}: missing {path.name}")
    if errors:
        return errors

    try:
        manifest = _load_json(required_files["manifest"])
        layout = _load_json(required_files["layout"])
        verify = _load_json(required_files["verify"])
        citations = _load_json(required_files["citations"])
    except json.JSONDecodeError as exc:
        return [f"{exc.doc}: invalid JSON: {exc.msg}"]

    errors.extend(_schema_errors(manifest, _load_schema("manifest.schema.json"), "manifest"))
    errors.extend(_schema_errors(layout, _load_schema("layout.schema.json"), "layout"))
    errors.extend(_schema_errors(verify, _load_schema("verify.schema.json"), "verify"))
    errors.extend(_schema_errors(citations, _load_schema("citations.schema.json"), "citations"))

    chunks, chunk_read_errors = _read_chunks(required_files["chunks"])
    errors.extend(chunk_read_errors)
    chunk_schema = _load_schema("chunk.schema.json")
    for index, chunk in enumerate(chunks):
        errors.extend(_schema_errors(chunk, chunk_schema, f"chunks[{index}]"))

    if errors:
        return errors

    output_paths = manifest["outputs"]
    for key in ("content_md", "layout_json", "verify_json", "citations_json", "chunks_jsonl"):
        expected_path = _relative_path(docpack, output_paths[key])
        if not expected_path.exists():
            errors.append(f"manifest.outputs.{key}: missing file {output_paths[key]}")

    source_path = _relative_path(docpack, manifest["source"]["path"])
    if not source_path.exists():
        errors.append(f"manifest.source.path: missing source file {manifest['source']['path']}")

    if manifest["outputs"]["layout_json"] != "layout.json":
        errors.append("manifest.outputs.layout_json: expected layout.json for v1")
    if manifest["outputs"]["verify_json"] != "verify.json":
        errors.append("manifest.outputs.verify_json: expected verify.json for v1")
    if manifest["outputs"]["citations_json"] != "citations.json":
        errors.append("manifest.outputs.citations_json: expected citations.json for v1")
    if manifest["outputs"]["chunks_jsonl"] != "chunks.jsonl":
        errors.append("manifest.outputs.chunks_jsonl: expected chunks.jsonl for v1")

    if manifest["source"]["sha256"].lower() != verify["source_sha256"].lower():
        errors.append("verify.source_sha256: does not match manifest.source.sha256")

    pages = verify.get("pages", [])
    page_numbers = {page["page"] for page in pages}
    layout_page_numbers = {page["page"] for page in layout.get("pages", [])}
    page_count = manifest["display"]["page_count"]
    if page_count != len(pages):
        errors.append(f"manifest.display.page_count: expected {len(pages)}, got {page_count}")
    if layout_page_numbers != page_numbers:
        errors.append("layout.pages: page set does not match verify.pages")

    pages_dir = manifest["display"]["pages_dir"]
    for page in pages:
        image = page.get("image") or f"{pages_dir}/page-{page['page']:04d}.png"
        if page["render_status"] == "ok" and not _relative_path(docpack, image).exists():
            errors.append(f"verify.pages[{page['page']}].image: missing rendered page {image}")

    block_ids: set[str] = set()
    for layout_page in layout.get("pages", []):
        for block in layout_page.get("blocks", []):
            block_id = block["block_id"]
            if block_id in block_ids:
                errors.append(f"layout.blocks: duplicate block_id {block_id}")
            block_ids.add(block_id)
            if block["page"] != layout_page["page"]:
                errors.append(f"layout.blocks.{block_id}: block page does not match containing page")
            if block["page"] not in page_numbers:
                errors.append(f"layout.blocks.{block_id}: unknown page {block['page']}")
            asset_path = block.get("asset_path")
            if asset_path and not _relative_path(docpack, asset_path).exists():
                errors.append(f"layout.blocks.{block_id}: missing asset {asset_path}")

    anchor_ids: set[str] = set()
    for anchor in citations.get("anchors", []):
        anchor_id = anchor["anchor_id"]
        if anchor_id in anchor_ids:
            errors.append(f"citations.anchors: duplicate anchor_id {anchor_id}")
        anchor_ids.add(anchor_id)
        page = anchor.get("page")
        if page is not None and page not in page_numbers:
            errors.append(f"citations.anchors.{anchor_id}: unknown page {page}")

    chunk_ids: set[str] = set()
    for chunk in chunks:
        chunk_id = chunk["chunk_id"]
        if chunk_id in chunk_ids:
            errors.append(f"chunks: duplicate chunk_id {chunk_id}")
        chunk_ids.add(chunk_id)

        page_start = chunk.get("page_start")
        page_end = chunk.get("page_end", page_start)
        if page_start is not None:
            if page_start not in page_numbers:
                errors.append(f"chunks.{chunk_id}: unknown page_start {page_start}")
            if page_end is not None and page_end < page_start:
                errors.append(f"chunks.{chunk_id}: page_end is before page_start")
        for anchor_id in chunk.get("citations", []):
            if anchor_id not in anchor_ids:
                errors.append(f"chunks.{chunk_id}: unknown citation anchor {anchor_id}")

    return errors


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Validate a DocPack directory.")
    parser.add_argument("docpack", type=Path, help="Path to <slug>.docpack")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable result")
    args = parser.parse_args(argv)

    docpack = args.docpack.resolve()
    errors = validate_docpack(docpack)
    result = {
        "docpack": str(docpack),
        "ok": not errors,
        "errors": errors,
    }

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))
    elif errors:
        for error in errors:
            print(f"error: {error}", file=sys.stderr)
    else:
        print(f"DocPack ok: {docpack}")

    return 0 if not errors else 1


if __name__ == "__main__":
    raise SystemExit(main())
