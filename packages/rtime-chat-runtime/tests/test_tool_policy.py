# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Shared tool-policy core: per-channel profile (disallow, intents, hints)."""

import json

from rtime_chat_runtime.tool_policy import ToolPolicy

QQ = ToolPolicy(channel="QQ", entry="qq", extra_disallowed=("Task", "Agent"))


def test_disallowed_includes_cron_and_extra():
    d = QQ.disallowed_tools_for_text("x")
    assert "CronCreate" in d and "Task" in d and "Agent" in d


def test_no_extra_disallowed_default():
    assert ToolPolicy(channel="飞书").disallowed_tools_for_text("x") == [
        "CronCreate",
        "CronDelete",
        "CronList",
    ]


def test_allowed_none_for_plain():
    assert QQ.allowed_tools_for_text("讲讲热统") is None


def test_allowed_web_intent():
    assert "WebSearch" in (QQ.allowed_tools_for_text("上网搜索一下") or [])


def test_hints_skip_commands():
    assert QQ.add_runtime_policy_hints("/model opus") == "/model opus"


def test_reminder_hint_uses_channel_label():
    assert "QQ 桥接" in QQ.add_runtime_policy_hints("明天提醒我交作业")


def test_memory_hint_uses_entry():
    assert "--entry qq" in QQ.add_runtime_policy_hints("记住我喜欢简洁")


def test_formula_hint_appended_when_set():
    p = ToolPolicy(channel="QQ", formula_hint="\n\n[FORMULA-HINT]")
    assert "FORMULA-HINT" in p.add_runtime_policy_hints("hi")


def test_personal_library_gated_by_env(monkeypatch):
    p = ToolPolicy(channel="QQ", personal_library_env="QQ_TEST_PL")
    monkeypatch.delenv("QQ_TEST_PL", raising=False)
    assert p.allowed_tools_for_text("看我的个人库") is None  # gate off
    monkeypatch.setenv("QQ_TEST_PL", "1")
    assert "Read" in (p.allowed_tools_for_text("看我的个人库") or [])  # gate on


# --- campus web-intent routing (块2) ---
def test_campus_intent_allows_web_tools():
    tools = QQ.allowed_tools_for_text("东区班车几点发车？") or []
    assert "WebFetch" in tools and "WebSearch" in tools


def test_campus_intent_hint_includes_service_urls():
    enriched = QQ.add_runtime_policy_hints("东区班车几点发车？")
    assert "校园服务地址" in enriched
    assert "busTimetable" in enriched
    assert "rtime-web-fetch" in enriched


def test_campus_terms_calendar_and_jw():
    for text in ("这学期校历什么时候放假", "帮我看看教务处最新通知公告"):
        assert "WebFetch" in (QQ.allowed_tools_for_text(text) or [])
        assert "校园服务地址" in QQ.add_runtime_policy_hints(text)


def test_timetable_alone_allows_web_but_no_campus_hint():
    # 泛"时刻表"(火车/航班)放行 web 工具，但不附校园服务地址。
    text = "帮我查下明早去北京的高铁时刻表"
    assert "WebFetch" in (QQ.allowed_tools_for_text(text) or [])
    assert "校园服务地址" not in QQ.add_runtime_policy_hints(text)


def test_plain_text_unaffected_by_campus_routing():
    assert QQ.allowed_tools_for_text("讲讲热统") is None
    enriched = QQ.add_runtime_policy_hints("讲讲热统")
    assert "校园服务地址" not in enriched
    assert "运行环境提示" not in enriched  # no hints at all for plain study text


