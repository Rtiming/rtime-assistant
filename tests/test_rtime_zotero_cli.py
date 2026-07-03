# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import argparse
import importlib.util
import json
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_zotero_module():
    spec = importlib.util.spec_from_file_location("rtime_zotero_cli", ROOT / "scripts" / "rtime-zotero.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def fixture(tmp_path: Path) -> Path:
    path = tmp_path / "zotero.json"
    path.write_text(
        json.dumps(
            {
                "items": [
                    {
                        "citekey": "run04-solid-state",
                        "zotero_key": "ABC123",
                        "title": "Solid State Physics Lecture",
                        "collections": ["run-04导入"],
                        "attachments": [
                            {
                                "path": "/brain/knowledge/courses/solid-state/lesson1.pdf",
                                "linked": True,
                            }
                        ],
                    }
                ],
                "collections": {"run-04导入": ["run04-solid-state"]},
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return path


def test_fixture_citekey_search_and_collection_are_read_only(tmp_path):
    mod = load_zotero_module()
    data_path = fixture(tmp_path)

    cite = mod.run_command(argparse.Namespace(command="citekey", citekey="run04-solid-state", fixture=data_path))
    search = mod.run_command(argparse.Namespace(command="search", query="Solid State", fixture=data_path))
    coll = mod.run_command(argparse.Namespace(command="collection", name="run-04", fixture=data_path))

    assert cite["match_count"] == 1
    assert cite["items"][0]["attachments"][0]["linked"] is True
    assert search["match_count"] == 1
    assert coll["match_count"] == 1


def test_refuses_non_read_rpc_method():
    mod = load_zotero_module()

    try:
        mod.ensure_read_method("item.save")
    except ValueError as exc:
        assert "Refusing" in str(exc)
    else:
        raise AssertionError("write method should be refused")


def test_live_payload_proves_whitelist_only():
    mod = load_zotero_module()
    client = mod.ReadOnlyRpcClient()
    client.methods_called = ["item.search"]

    payload = mod.whitelist_proof({"items": []}, client)

    assert payload["write_call_count"] == 0
    assert payload["rpc_methods_called"] == ["item.search"]
