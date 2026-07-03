#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Read-only Better BibTeX JSON-RPC wrapper for Zotero.

The default backend talks to Better BibTeX's local JSON-RPC endpoint. Tests and
offline smoke checks can use ``--fixture``; that mode is still read-only and
does not touch Zotero.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_BBT_URL = os.environ.get("ZOTERO_BBT_URL", "http://127.0.0.1:23119/better-bibtex/json-rpc")
READ_ONLY_RPC_METHODS = frozenset(
    {
        "item.search",
        "item.attachments",
        "item.collections",
    }
)
WRITE_METHOD_DENYLIST = (
    "create",
    "update",
    "delete",
    "remove",
    "convert",
    "attachment.add",
    "attachments.add",
    "item.save",
    "collection.create",
)


class ZoteroUnavailable(RuntimeError):
    """Raised when Better BibTeX JSON-RPC is not reachable."""


def ensure_read_method(method: str) -> None:
    lowered = method.casefold()
    if method not in READ_ONLY_RPC_METHODS or any(word in lowered for word in WRITE_METHOD_DENYLIST):
        raise ValueError(f"Refusing non-read-only Zotero RPC method: {method}")


class ReadOnlyRpcClient:
    def __init__(
        self,
        endpoint: str = DEFAULT_BBT_URL,
        timeout: float = 5.0,
        methods_called: list[str] | None = None,
    ) -> None:
        self.endpoint = endpoint
        self.timeout = timeout
        self.methods_called = methods_called

    def call(self, method: str, params: Any) -> Any:
        ensure_read_method(method)
        if self.methods_called is None:
            self.methods_called = []
        self.methods_called.append(method)
        payload = {"jsonrpc": "2.0", "id": len(self.methods_called), "method": method, "params": params}
        req = urllib.request.Request(
            self.endpoint,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as resp:  # noqa: S310 - localhost only
                body = json.loads(resp.read().decode("utf-8"))
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise ZoteroUnavailable(
                "Zotero/Better BibTeX JSON-RPC is not reachable. Start Zotero with Better BibTeX enabled, "
                f"then retry. Endpoint: {self.endpoint}"
            ) from exc
        if "error" in body:
            raise RuntimeError(f"Zotero JSON-RPC error for {method}: {body['error']}")
        return body.get("result")


def load_fixture(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        data = json.load(fh)
    data.setdefault("items", [])
    data.setdefault("collections", {})
    return data


def normalize_item(item: dict[str, Any]) -> dict[str, Any]:
    attachments = item.get("attachments") or item.get("attachment_paths") or []
    if attachments and isinstance(attachments[0], str):
        attachments = [{"path": path, "linked": True} for path in attachments]
    return {
        "citekey": item.get("citekey") or item.get("citationKey") or item.get("citation-key") or item.get("key"),
        "zotero_key": item.get("zotero_key") or item.get("zoteroKey") or item.get("key"),
        "title": item.get("title"),
        "creators": item.get("creators") or item.get("authors") or item.get("author") or [],
        "year": item.get("year") or item.get("date") or item.get("issued"),
        "collections": item.get("collections") or [],
        "attachments": attachments,
        "zotero_uri": item.get("id"),
    }


def fixture_items(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [normalize_item(item) for item in data.get("items", [])]


def citekey_from_fixture(citekey: str, data: dict[str, Any]) -> dict[str, Any]:
    items = [item for item in fixture_items(data) if str(item.get("citekey") or "").casefold() == citekey.casefold()]
    return {"query": citekey, "backend": "fixture", "match_count": len(items), "items": items[:10]}


def search_from_fixture(query: str, data: dict[str, Any]) -> dict[str, Any]:
    needle = query.casefold()
    matches = [
        item
        for item in fixture_items(data)
        if needle in json.dumps(item, ensure_ascii=False).casefold()
    ]
    return {"query": query, "backend": "fixture", "match_count": len(matches), "items": matches[:25]}


def collection_from_fixture(name: str, data: dict[str, Any]) -> dict[str, Any]:
    requested = name.casefold()
    keys = [
        citekey
        for coll, citekeys in (data.get("collections") or {}).items()
        if requested in coll.casefold()
        for citekey in citekeys
    ]
    if not keys:
        keys = [
            item.get("citekey")
            for item in fixture_items(data)
            if any(requested in str(coll).casefold() for coll in item.get("collections") or [])
        ]
    items = [item for item in fixture_items(data) if item.get("citekey") in keys]
    return {"query": name, "backend": "fixture", "match_count": len(items), "items": items[:50]}


def citekey_live(citekey: str, client: ReadOnlyRpcClient) -> dict[str, Any]:
    # Better BibTeX exposes item.search as a read method. BBT versions differ in
    # accepted params, so the CLI reports raw normalized data instead of trying
    # to mutate or repair Zotero state.
    result = client.call("item.search", [citekey])
    items = normalize_live_items(result)
    exact = [item for item in items if str(item.get("citekey") or "").casefold() == citekey.casefold()]
    selected = exact or items[:1]
    for item in selected:
        item_citekey = item.get("citekey")
        if item_citekey:
            attachments = client.call("item.attachments", [item_citekey])
            item["attachments"] = normalize_attachments(attachments)
    return whitelist_proof(
        {"query": citekey, "backend": "bbt-json-rpc", "match_count": len(exact), "items": selected},
        client,
    )


def search_live(query: str, client: ReadOnlyRpcClient) -> dict[str, Any]:
    result = client.call("item.search", [query])
    return whitelist_proof(
        {"query": query, "backend": "bbt-json-rpc", "match_count": len(normalize_live_items(result)), "items": normalize_live_items(result)[:25]},
        client,
    )


def collection_live(name: str, client: ReadOnlyRpcClient, citekey: str | None = None, max_items: int = 2000) -> dict[str, Any]:
    if citekey:
        items = normalize_live_items(client.call("item.search", [citekey]))
    else:
        items = normalize_live_items(client.call("item.search", [""]))[:max_items]
    citekeys = [item["citekey"] for item in items if item.get("citekey")]
    collection_map: dict[str, list[dict[str, Any]]] = {}
    for start in range(0, len(citekeys), 200):
        chunk = citekeys[start : start + 200]
        if not chunk:
            continue
        collection_map.update(client.call("item.collections", [chunk]) or {})
    matched_items: list[dict[str, Any]] = []
    needle = name.casefold()
    for item in items:
        collections = collection_map.get(str(item.get("citekey")), [])
        item["collections"] = collections
        if any(needle in str(coll.get("name", "")).casefold() for coll in collections if isinstance(coll, dict)):
            matched_items.append(item)
    return whitelist_proof(
        {
            "query": name,
            "backend": "bbt-json-rpc",
            "scanned_item_count": len(items),
            "match_count": len(matched_items),
            "items": matched_items[:50],
        },
        client,
    )


def normalize_attachments(result: Any) -> list[dict[str, Any]]:
    if not isinstance(result, list):
        return []
    attachments = []
    for item in result:
        if isinstance(item, dict):
            attachments.append(
                {
                    "path": item.get("path"),
                    "open": item.get("open"),
                    "linked": bool(item.get("path")),
                }
            )
    return attachments


def normalize_live_items(result: Any) -> list[dict[str, Any]]:
    if result is None:
        return []
    if isinstance(result, dict) and "items" in result:
        result = result["items"]
    if isinstance(result, dict):
        result = [result]
    if not isinstance(result, list):
        return [{"raw": result}]
    return [normalize_item(item) if isinstance(item, dict) else {"raw": item} for item in result]


def whitelist_proof(payload: dict[str, Any], client: ReadOnlyRpcClient) -> dict[str, Any]:
    methods = list(client.methods_called or [])
    payload["rpc_methods_called"] = methods
    payload["read_only_whitelist"] = sorted(READ_ONLY_RPC_METHODS)
    payload["write_methods_called"] = [
        method
        for method in methods
        if method not in READ_ONLY_RPC_METHODS or any(word in method.casefold() for word in WRITE_METHOD_DENYLIST)
    ]
    payload["write_call_count"] = len(payload["write_methods_called"])
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--endpoint", default=DEFAULT_BBT_URL)
    parser.add_argument("--timeout", type=float, default=5.0)
    parser.add_argument("--fixture", type=Path, help="offline fixture JSON for tests/smoke")
    sub = parser.add_subparsers(dest="command", required=True)
    p_citekey = sub.add_parser("citekey", help="read metadata and linked attachment paths by citekey")
    p_citekey.add_argument("citekey")
    p_search = sub.add_parser("search", help="read-only Zotero/BBT search")
    p_search.add_argument("query")
    p_collection = sub.add_parser("collection", help="list read-only collection matches/items")
    p_collection.add_argument("name")
    p_collection.add_argument("--citekey", help="optional citekey to verify membership without scanning all items")
    p_collection.add_argument("--max-items", type=int, default=2000)
    return parser


def run_command(args: argparse.Namespace) -> dict[str, Any]:
    if args.fixture:
        data = load_fixture(args.fixture)
        if args.command == "citekey":
            return citekey_from_fixture(args.citekey, data)
        if args.command == "search":
            return search_from_fixture(args.query, data)
        if args.command == "collection":
            return collection_from_fixture(args.name, data)
    client = ReadOnlyRpcClient(endpoint=args.endpoint, timeout=args.timeout)
    if args.command == "citekey":
        return citekey_live(args.citekey, client)
    if args.command == "search":
        return search_live(args.query, client)
    if args.command == "collection":
        return collection_live(args.name, client, citekey=getattr(args, "citekey", None), max_items=getattr(args, "max_items", 2000))
    raise ValueError(args.command)


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        payload = run_command(args)
    except ZoteroUnavailable as exc:
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 69
    except Exception as exc:  # noqa: BLE001 - CLI should return structured failure
        print(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False, indent=2), file=sys.stderr)
        return 1
    payload.setdefault("ok", True)
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
