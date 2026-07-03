# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Real profile loader (T5b) + override path — the /api/profiles source contract.

``load_profiles()`` has two paths:
  1. the git ``profiles/`` tree (default; ``RTIME_PROFILES_ROOT`` -> repo profiles),
     compiled by the shared loader and filtered to ``channels.web`` declarers;
  2. an ad-hoc ``RTIME_WEB_CHAT_PROFILES`` JSON override (inline or a path).

Both project to the behavior shape (id/name/description/system_prompt/read_only/
mcp_config/render); ``public_view`` exposes only the 4 PUBLIC keys.
"""

from __future__ import annotations

import json

import pytest
from web_chat.profiles import (
    _BEHAVIOR_KEYS,
    PUBLIC_KEYS,
    get_profile,
    load_profiles,
    public_view,
)

_SHAPE = set(_BEHAVIOR_KEYS)


# --- real profiles/ tree -------------------------------------------------------
def test_loads_web_enabled_profiles_from_tree():
    """The repo profiles/ tree yields exactly the channels.web declarers (owner, su)."""
    profiles = load_profiles()
    assert [p["id"] for p in profiles] == [
        "owner",
        "studentunion",
    ]  # sorted, owner default
    for p in profiles:
        assert set(p) == _SHAPE
        assert p["system_prompt"]


def test_studentunion_is_read_only_scoped_gateway():
    """studentunion web profile: read-only + the scoped 8781 gateway (like QQ)."""
    su = get_profile(load_profiles(), "studentunion")
    assert su["read_only"] is True
    assert su["render"] == "markdown"
    # the web session reaches the scoped gateway (8781); scope/personal-data denial
    # is enforced by that gateway's library-policy.json (generated from library.scope).
    assert "8781" in su["mcp_config"]
    assert "rtime-library-gateway" in su["mcp_config"]


def test_owner_is_writable_no_mcp():
    owner = get_profile(load_profiles(), "owner")
    assert owner["read_only"] is False
    # owner web has no gateway (gateway-only consumer; no /mnt/brain on web) -> empty.
    assert json.loads(owner["mcp_config"]) == {"mcpServers": {}}


def test_web_prompt_differs_from_qq_plaintext():
    """The web system prompt allows markdown/LaTeX (not the QQ plain-text prompt)."""
    su = get_profile(load_profiles(), "studentunion")
    assert "Markdown" in su["system_prompt"] or "LaTeX" in su["system_prompt"]


def test_missing_tree_fails_fast(monkeypatch, tmp_path):
    monkeypatch.setenv("RTIME_PROFILES_ROOT", str(tmp_path / "nope"))
    with pytest.raises(ValueError):
        load_profiles()


def test_tree_without_web_profiles_fails_fast(monkeypatch, tmp_path):
    """A profiles tree with a profile that has NO channels.web -> empty -> fail fast."""
    root = tmp_path / "profiles"
    (root / "_base" / "prompts").mkdir(parents=True)
    (root / "_base" / "prompts" / "qq-system.md").write_text("b\n", encoding="utf-8")
    (root / "_base" / "qq.yaml").write_text(
        "schema_version: 1\nprofile:\n  id: _base-qq\n", encoding="utf-8"
    )
    pdir = root / "qqonly"
    (pdir / "prompts").mkdir(parents=True)
    (pdir / "prompts" / "system.md").write_text("x\n", encoding="utf-8")
    (pdir / "profile.yaml").write_text(
        "schema_version: 1\n"
        "profile:\n  id: qqonly\n  extends: _base/qq\n"
        "identity:\n  system_prompt_file: prompts/system.md\n"
        "channels:\n  qq:\n    public_groups: ['1']\n    group_allowlist: ['1']\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_PROFILES_ROOT", str(root))
    with pytest.raises(ValueError):
        load_profiles()


# --- override path -------------------------------------------------------------
def test_env_inline_json_override(monkeypatch):
    monkeypatch.setenv(
        "RTIME_WEB_CHAT_PROFILES",
        json.dumps([{"id": "kb", "name": "知识库", "read_only": True}]),
    )
    profiles = load_profiles()
    assert len(profiles) == 1
    assert profiles[0]["id"] == "kb"
    assert profiles[0]["read_only"] is True
    assert profiles[0]["system_prompt"]  # default filled in
    assert profiles[0]["render"] == "markdown"


def test_env_path_override(monkeypatch, tmp_path):
    path = tmp_path / "profiles.json"
    path.write_text(json.dumps([{"id": "solo"}]), encoding="utf-8")
    monkeypatch.setenv("RTIME_WEB_CHAT_PROFILES", str(path))
    profiles = load_profiles()
    assert profiles[0]["id"] == "solo"
    assert profiles[0]["name"] == "solo"  # name falls back to id
    assert profiles[0]["read_only"] is False
    assert profiles[0]["mcp_config"] is None  # no gateway -> process default used


def test_invalid_json_fails_fast(monkeypatch):
    monkeypatch.setenv("RTIME_WEB_CHAT_PROFILES", "[not json")
    with pytest.raises(ValueError):
        load_profiles()


def test_empty_array_fails_fast(monkeypatch):
    monkeypatch.setenv("RTIME_WEB_CHAT_PROFILES", "[]")
    with pytest.raises(ValueError):
        load_profiles()


def test_entry_without_id_fails_fast(monkeypatch):
    monkeypatch.setenv("RTIME_WEB_CHAT_PROFILES", json.dumps([{"name": "no id"}]))
    with pytest.raises(ValueError):
        load_profiles()


def test_duplicate_ids_fail_fast(monkeypatch):
    monkeypatch.setenv(
        "RTIME_WEB_CHAT_PROFILES", json.dumps([{"id": "a"}, {"id": "a"}])
    )
    with pytest.raises(ValueError):
        load_profiles()


# --- shared helpers ------------------------------------------------------------
def test_get_profile():
    profiles = load_profiles()
    assert get_profile(profiles, "owner")["id"] == "owner"
    assert get_profile(profiles, "ghost") is None


def test_public_view_hides_prompt_and_mcp():
    for p in public_view(load_profiles()):
        assert set(p) == set(PUBLIC_KEYS)
        assert "system_prompt" not in p
        assert "mcp_config" not in p
