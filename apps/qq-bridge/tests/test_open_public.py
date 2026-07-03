# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""开放答疑模式(open_public) + 命令分层(basic/admin)访问控制测试。

Change 1 — open_public: 任何群里任何非黑名单成员 @bot 都能提问(不再要求群在
public_groups 白名单里);blocked 仍一律拒;admin 仍是 admin;私聊由 private_access 单独
控制;read_only/库 scope 不被削弱(仍每次运行强制)。

Change 2 — 命令分层:basic 命令(/new /reset /stream /help)对所有可服务用户(私聊+群)
生效;admin 命令(/model)仅 admin,非 admin 收到友好拒绝(不落模型);未知 /foo 落回问答。
"""

import asyncio

import qq_bridge.app as app_mod
from qq_bridge.app import build_model_handler, build_notice_handler
from qq_bridge.config import QQBridgeConfig
from qq_bridge.onebot.protocol import IncomingMessage
from qq_bridge.sessions import SessionStore

BOT_ID = "479"


def _run(coro):
    return asyncio.run(coro)


def _replies():
    out: list[str] = []

    async def reply(t):
        out.append(t)

    return out, reply


def _group_msg(user_id="222", group_id="900", text="东区班车几点", mentions=None):
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


def _private_msg(user_id="111", text="问题", sub_type=""):
    return IncomingMessage(
        self_id=BOT_ID,
        message_type="private",
        user_id=user_id,
        group_id=None,
        chat_id=user_id,
        is_group=False,
        message_id="1",
        text=text,
        sub_type=sub_type,
    )


def _cfg(tmp_path, **kw):
    # open_public 实例典型形态:owner=111,不配 public_groups/allowlist,autoleave 关。
    kw.setdefault("owner_ids", frozenset({"111"}))
    kw.setdefault("open_public", True)
    kw.setdefault("group_autoleave", False)
    return QQBridgeConfig(claude_cli="/x/claude", sessions_dir=str(tmp_path), **kw)


def _patch_runner(monkeypatch, answer="公开答案"):
    calls: list[dict] = []

    async def fake_run(prompt, **k):
        calls.append({"prompt": prompt, **k})
        return (answer, "sess-1", False)

    monkeypatch.setattr(app_mod, "run_claude", fake_run)
    return calls


# =====================================================================
# Change 1 — 开放答疑模式访问门
# =====================================================================
def test_open_mode_random_group_random_user_runs(monkeypatch, tmp_path):
    # 任意群(不在 public_groups)+ 任意用户 @bot => tier=user,照常回答。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path)  # public_groups 空
    _run(build_model_handler(cfg)(_group_msg(user_id="55555", group_id="98765"), reply))
    assert any("公开答案" in o for o in out) and len(calls) == 1


def test_open_mode_admin_in_any_group_is_admin(monkeypatch, tmp_path):
    # admin(owner=111)在任意群里发 admin 命令 /model => tier=admin,命令生效。
    _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path)
    _run(
        build_model_handler(cfg)(
            _group_msg(user_id="111", group_id="424242", text="/model opus"), reply
        )
    )
    assert any("模型已设为" in o for o in out)  # admin 命令在群里对 admin 生效
    assert (
        SessionStore(str(tmp_path)).get("111", "424242").model.startswith("claude-opus")
    )


def test_open_mode_blocked_user_rejected(monkeypatch, tmp_path):
    # 硬约束:开放模式下黑名单仍一律拒(blocked > 一切)。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path, blocked_users=frozenset({"666"}))
    _run(build_model_handler(cfg)(_group_msg(user_id="666", group_id="98765"), reply))
    assert out == [] and calls == []


def test_open_mode_non_at_group_message_ignored(monkeypatch, tmp_path):
    # 不 @bot 的群消息仍静默(防刷屏),开放模式不改这一点。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path)
    _run(build_model_handler(cfg)(_group_msg(user_id="55555", mentions=[]), reply))
    assert out == [] and calls == []


def test_open_mode_does_not_open_private(monkeypatch, tmp_path):
    # open_public 自身不放开私聊:默认 private_access=admin_allowed 时陌生人私聊仍拒。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(tmp_path)  # allowed_users 空
    _run(
        build_model_handler(cfg)(_private_msg(user_id="99999", text="放我进来"), reply)
    )
    assert out == [] and calls == []
    # admin 私聊照常
    _run(build_model_handler(cfg)(_private_msg(user_id="111", text="问题"), reply))
    assert any("公开答案" in o for o in out)


def test_private_access_allows_friends_and_temporary(monkeypatch, tmp_path):
    # Student-union shape: friends and group temporary sessions can ask as normal users.
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, private_access="friends_and_temporary")
    )
    _run(handler(_private_msg(user_id="555", text="校历在哪", sub_type="friend"), reply))
    _run(handler(_private_msg(user_id="666", text="报修流程", sub_type="group"), reply))
    assert len(calls) == 2
    assert sum("公开答案" in o for o in out) >= 2


def test_private_access_rejects_other_private_subtype(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, private_access="friends_and_temporary")
    )
    _run(handler(_private_msg(user_id="555", text="放我进来", sub_type="other"), reply))
    assert out == [] and calls == []


def test_friends_private_access_does_not_allow_temporary(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, private_access="friends")
    )
    _run(handler(_private_msg(user_id="555", text="临时会话", sub_type="group"), reply))
    assert out == [] and calls == []


def test_private_access_blocked_still_wins(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(
            tmp_path,
            open_public=False,
            private_access="friends_and_temporary",
            blocked_users=frozenset({"555"}),
        )
    )
    _run(handler(_private_msg(user_id="555", text="校历在哪", sub_type="friend"), reply))
    assert out == [] and calls == []


def test_open_mode_notice_never_autoleaves(tmp_path):
    # 开放模式:被拉进任何群都不自动退(哪怕 group_autoleave 意外为 True)。
    cfg = QQBridgeConfig(
        owner_ids=frozenset({"111"}),
        open_public=True,
        group_autoleave=True,  # 即使总开关开,open_public 也压过它不退群
        group_allowlist=frozenset(),
    )
    calls = []

    async def call_action(action, params):
        calls.append((action, params))

    ev = {
        "notice_type": "group_increase",
        "self_id": BOT_ID,
        "user_id": BOT_ID,
        "group_id": "13579",
    }
    _run(build_notice_handler(cfg)(ev, call_action))
    assert calls == []  # 开放模式绝不退群


# =====================================================================
# Change 1 — read_only / scope 在开放模式下仍是硬门
# =====================================================================
def test_open_mode_still_enforces_readonly_and_scope(monkeypatch, tmp_path):
    # 隐私由设计保证:read_only 硬门 + 8781 scoped 网关对每次运行都生效,与谁提问无关。
    monkeypatch.setenv("QQ_READ_ONLY", "1")
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    cfg = _cfg(
        tmp_path,
        permission_mode="bypassPermissions",  # 只读模式下不被信任
        mcp_config='{"mcpServers": {"rtime-library-gateway": '
        '{"type": "http", "url": "http://127.0.0.1:8781/mcp"}}}',
    )
    _run(
        build_model_handler(cfg)(
            _group_msg(user_id="55555", group_id="98765", text="宿舍报修流程"), reply
        )
    )
    assert len(calls) == 1
    run = calls[0]
    # dontAsk 硬门:即使一个随机陌生人在随机群提问,只读依然强制。
    assert run["permission_mode"] == "dontAsk"
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent"):
        assert tool in run["disallowed_tools"]
    assert "Bash" not in run["disallowed_tools"]  # 裸 Bash 不进 deny
    assert "Read" in run["allowed_tools"] and "Grep" in run["allowed_tools"]
    assert "mcp__rtime-library-gateway__*" in run["allowed_tools"]
    # scoped 8781 网关按 profile 原样传给 runner(scope/personal-data 拒在网关进程执行)。
    assert run["mcp_config"] and "8781" in run["mcp_config"]


# =====================================================================
# Change 2 — 命令分层(私聊)
# =====================================================================
def test_basic_commands_work_for_normal_user_private(monkeypatch, tmp_path):
    # 白名单普通用户私聊:basic 命令(/new /stream /help)都能用。
    _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, allowed_users=frozenset({"555"}))
    )
    _run(handler(_private_msg(user_id="555", text="/new"), reply))
    assert out == ["🆕 已开始新对话"]
    out.clear()
    _run(handler(_private_msg(user_id="555", text="/stream on"), reply))
    assert any("已开启流式" in o for o in out)
    out.clear()
    _run(handler(_private_msg(user_id="555", text="/help"), reply))
    assert len(out) == 1 and "/new" in out[0] and "/stream" in out[0]


def test_help_hides_admin_commands_from_normal_user(monkeypatch, tmp_path):
    # /help 只列 caller 能用的命令:普通用户看不到 admin 命令 /model。
    _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, allowed_users=frozenset({"555"}))
    )
    _run(handler(_private_msg(user_id="555", text="/help"), reply))
    assert "/model" not in out[0]


def test_help_shows_admin_commands_for_admin(monkeypatch, tmp_path):
    _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path, open_public=False))
    _run(handler(_private_msg(user_id="111", text="/help"), reply))
    assert "/model" in out[0] and "/new" in out[0]


def test_admin_command_from_non_admin_refused_no_run(monkeypatch, tmp_path):
    # /model 非 admin => 友好拒绝,不落模型,会话模型不变。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(
        _cfg(tmp_path, open_public=False, allowed_users=frozenset({"555"}))
    )
    _run(handler(_private_msg(user_id="555", text="/model opus"), reply))
    assert calls == []  # 不落模型
    assert len(out) == 1 and "管理员" in out[0]  # 友好拒绝
    assert SessionStore(str(tmp_path)).get("555", "555").model == ""  # 没生效


def test_admin_command_from_admin_runs(monkeypatch, tmp_path):
    _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path, open_public=False))
    _run(handler(_private_msg(user_id="111", text="/model opus"), reply))
    assert any("模型已设为" in o for o in out)
    assert SessionStore(str(tmp_path)).get("111", "111").model.startswith("claude-opus")


def test_unknown_slash_command_treated_as_qa(monkeypatch, tmp_path):
    # 未知 /foo 落回普通问答(现状行为)。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path, open_public=False))
    _run(handler(_private_msg(user_id="111", text="/foobar 帮我查一下"), reply))
    assert len(calls) == 1  # 进了模型
    assert "/foobar 帮我查一下" in calls[0]["prompt"]


# =====================================================================
# Change 2 — 命令分层(群聊)
# =====================================================================
def test_group_basic_command_works_for_normal_member(monkeypatch, tmp_path):
    # 群里普通成员的 basic 命令生效(旧行为是静默忽略,现在改为能用)。
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path))
    _run(handler(_group_msg(user_id="55555", group_id="98765", text="/new"), reply))
    assert calls == [] and out == ["🆕 已开始新对话"]


def test_group_admin_command_from_non_admin_refused(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path))
    _run(
        handler(
            _group_msg(user_id="55555", group_id="98765", text="/model opus"), reply
        )
    )
    assert calls == [] and len(out) == 1 and "管理员" in out[0]


def test_group_unknown_slash_falls_through_to_qa(monkeypatch, tmp_path):
    calls = _patch_runner(monkeypatch)
    out, reply = _replies()
    handler = build_model_handler(_cfg(tmp_path))
    _run(handler(_group_msg(user_id="55555", group_id="98765", text="/foo bar"), reply))
    assert len(calls) == 1  # 未知命令落回问答