def test_campus_urls_env_file_override(monkeypatch, tmp_path):
    f = tmp_path / "campus.json"
    f.write_text(
        json.dumps([{"name": "测试页", "url": "https://example.org/x"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv("RTIME_CAMPUS_URLS_FILE", str(f))
    enriched = QQ.add_runtime_policy_hints("看看校历")
    assert "https://example.org/x" in enriched
    assert "busTimetable" not in enriched


# --- read-only hard door (公开只读实例,如学生会答疑) ---
RO = ToolPolicy(
    channel="QQ", entry="qq", extra_disallowed=("Task", "Agent"), read_only=True
)


def test_readonly_disallows_write_and_spawn_tools():
    d = RO.disallowed_tools_for_text("随便什么")
    for tool in ("Edit", "Write", "MultiEdit", "NotebookEdit", "Task", "Agent"):
        assert tool in d
    assert "CronCreate" in d  # core Cron disallow还在
    assert d.count("Task") == 1 and d.count("Agent") == 1  # extra_disallowed去重


def test_readonly_does_not_deny_bare_bash():
    # deny 压过 allow:禁裸 Bash 会连带禁 Bash(rtime-web-fetch *),所以 Bash 不进黑名单,
    # 其余 Bash 靠 dontAsk + 封闭 allowlist 拒绝。
    assert "Bash" not in RO.disallowed_tools_for_text("x")


def test_readonly_allowlist_is_closed_and_readonly():
    allowed = RO.allowed_tools_for_text("讲讲热统")  # plain text: 非只读时应为 None
    assert allowed is not None
    for tool in (
        "Read",
        "Grep",
        "Glob",
        "WebFetch",
        "WebSearch",
        "Bash(rtime-web-fetch *)",
    ):
        assert tool in allowed
    assert "mcp__rtime-library-gateway__*" in allowed  # scoped 8781 网关放行
    # 窄口写工具绝不放行:公开提问者不能写 owner 的提醒/记忆/context source。
    joined = ",".join(allowed)
    assert "rtime-reminder-register" not in joined
    assert "rtime-memory-candidate" not in joined
    assert "rtime-context-source" not in joined


def test_readonly_web_intent_still_covered():
    allowed = RO.allowed_tools_for_text("上网搜索一下东区班车") or []
    assert "WebFetch" in allowed and "Bash(rtime-web-fetch *)" in allowed


def test_readonly_reminder_intent_gets_no_write_tools_or_hint():
    allowed = RO.allowed_tools_for_text("明天提醒我交作业") or []
    assert "Bash(rtime-reminder-register *)" not in allowed
    assert "rtime-reminder-register" not in RO.add_runtime_policy_hints(
        "明天提醒我交作业"
    )


def test_readonly_env_driven(monkeypatch):
    p = ToolPolicy(channel="QQ", read_only_env="QQ_TEST_RO")
    monkeypatch.delenv("QQ_TEST_RO", raising=False)
    assert p.is_read_only() is False
    assert p.allowed_tools_for_text("讲讲热统") is None  # 默认0:行为不变
    assert "Write" not in p.disallowed_tools_for_text("x")
    monkeypatch.setenv("QQ_TEST_RO", "1")
    assert p.is_read_only() is True
    assert "Write" in p.disallowed_tools_for_text("x")
    assert "Read" in (p.allowed_tools_for_text("讲讲热统") or [])


def test_readonly_blocks_personal_library_even_if_env_set(monkeypatch):
    p = ToolPolicy(channel="QQ", personal_library_env="QQ_TEST_PL2", read_only=True)
    monkeypatch.setenv("QQ_TEST_PL2", "1")
    allowed = p.allowed_tools_for_text("看我的个人库") or []
    assert allowed == list(_core_readonly_allowed())
    assert "personal-data" not in p.add_runtime_policy_hints("看我的个人库")


def test_default_policy_unaffected_by_readonly_addition():
    # 不开 read_only:一切照旧(plain→None,黑名单只有 Cron*+extra)。
    assert QQ.allowed_tools_for_text("讲讲热统") is None
    assert QQ.disallowed_tools_for_text("x") == [
        "CronCreate",
        "CronDelete",
        "CronList",
        "Task",
        "Agent",
    ]


def test_readonly_permission_mode_constant():
    from rtime_chat_runtime.tool_policy import READONLY_PERMISSION_MODE

    assert READONLY_PERMISSION_MODE == "dontAsk"


def _core_readonly_allowed():
    from rtime_chat_runtime.tool_policy import READONLY_ALLOWED

    return READONLY_ALLOWED


# --- A3 决策3: 管理员上报工具 ---
def test_notify_tool_in_readonly_allowlist():
    ro = ToolPolicy(channel="QQ", entry="qq", read_only=True)
    allowed = ro.allowed_tools_for_text("随便问点啥")
    assert "Bash(rtime-notify-admin *)" in (allowed or [])


def test_notify_tool_allowed_on_escalation_intent_nonreadonly():
    allowed = QQ.allowed_tools_for_text("我要转人工")
    assert "Bash(rtime-notify-admin *)" in (allowed or [])
    # 无上报意图时非只读不平白放行
    assert QQ.allowed_tools_for_text("讲讲热统") is None


def test_escalation_hint_appended_on_intent():
    ro = ToolPolicy(channel="QQ", entry="qq", read_only=True)
    enriched = ro.add_runtime_policy_hints("这事我要投诉,找个管理员")
    assert "rtime-notify-admin" in enriched
    # 普通问题不附上报提示
    assert "rtime-notify-admin" not in ro.add_runtime_policy_hints("班车几点")
