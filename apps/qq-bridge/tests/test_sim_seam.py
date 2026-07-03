# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Simulation-harness assertion faces for the QQ process_event seam (T4).

Everything here runs the REAL chain via ``QQEventPipeline.process_event`` fed
synthetic OneBot events (``rtime_chat_runtime.testing.synth``) with the model side
supplied by ``FakeModelRunner`` — zero network, zero subprocess, ordinary pytest.

These are the profile-INDEPENDENT faces (design §3.2): actor-tier matrix, model
pinning, read_only forcing, plaintext rendering, and outbound-action routing.
Profile-dependent expectations (system_prompt == profile file, library scope) land
after T1/T2 provide the profile loader.
"""

from __future__ import annotations

import asyncio

from qq_bridge.app import build_pipeline
from qq_bridge.config import QQBridgeConfig
from qq_bridge.events import OutboundAction
from rtime_chat_runtime.testing import (
    FakeModelRunner,
    ScriptedReply,
    make_qq_group_at,
    make_qq_private,
)

BOT = "479"
ADMIN = "111"
GROUP = "600"


def _run(coro):
    return asyncio.run(coro)


def _cfg(tmp_path, **kw):
    kw.setdefault("owner_ids", frozenset({ADMIN}))
    return QQBridgeConfig(
        claude_cli="/x/claude", sessions_dir=str(tmp_path / "sessions"), **kw
    )


def _process(cfg, event, *, runner=None):
    """Drive one event through the full seam; return (actions, runner)."""
    runner = runner or FakeModelRunner("答案")
    pipeline = build_pipeline(cfg, model_runner=runner)
    actions = _run(pipeline.process_event(event))
    return actions, runner


def _texts(actions) -> list[str]:
    return [a.message_text for a in actions]


# =====================================================================
# A. actor-tier matrix
# =====================================================================
def test_blocked_user_silenced_in_public_group(tmp_path):
    cfg = _cfg(
        tmp_path,
        public_groups=frozenset({GROUP}),
        group_allowlist=frozenset({GROUP}),
        blocked_users=frozenset({"222"}),
    )
    actions, runner = _process(cfg, make_qq_group_at(GROUP, "222", "问题", self_id=BOT))
    assert actions == [] and runner.calls == []


def test_blocked_beats_admin_in_private(tmp_path):
    cfg = _cfg(tmp_path, blocked_users=frozenset({ADMIN}))
    actions, runner = _process(cfg, make_qq_private(ADMIN, "问题", self_id=BOT))
    assert actions == [] and runner.calls == []


def test_admin_private_runs(tmp_path):
    # Default config streams => a "思考中" ack precedes the answer; assert the model
    # ran once and the answer reached the user.
    cfg = _cfg(tmp_path)
    actions, runner = _process(cfg, make_qq_private(ADMIN, "讲讲热统", self_id=BOT))
    assert len(runner.calls) == 1
    assert "答案" in _texts(actions)


def test_non_allowed_private_denied(tmp_path):
    cfg = _cfg(tmp_path)
    actions, runner = _process(cfg, make_qq_private("999", "让我进来", self_id=BOT))
    assert actions == [] and runner.calls == []


def test_private_access_friend_and_temporary_runs(tmp_path):
    cfg = _cfg(tmp_path, private_access="friends_and_temporary")
    friend_actions, runner = _process(
        cfg, make_qq_private("999", "校历在哪", self_id=BOT, sub_type="friend")
    )
    temp_actions, runner = _process(
        cfg,
        make_qq_private("998", "报修流程", self_id=BOT, sub_type="group"),
        runner=runner,
    )
    assert len(runner.calls) == 2
    assert "答案" in _texts(friend_actions)
    assert "答案" in _texts(temp_actions)


def test_empty_allowlists_deny_all_private(tmp_path):
    # No admin, no allowed_users => reject ALL private (never allow-all on QQ).
    cfg = _cfg(tmp_path, owner_ids=frozenset(), admin_ids=frozenset())
    actions, runner = _process(cfg, make_qq_private("2000000001", "hi", self_id=BOT))  # 任意陌生号(假)
    assert actions == [] and runner.calls == []


def test_public_group_at_bot_runs(tmp_path):
    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "东区班车几点", at_bot=True, self_id=BOT)
    )
    assert len(runner.calls) == 1
    assert "答案" in _texts(actions)


def test_public_group_without_at_ignored(tmp_path):
    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "闲聊", at_bot=False, self_id=BOT)
    )
    assert actions == [] and runner.calls == []


def test_group_admin_command_from_non_admin_refused(tmp_path):
    # /model 是 admin 命令:群里非 admin 发 => 友好拒绝,不进模型(命令分层)。
    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "/model opus", at_bot=True, self_id=BOT)
    )
    assert runner.calls == []  # 命令不落模型
    assert any("管理员" in t for t in _texts(actions))  # 收到友好拒绝


def test_group_basic_command_from_any_user_runs(tmp_path):
    # /new 是 basic 命令:群里任何可服务成员都能用(含普通成员),不进模型。
    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "/new", at_bot=True, self_id=BOT)
    )
    assert runner.calls == []  # 命令不落模型
    assert any("已开始新对话" in t for t in _texts(actions))


def test_group_admin_command_from_admin_runs(tmp_path):
    # admin 在群里发 /model 生效(只改 admin 自己的会话,安全)。
    from qq_bridge.sessions import SessionStore

    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, runner = _process(
        cfg, make_qq_group_at(GROUP, ADMIN, "/model opus", at_bot=True, self_id=BOT)
    )
    assert runner.calls == []  # 命令不落模型
    assert any("模型已设为" in t for t in _texts(actions))
    store = SessionStore(str(tmp_path / "sessions"))
    assert store.get(ADMIN, GROUP).model.startswith("claude-opus")


# =====================================================================
# B. model pinning
# =====================================================================
def test_non_admin_pinned_to_instance_default(tmp_path):
    from qq_bridge.sessions import SessionStore

    cfg = _cfg(
        tmp_path,
        model="kimi-fixed",
        public_groups=frozenset({GROUP}),
        group_allowlist=frozenset({GROUP}),
    )
    # Poison the session with a historical /model pick; the pin must override it.
    SessionStore(str(tmp_path / "sessions")).set_model("222", GROUP, "claude-opus-4")
    _, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "问题", at_bot=True, self_id=BOT)
    )
    assert runner.last.model == "kimi-fixed"


def test_admin_model_switch_honored(tmp_path):
    from qq_bridge.sessions import SessionStore

    cfg = _cfg(tmp_path, model="kimi-fixed")
    SessionStore(str(tmp_path / "sessions")).set_model(ADMIN, ADMIN, "claude-opus-4")
    _, runner = _process(cfg, make_qq_private(ADMIN, "问题", self_id=BOT))
    assert runner.last.model == "claude-opus-4"  # admin session choice not pinned


# =====================================================================
# C. read_only forces dontAsk + full deny set
# =====================================================================
def test_readonly_forces_dontask_and_full_deny(tmp_path, monkeypatch):
    monkeypatch.setenv("QQ_READ_ONLY", "1")
    cfg = _cfg(
        tmp_path,
        permission_mode="bypassPermissions",  # must NOT be trusted under read_only
        public_groups=frozenset({GROUP}),
        group_allowlist=frozenset({GROUP}),
    )
    _, runner = _process(
        cfg, make_qq_group_at(GROUP, "222", "宿舍报修流程", at_bot=True, self_id=BOT)
    )
    call = runner.last
    assert call.permission_mode == "dontAsk"
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent"):
        assert tool in call.disallowed_tools
    assert "Bash" not in call.disallowed_tools  # bare Bash never in deny (deny > allow)
    assert "Read" in call.allowed_tools and "Grep" in call.allowed_tools
    assert "mcp__rtime-library-gateway__*" in call.allowed_tools


def test_readonly_off_keeps_session_permission_mode(tmp_path, monkeypatch):
    monkeypatch.delenv("QQ_READ_ONLY", raising=False)
    cfg = _cfg(tmp_path, permission_mode="bypassPermissions")
    _, runner = _process(cfg, make_qq_private(ADMIN, "问题", self_id=BOT))
    call = runner.last
    assert call.permission_mode == "bypassPermissions"
    assert call.allowed_tools is None  # plain text: all tools
    assert "Write" not in call.disallowed_tools


# =====================================================================
# D. renderer: markdown-laden reply -> plaintext outbound
# =====================================================================
def test_markdown_reply_stripped_in_outbound_action(tmp_path):
    md = (
        "## 报修流程\n"
        "- **第一步**：拨打 `63603982`\n"
        "> 来源：[后勤](https://logistics.ustc.edu.cn/x)\n"
        "见 knowledge/institutions/ustc/a_b_c.md ，2*3=6 📞"
    )
    cfg = _cfg(tmp_path)
    runner = FakeModelRunner(ScriptedReply(text=md))
    actions, _ = _process(
        cfg, make_qq_private(ADMIN, "报修", self_id=BOT), runner=runner
    )
    body = "\n".join(_texts(actions))
    # markdown scaffolding gone
    assert "**" not in body
    assert "##" not in body
    assert "`" not in body
    assert "](" not in body and "[后勤]" not in body
    # link downgraded to label（url）
    assert "后勤（https://logistics.ustc.edu.cn/x）" in body
    # plain content preserved untouched
    assert "knowledge/institutions/ustc/a_b_c.md" in body  # snake_case path
    assert "2*3=6" in body
    assert "63603982" in body  # phone
    assert "📞" in body  # emoji


# =====================================================================
# E. outbound-action routing (private -> send_private, group -> send_group)
# =====================================================================
def test_private_routes_to_send_private_with_user_id(tmp_path):
    cfg = _cfg(tmp_path)
    actions, _ = _process(cfg, make_qq_private(ADMIN, "问题", self_id=BOT))
    assert actions
    assert actions[-1].action == "send_private_msg"
    assert actions[-1].params["user_id"] == int(ADMIN)


def test_group_routes_to_send_group_with_group_id(tmp_path):
    cfg = _cfg(
        tmp_path, public_groups=frozenset({GROUP}), group_allowlist=frozenset({GROUP})
    )
    actions, _ = _process(
        cfg, make_qq_group_at(GROUP, "222", "问题", at_bot=True, self_id=BOT)
    )
    assert actions
    assert all(a.action == "send_group_msg" for a in actions)
    assert actions[-1].params["group_id"] == int(GROUP)


def test_group_reply_can_at_sender(tmp_path):
    cfg = _cfg(
        tmp_path,
        public_groups=frozenset({GROUP}),
        group_allowlist=frozenset({GROUP}),
        group_reply_at_sender=True,
    )
    actions, _ = _process(
        cfg, make_qq_group_at(GROUP, "222", "问题", at_bot=True, self_id=BOT)
    )
    assert actions
    assert all(
        a.params.get("message", "").startswith("[CQ:at,qq=222] ")
        for a in actions
        if a.action == "send_group_msg" and isinstance(a.params.get("message"), str)
    )


# =====================================================================
# seam integrity: synth events decode through the real OneBot parser
# =====================================================================
def test_synth_qq_private_decodes_through_parser():
    from qq_bridge.onebot.protocol import parse_message_event

    msg = parse_message_event(make_qq_private("10001", "你好", self_id=BOT))
    assert (msg.is_group, msg.user_id, msg.chat_id, msg.text) == (
        False,
        "10001",
        "10001",
        "你好",
    )
    assert msg.sub_type == "friend"
    assert msg.mentions == []


def test_synth_qq_group_at_bot_decodes_with_mention():
    from qq_bridge.onebot.protocol import parse_message_event

    msg = parse_message_event(
        make_qq_group_at(GROUP, "222", "问题", at_bot=True, self_id=BOT)
    )
    assert msg.is_group and msg.chat_id == GROUP and msg.user_id == "222"
    assert BOT in msg.mentions  # drives _group_message_triggered
    assert msg.text == "问题"


# =====================================================================
# request / notice events flow through the seam identically
# =====================================================================
def test_seam_request_event_produces_outbound_action(tmp_path):
    cfg = _cfg(tmp_path, group_invite_policy="reject")
    pipeline = build_pipeline(cfg, model_runner=FakeModelRunner())
    event = {
        "post_type": "request",
        "request_type": "group",
        "sub_type": "invite",
        "user_id": 999,
        "group_id": 555,
        "flag": "f1",
    }
    actions = _run(pipeline.process_event(event))
    assert actions == [
        OutboundAction(
            action="set_group_add_request",
            params={
                "flag": "f1",
                "sub_type": "invite",
                "approve": False,
                "reason": "bot 不自动入群",
            },
        )
    ]
