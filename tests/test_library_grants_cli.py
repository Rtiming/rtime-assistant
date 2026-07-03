# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""J6 grant 台账管理 CLI(owner 审核视图 + add/revoke/gen-policy)。"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "packages" / "rtime-library-gateway" / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from rtime_library_gateway.grants_cli import GrantLedger, main  # noqa: E402


def _run(tmp_path, *argv) -> int:
    ledger = tmp_path / "grants.jsonl"
    return main(["--ledger", str(ledger), *argv])


def test_add_list_show_roundtrip(tmp_path, capsys):
    assert _run(tmp_path, "add", "--grant-id", "studentunion", "--subject", "stu",
                "--prefix", "knowledge/institutions/ustc") == 0
    capsys.readouterr()
    assert _run(tmp_path, "list") == 0
    view = json.loads(capsys.readouterr().out)
    assert view[0]["grant_id"] == "studentunion" and view[0]["active"] is True
    assert view[0]["read_prefixes"] == ["knowledge/institutions/ustc"]
    assert view[0]["contribute_prefixes"] == []


def test_add_duplicate_rejected(tmp_path, capsys):
    _run(tmp_path, "add", "--grant-id", "g", "--subject", "s", "--prefix", "knowledge/a")
    capsys.readouterr()
    assert _run(tmp_path, "add", "--grant-id", "g", "--subject", "s", "--prefix", "knowledge/a") == 1
    assert "已存在" in capsys.readouterr().out


def test_revoke_flips_active(tmp_path, capsys):
    _run(tmp_path, "add", "--grant-id", "g", "--subject", "s", "--prefix", "knowledge/a")
    capsys.readouterr()
    assert _run(tmp_path, "revoke", "g") == 0
    capsys.readouterr()
    _run(tmp_path, "list")
    view = json.loads(capsys.readouterr().out)
    assert view[0]["status"] == "revoked" and view[0]["active"] is False
    # 吊销不存在的 grant => 非零
    assert _run(tmp_path, "revoke", "nope") == 1


def test_contribute_flag(tmp_path, capsys):
    _run(tmp_path, "add", "--grant-id", "g", "--subject", "s",
         "--prefix", "knowledge/a", "--contribute")
    capsys.readouterr()
    _run(tmp_path, "list")
    view = json.loads(capsys.readouterr().out)
    assert view[0]["contribute_prefixes"] == ["knowledge/a"]


def test_gen_policy_matches_grant_to_policy(tmp_path, capsys):
    _run(tmp_path, "add", "--grant-id", "studentunion", "--subject", "stu",
         "--prefix", "knowledge/institutions/ustc")
    capsys.readouterr()
    assert _run(tmp_path, "gen-policy", "studentunion") == 0
    pol = json.loads(capsys.readouterr().out)
    assert pol["allowed_path_prefixes"] == ["knowledge/institutions/ustc"]
    assert pol["default_write"] == "deny"
    assert pol["_generated_from_grant"] == "studentunion"


def test_gen_policy_to_file(tmp_path, capsys):
    _run(tmp_path, "add", "--grant-id", "g", "--subject", "s", "--prefix", "knowledge/a")
    capsys.readouterr()
    out = tmp_path / "gen-policy.json"
    assert _run(tmp_path, "gen-policy", "g", "--out", str(out)) == 0
    assert out.is_file()
    pol = json.loads(out.read_text(encoding="utf-8"))
    assert pol["allowed_path_prefixes"] == ["knowledge/a"]


def test_ledger_atomic_save_no_tmp(tmp_path):
    ledger = GrantLedger(tmp_path / "grants.jsonl")
    from rtime_library_gateway.grants import Grant, GrantScope

    ledger.add(Grant("g", "s", (GrantScope("knowledge/a"),), granted_at="2026-07-04T00:00:00Z"))
    assert (tmp_path / "grants.jsonl").is_file()
    assert not list(tmp_path.glob(".*.tmp"))
