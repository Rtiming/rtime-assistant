# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""T5b security acceptance — the headline: a studentunion WEB session behaves like
the studentunion QQ session (design: "与QQ渠道行为一致是验收标准").

Driven END-TO-END through a live ThreadingHTTPServer with the model runner FAKED
(captures the exact runtime-call kwargs), the profiles compiled from the REAL git
``profiles/`` tree. Asserts:

  1. read-only HARD DOOR: permission_mode == dontAsk (READONLY_PERMISSION_MODE),
     disallowed ⊇ {Edit, Write, MultiEdit, NotebookEdit, Task, Agent}, bare Bash NOT
     denied, closed read-only allowlist (Read/Grep/… + the scoped gateway glob).
  2. LIBRARY SCOPE 8781: the runtime call's mcp_config points at the scoped 8781
     gateway (whose library-policy.json — generated from library.scope — confines
     reads to knowledge/institutions/ustc and denies personal-data/profile; the SAME
     data-door the QQ session hits).
  3. PERSONAL-DATA DENIED: a personal-data query in a read-only web session gets the
     closed allowlist (no personal-library unlock) — never the write/personal tools.
  4. /api/profiles NEVER leaks system_prompt (or mcp_config).
  5. env CANNOT DOWNGRADE a profile's read_only (fail-closed union — WEB_CHAT_READ_ONLY=0
     with a read_only:true profile keeps the door ON; do not reintroduce the env=0 bug).
"""

from __future__ import annotations

import json

from conftest import http_get, make_config, post_chat
from rtime_chat_runtime.tool_policy import (
    READONLY_ALLOWED,
    READONLY_DISALLOWED,
    READONLY_PERMISSION_MODE,
)


def _capturing_run(calls):
    async def fake_run(prompt, **kwargs):
        calls.append({"prompt": prompt, **kwargs})
        return "答案", "sid-1", False

    return fake_run


# 1 + 2: read-only hard door + scoped 8781 gateway reach the runtime call ---------
def test_studentunion_web_session_read_only_hard_door_and_8781_scope(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=_capturing_run(calls))
    status, events = post_chat(
        base, {"profile": "studentunion", "message": "宿舍报修流程？"}
    )
    assert status == 200
    call = calls[0]

    # (1) read-only hard door — identical to the QQ studentunion door.
    assert call["permission_mode"] == READONLY_PERMISSION_MODE
    for tool in READONLY_DISALLOWED:  # Edit/Write/MultiEdit/NotebookEdit/Task/Agent
        assert tool in call["disallowed_tools"], tool
    assert "Bash" not in call["disallowed_tools"]  # deny>allow would kill web-fetch
    assert call["allowed_tools"] == list(READONLY_ALLOWED)
    assert "mcp__rtime-library-gateway__*" in call["allowed_tools"]

    # (2) library scope 8781 — the runtime call reaches the scoped gateway.
    servers = json.loads(call["mcp_config"])["mcpServers"]
    assert "rtime-library-gateway" in servers
    assert "8781" in servers["rtime-library-gateway"]["url"]


# 3: a personal-data query in the read-only web session gets the closed allowlist --
def test_studentunion_web_personal_data_query_denied(live_server):
    calls: list[dict] = []
    base = live_server(fake_run=_capturing_run(calls))
    # a message that would trip the personal-library intent on a WRITABLE owner path.
    post_chat(
        base, {"profile": "studentunion", "message": "查一下我的个人档案和聊天记录"}
    )
    call = calls[0]
    # closed read-only allowlist: personal-library read unlock is NOT granted, no
    # write tools; only the read-only base set (same as QQ read-only). The gateway's
    # excluded_top_dirs=[personal-data, profile] is the enforcing data-door.
    assert call["allowed_tools"] == list(READONLY_ALLOWED)
    assert call["permission_mode"] == READONLY_PERMISSION_MODE
    # the personal-library HINT (owner-only unlock) is never appended in read-only.
    assert "owner 明确授权的单用户" not in call["prompt"]


# 4: /api/profiles never leaks system_prompt / mcp_config ------------------------
def test_api_profiles_never_leaks_system_prompt(live_server):
    base = live_server()
    status, _headers, body = http_get(base + "/api/profiles")
    assert status == 200
    payload = json.loads(body)
    assert payload["default"] == "owner"
    for entry in payload["profiles"]:
        assert set(entry) == {"id", "name", "description", "read_only"}
        assert "system_prompt" not in entry
        assert "mcp_config" not in entry
    # the studentunion entry is present and marked read-only.
    su = next(e for e in payload["profiles"] if e["id"] == "studentunion")
    assert su["read_only"] is True


# 5: env cannot DOWNGRADE a profile's read_only (fail-closed union) --------------
def test_env_zero_cannot_downgrade_studentunion_read_only(live_server, monkeypatch):
    """WEB_CHAT_READ_ONLY=0 + read_only:true profile -> door STAYS ON (no env=0 bug)."""
    monkeypatch.setenv("WEB_CHAT_READ_ONLY", "0")  # the fail-open trap
    calls: list[dict] = []
    base = live_server(fake_run=_capturing_run(calls))
    post_chat(base, {"profile": "studentunion", "message": "校历在哪查？"})
    call = calls[0]
    # env=0 is a no-op against the profile's read_only:true.
    assert call["permission_mode"] == READONLY_PERMISSION_MODE
    for tool in ("Edit", "Write", "Task"):
        assert tool in call["disallowed_tools"], tool


def test_env_one_forces_read_only_on_writable_owner(live_server, monkeypatch, tmp_path):
    """WEB_CHAT_READ_ONLY=1 forces the door ON even for the writable owner profile
    (env can only STRENGTHEN — belt-and-suspenders)."""
    monkeypatch.setenv("WEB_CHAT_READ_ONLY", "1")
    calls: list[dict] = []
    # a permissive session default that MUST be overridden by the forced door.
    cfg = make_config(tmp_path, permission_mode="bypassPermissions")
    base = live_server(cfg, fake_run=_capturing_run(calls))
    post_chat(base, {"profile": "owner", "message": "你好"})
    call = calls[0]
    assert call["permission_mode"] == READONLY_PERMISSION_MODE
    assert "Write" in call["disallowed_tools"]


def test_owner_web_session_not_read_only_by_default(live_server, tmp_path):
    """Sanity: without the env door, the owner web profile is writable (union off)."""
    calls: list[dict] = []
    cfg = make_config(tmp_path, permission_mode="bypassPermissions")
    base = live_server(cfg, fake_run=_capturing_run(calls))
    post_chat(base, {"profile": "owner", "message": "帮我改下笔记"})
    call = calls[0]
    assert call["permission_mode"] == "bypassPermissions"
    assert "Write" not in call["disallowed_tools"]
