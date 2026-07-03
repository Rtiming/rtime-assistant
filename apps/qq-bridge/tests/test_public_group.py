# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""公开答疑访问模式(学生会实例) + QQ_READ_ONLY 只读硬门接线。

Group access: any member of a QQ_PUBLIC_GROUPS group may ask, but only @bot
messages trigger; slash commands are ignored in groups; non-public groups and
stranger DMs stay rejected. Read-only door: QQ_READ_ONLY=1 must force
permission-mode=dontAsk and pass the closed read-only allow/disallow lists to the
runner.
"""

import asyncio

import qq_bridge.app as app_mod
from qq_bridge.app import build_model_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.protocol import IncomingMessage

BOT_ID = "479"
GROUP = "600"


def _run(coro):
    return asyncio.run(coro)


def _replies():
    out: list[str] = []

    async def reply(t):
        out.append(t)

    return out, reply


def _group_msg(user_id="222", group_id=GROUP, text="东区班车几点", mentions=None):
    return IncomingMessage(
        self_id=BOT_ID,
        message_type="group",
        user_id=user_id,
        group_id=group_id,
        chat_id=group_id,
        is_group=True,
        message_id="1",
        text=text,
        mentions=[BOT_ID] if mentions is None else mentions,
    )


def _private_msg(user_id="111", text="问题"):
    return IncomingMessage(
        self_id=BOT_ID,
        message_type="private",
        user_id=user_id,
        group_id=None,
        chat_id=user_id,
        is_group=False,
        message_id="1",
        text=text,
    )


def _cfg(tmp_path, **kw):
    kw.setdefault("owner_ids", frozenset({"111"}))
    kw.setdefault("public_groups", frozenset({GROUP}))
    kw.setdefault("group_allowlist", frozenset({GROUP}))
    return QQBridgeConfig(claude_cli="/x/claude", sessions_dir=str(tmp_path), **kw)


def _patch_runner(monkeypatch, answer="公开答案"):
    calls: list[dict] = []

    async def fake_run(prompt, **k):
        calls.append({"prompt": prompt, **k})
        return (answer, "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    return calls


# --- 公开群访问门 ---
def test_public_group_member_at_bot_gets_answer(monkeypatch, tmp_path):
    _patch_runner(monkeypatch)
    out, reply = _replies()
    _run(build_model_handler(_cfg(tmp_path))(_group_msg(user_id="222"), reply))
    assert any("公开答案" in o for o in out)  # 非 owner 群成员可问


def test_public_group_without_at_stays_silent(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    _run(build_model_handler(_cfg(tmp_path))(_group_msg(mentions=[]), reply))
    assert out == [] and calls == []  # 不 @bot 不响应(防刷屏)


def test_public_group_at_someone_else_stays_silent(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    _run(build_model_handler(_cfg(tmp_path))(_group_msg(mentions=["888"]), reply))
    assert out == [] and calls == []


def test_non_public_group_rejected(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    _run(build_model_handler(_cfg(tmp_path))(_group_msg(group_id="777"), reply))
    assert out == [] and calls == []


def test_group_rejected_when_public_groups_empty(monkeypatch, tmp_path):
    # 默认(未配置公开群):群消息一律拒 —— 现状行为不变。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, public_groups=frozenset())
    _run(build_model_handler(cfg)(_group_msg(user_id="111"), reply))  # 即使 owner
    assert out == [] and calls == []


def test_private_still_owner_only(monkeypatch, tmp_path):
    _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path))
    _run(handler(_private_msg(user_id="999"), reply))
    assert out == []  # 陌生人私聊仍拒
    _run(handler(_private_msg(user_id="111"), reply))
    assert any("公开答案" in o for o in out)  # owner 私聊照常


def test_group_basic_command_works_admin_command_refused(monkeypatch, tmp_path):
    # 命令分层(改版):群里 basic 命令(/new)对普通成员也生效;admin 命令(/model)
    # 非 admin 收到友好拒绝(不进模型)。owner=111,发消息的是普通成员 222。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path))
    _run(handler(_group_msg(user_id="222", text="/model opus"), reply))
    assert calls == [] and len(out) == 1 and "管理员" in out[0]  # admin 命令被拒
    out.clear()
    _run(handler(_group_msg(user_id="222", text="/new"), reply))
    assert calls == [] and out == ["🆕 已开始新对话"]  # basic 命令群里也能用


# --- QQ_READ_ONLY 只读硬门接线 ---
def test_readonly_forces_dontask_and_readonly_tool_lists(monkeypatch, tmp_path):
    monkeypatch.setenv("QQ_READ_ONLY", "1")
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, permission_mode="bypassPermissions")
    _run(build_model_handler(cfg)(_group_msg(text="宿舍报修流程是什么"), reply))
    assert len(calls) == 1
    run = calls[0]
    assert run["permission_mode"] == "dontAsk"  # 不信任 bypassPermissions
    disallowed = run["disallowed_tools"]
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent"):
        assert tool in disallowed
    assert "Bash" not in disallowed  # 裸 Bash 不进黑名单(deny 会压过 allow)
    allowed = run["allowed_tools"]
    assert "Read" in allowed and "Grep" in allowed
    assert "WebFetch" in allowed and "Bash(rtime-web-fetch *)" in allowed
    assert "mcp__rtime-library-gateway__*" in allowed


def test_readonly_off_keeps_session_permission_mode(monkeypatch, tmp_path):
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, permission_mode="bypassPermissions")
    _run(build_model_handler(cfg)(_private_msg(user_id="111"), reply))
    assert len(calls) == 1
    assert calls[0]["permission_mode"] == "bypassPermissions"  # 默认0:行为不变
    assert calls[0]["allowed_tools"] is None  # plain text 仍全工具
    assert "Write" not in calls[0]["disallowed_tools"]


# --- 用户分级(blocked > admin > allowed_users > 公开群成员) ---
def test_blocked_user_rejected_in_public_group(monkeypatch, tmp_path):
    # 黑名单优先级最高:公开群成员命中黑名单也一律拒(公开群防捣乱的关键)。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, blocked_users=frozenset({"222"}))
    _run(build_model_handler(cfg)(_group_msg(user_id="222"), reply))
    assert out == [] and calls == []


def test_blocked_beats_admin_in_private(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, blocked_users=frozenset({"111"}))  # admin 也被黑名单压过
    _run(build_model_handler(cfg)(_private_msg(user_id="111"), reply))
    assert out == [] and calls == []


def test_allowed_user_private_can_ask_but_no_commands(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path, allowed_users=frozenset({"555"})))
    _run(handler(_private_msg(user_id="555", text="宿舍报修流程"), reply))
    assert any("公开答案" in o for o in out)  # 白名单普通用户私聊可问
    out.clear()
    calls.clear()
    _run(handler(_private_msg(user_id="555", text="/model opus"), reply))
    assert calls == []  # 不落模型
    assert len(out) == 1 and "管理员" in out[0]  # 友好拒绝提示
    from qq_bridge.sessions import SessionStore

    assert SessionStore(str(tmp_path)).get("555", "555").model == ""  # 命令没生效


def test_admin_ids_env_grants_commands_beyond_owner(monkeypatch, tmp_path):
    # QQ_ADMIN_IDS 显式给出时以其为准:非 owner 的 admin 私聊命令正常。
    _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, admin_ids=frozenset({"333"}))
    _run(
        build_model_handler(cfg)(_private_msg(user_id="333", text="/model opus"), reply)
    )
    assert any("模型已设为" in o for o in out)
    from qq_bridge.sessions import SessionStore

    assert SessionStore(str(tmp_path)).get("333", "333").model.startswith("claude-opus")


def test_admin_defaults_to_owner_ids():
    from qq_bridge.config import QQBridgeConfig

    assert QQBridgeConfig(owner_ids=frozenset({"1"})).admin_ids == frozenset({"1"})
    assert QQBridgeConfig(
        owner_ids=frozenset({"1"}), admin_ids=frozenset({"2"})
    ).admin_ids == frozenset({"2"})


def test_normal_user_model_pinned_to_instance_default(monkeypatch, tmp_path):
    # 普通用户(公开群成员)固定用实例默认模型:session 里的历史 /model 选择不生效。
    from qq_bridge.sessions import SessionStore

    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, model="kimi-fixed")
    SessionStore(str(tmp_path)).set_model("222", GROUP, "claude-opus-4")  # 历史残留
    _run(build_model_handler(cfg)(_group_msg(user_id="222"), reply))
    assert len(calls) == 1 and calls[0]["model"] == "kimi-fixed"


def test_admin_model_choice_not_pinned(monkeypatch, tmp_path):
    from qq_bridge.sessions import SessionStore

    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, model="kimi-fixed")
    SessionStore(str(tmp_path)).set_model("111", "111", "claude-opus-4")
    _run(build_model_handler(cfg)(_private_msg(user_id="111"), reply))
    assert len(calls) == 1 and calls[0]["model"] == "claude-opus-4"  # admin 不受限


def test_group_autoleave_switch_off_keeps_bot_in_group():
    """QQ_GROUP_AUTOLEAVE=0 => notice handler never leaves, even non-allowlisted."""
    import asyncio
    from qq_bridge.app import build_notice_handler
    from qq_bridge.config import QQBridgeConfig

    cfg = QQBridgeConfig(owner_ids=frozenset({"1"}), group_allowlist=frozenset(), group_autoleave=False)
    handler = build_notice_handler(cfg)
    calls = []

    async def call_action(action, params):
        calls.append((action, params))

    ev = {"notice_type": "group_increase", "self_id": "9", "user_id": "9", "group_id": "12345"}
    asyncio.run(handler(ev, call_action))
    assert calls == []  # autoleave off => no set_group_leave


def test_group_autoleave_default_on_leaves_nonallowlisted():
    import asyncio
    from qq_bridge.app import build_notice_handler
    from qq_bridge.config import QQBridgeConfig

    cfg = QQBridgeConfig(owner_ids=frozenset({"1"}), group_allowlist=frozenset())
    handler = build_notice_handler(cfg)
    calls = []

    async def call_action(action, params):
        calls.append((action, params))

    ev = {"notice_type": "group_increase", "self_id": "9", "user_id": "9", "group_id": "12345"}
    asyncio.run(handler(ev, call_action))
    assert any(a == "set_group_leave" for a, _ in calls)  # default on => leaves
