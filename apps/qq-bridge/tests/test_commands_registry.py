# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""命令注册表(commands.py)的直接单元测试 — 解析 / tier 门 / dispatch 契约。

These exercise the declarative registry itself (not through the message handler),
so the modular seam is locked: parse_command, the tier ladder, and the dispatch
contract (unknown => not handled; admin cmd + non-admin => refusal, no handler call).
"""

import asyncio

from qq_bridge.commands import (
    COMMANDS,
    NON_ADMIN_COMMAND_REPLY,
    CommandContext,
    dispatch,
    parse_command,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeStore:
    def __init__(self):
        self.calls: list[tuple] = []
        self.model = ""
        self.stream = None

    def reset(self, u, c):
        self.calls.append(("reset", u, c))

    def set_model(self, u, c, m):
        self.calls.append(("set_model", u, c, m))
        self.model = m

    def set_stream(self, u, c, on):
        self.calls.append(("set_stream", u, c, on))

    def get(self, u, c):
        return self


def _dispatch(text, caller_tier, store=None):
    store = store or _FakeStore()
    out: list[str] = []

    async def reply(t):
        out.append(t)

    def ctx_factory(arg):
        return CommandContext(
            user_id="u",
            chat_id="c",
            arg=arg,
            reply=reply,
            store=store,
            default_stream=False,
        )

    handled = _run(
        dispatch(text, caller_tier=caller_tier, ctx_factory=ctx_factory, reply=reply)
    )
    return handled, out, store


# --- parse_command --------------------------------------------------------------
def test_parse_known_command_with_arg():
    assert parse_command("/model opus") == ("/model", "opus")


def test_parse_is_case_insensitive_on_name():
    assert parse_command("/MODEL Opus") == ("/model", "Opus")  # arg case preserved


def test_parse_bare_command_empty_arg():
    assert parse_command("/new") == ("/new", "")


def test_parse_unknown_command_is_none():
    assert parse_command("/foobar x") is None


def test_parse_prefix_not_matched_as_command():
    assert parse_command("/models") == ("/models", "")
    assert parse_command("/modelsx") is None


def test_parse_non_slash_is_none():
    assert parse_command("你好 /model") is None


# --- registry shape -------------------------------------------------------------
def test_registry_tiers_are_as_specified():
    assert {n: c.tier for n, c in COMMANDS.items()} == {
        "/new": "user",
        "/reset": "user",
        "/stream": "user",
        "/help": "user",
        "/model": "admin",
        "/models": "admin",
    }


# --- dispatch contract ----------------------------------------------------------
def test_dispatch_unknown_returns_not_handled():
    handled, out, _ = _dispatch("/nope", "admin")
    assert handled is False and out == []


def test_dispatch_basic_command_user_tier_runs():
    handled, out, store = _dispatch("/new", "user")
    assert handled is True and out == ["🆕 已开始新对话"]
    assert ("reset", "u", "c") in store.calls


def test_dispatch_admin_command_non_admin_refused_no_handler():
    store = _FakeStore()
    handled, out, store = _dispatch("/model opus", "user", store=store)
    assert handled is True  # handled = refusal sent, no run
    assert out == [NON_ADMIN_COMMAND_REPLY]
    assert store.calls == []  # handler never ran (model unchanged)


def test_dispatch_admin_command_admin_runs():
    handled, out, store = _dispatch("/model opus", "admin")
    assert handled is True
    assert any("模型已设为" in o for o in out)
    assert store.model.startswith("claude-opus")


def test_models_lists_numbered_choices_for_admin():
    handled, out, _ = _dispatch("/models", "admin")
    assert handled is True
    assert "可选模型" in out[0]
    assert "1." in out[0]
    assert "/model 1" in out[0]


def test_model_command_accepts_numbered_choice():
    handled, out, store = _dispatch("/model 1", "admin")
    assert handled is True
    assert any("模型已设为" in o for o in out)
    assert store.model == ""  # registry choice 1 is the wrapper default (kimi)


def test_model_reset_uses_instance_default():
    store = _FakeStore()
    out: list[str] = []

    async def reply(t):
        out.append(t)

    def ctx_factory(arg):
        return CommandContext(
            user_id="u",
            chat_id="c",
            arg=arg,
            reply=reply,
            store=store,
            default_stream=False,
            default_model="ds",
        )

    handled = _run(
        dispatch("/model reset", caller_tier="admin", ctx_factory=ctx_factory, reply=reply)
    )
    assert handled is True
    assert store.model == "ds"
    assert "实例默认" in out[0]


def test_help_scopes_to_caller_tier():
    _, out_user, _ = _dispatch("/help", "user")
    _, out_admin, _ = _dispatch("/help", "admin")
    assert "/model" not in out_user[0]  # user never sees admin cmd
    assert "/model" in out_admin[0]
    assert "/new" in out_user[0] and "/new" in out_admin[0]
