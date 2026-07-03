# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from tool_policy import (
    CONTEXT_SOURCE_ALLOWED_TOOLS,
    FEISHU_DEFAULT_DISALLOWED_TOOLS,
    IMAGE_ALLOWED_TOOLS,
    MEMORY_ALLOWED_TOOLS,
    PERSONAL_LIBRARY_ALLOWED_TOOLS,
    QQ_CODE_ALLOWED_TOOLS,
    REMINDER_ALLOWED_TOOLS,
    WEB_ALLOWED_TOOLS,
    add_runtime_policy_hints,
    add_web_fallback_hint,
    allowed_tools_for_text,
    context_source_intent_detected,
    disallowed_tools_for_text,
    memory_intent_detected,
    merge_allowed_tools,
    owner_personal_library_access_enabled,
    personal_library_intent_detected,
    qq_code_intent_detected,
    reminder_intent_detected,
)


def test_allowed_tools_for_text_requires_web_intent():
    assert allowed_tools_for_text("整理一下当前项目文档") is None
    assert allowed_tools_for_text("联网搜索一下 Claude Code 权限") == WEB_ALLOWED_TOOLS
    assert allowed_tools_for_text("打开 https://example.org 看看") == WEB_ALLOWED_TOOLS


def test_qq_code_intent_gets_narrow_request_tool():
    text = "我 QQ 小号掉线了，帮我把码发过来"

    assert qq_code_intent_detected(text) is True
    assert allowed_tools_for_text(text) == QQ_CODE_ALLOWED_TOOLS
    enriched = add_runtime_policy_hints(text)
    assert "rtime-qq-code request" in enriched
    assert "不要尝试读取 Docker" in enriched
    assert "qq_selfheal" in enriched


def test_qq_code_intent_does_not_match_normal_code_requests():
    assert qq_code_intent_detected("给我代码看看") is False
    assert allowed_tools_for_text("给我代码看看") is None


def test_qq_code_tool_can_be_actor_gated_by_bridge():
    text = "我 QQ 小号掉线了，帮我把码发过来"

    assert allowed_tools_for_text(text, allow_qq_code=False) is None
    enriched = add_runtime_policy_hints(text, allow_qq_code=False)
    assert "rtime-qq-code request" not in enriched


def test_add_web_fallback_hint_only_for_web_requests():
    plain = "总结这段文字"
    assert add_web_fallback_hint(plain) == plain

    web_text = add_web_fallback_hint("搜索一下 Claude Code")
    assert "rtime-web-fetch search" in web_text
    assert "WebFetch" in web_text


def test_merge_allowed_tools_deduplicates_preserving_order():
    assert merge_allowed_tools(IMAGE_ALLOWED_TOOLS, ["Read"], WEB_ALLOWED_TOOLS) == [
        "Read",
        *WEB_ALLOWED_TOOLS,
    ]


def test_reminder_intent_gets_register_tool_not_cron():
    text = "明天北京时间9点提醒我写作业"

    assert reminder_intent_detected(text) is True
    assert allowed_tools_for_text(text) == REMINDER_ALLOWED_TOOLS
    assert disallowed_tools_for_text(text) == FEISHU_DEFAULT_DISALLOWED_TOOLS


def test_runtime_policy_hint_blocks_claude_cron_for_reminders():
    enriched = add_runtime_policy_hints("10分钟后提醒我喝水")

    assert "rtime-reminder-register add" in enriched
    assert "CronCreate/CronList/CronDelete" in enriched
    assert "`notify` 只用于完全自包含的固定文本推送" in enriched
    assert "默认使用 `wake`" in enriched
    assert "自包含到点任务" in enriched


def test_memory_intent_gets_candidate_tool_only():
    text = "请记住：我复习时先看当天计划"

    assert memory_intent_detected(text) is True
    assert allowed_tools_for_text(text) == MEMORY_ALLOWED_TOOLS
    enriched = add_runtime_policy_hints(text)
    assert "rtime-memory-candidate add" in enriched
    assert "memory/cards" in enriched


def test_context_source_intent_gets_registry_tool_only():
    text = "把这个复习计划加入动态上下文源"

    assert context_source_intent_detected(text) is True
    assert allowed_tools_for_text(text) == CONTEXT_SOURCE_ALLOWED_TOOLS
    enriched = add_runtime_policy_hints(text)
    assert "rtime-context-source list|add|deactivate|check" in enriched
    assert "禁止 personal-data" in enriched


def test_personal_library_access_is_env_gated(monkeypatch):
    text = "请查看我的个人库和聊天记录，概括近期重点"

    assert personal_library_intent_detected(text) is True
    monkeypatch.delenv("FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS", raising=False)
    assert owner_personal_library_access_enabled() is False
    assert allowed_tools_for_text(text) is None
    assert "/mnt/brain/personal-data" not in add_runtime_policy_hints(text)

    monkeypatch.setenv("FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS", "1")
    assert owner_personal_library_access_enabled() is True
    assert allowed_tools_for_text(text) == PERSONAL_LIBRARY_ALLOWED_TOOLS
    enriched = add_runtime_policy_hints(text)
    assert "/mnt/brain/personal-data" in enriched
    assert "Read/Grep/Glob/LS" in enriched
    assert "不要整段外发" in enriched


def test_personal_library_intent_merges_with_other_allowlists(monkeypatch):
    monkeypatch.setenv("FEISHU_OWNER_PERSONAL_LIBRARY_ACCESS", "1")

    tools = allowed_tools_for_text("联网搜索一下，并结合我的个人信息判断")

    assert tools == [*WEB_ALLOWED_TOOLS, *PERSONAL_LIBRARY_ALLOWED_TOOLS]
    assert "Bash(*)" not in tools


def test_mixed_reminder_and_memory_intents_merge_allowlists():
    tools = allowed_tools_for_text("明天提醒我复习，并记住我喜欢先看计划")

    assert tools == [*REMINDER_ALLOWED_TOOLS, *MEMORY_ALLOWED_TOOLS]
    assert "Bash(*)" not in tools


def test_runtime_policy_hint_explains_attachment_send_directive():
    enriched = add_runtime_policy_hints("生成一个图片发给我")

    assert "[[rtime-send-image:/absolute/path]]" in enriched
    assert "[[rtime-send-file:/absolute/path]]" in enriched
    assert "真正的飞书附件" in enriched


def test_reminder_policy_does_not_trigger_for_module_diagnostics():
    assert reminder_intent_detected("定时模块有问题，帮我诊断") is False
    assert allowed_tools_for_text("定时模块有问题，帮我诊断") is None


def test_formula_output_hint_present_for_normal_messages_and_forbids_images():
    enriched = add_runtime_policy_hints("帮我写薛定谔方程")
    assert "LaTeX" in enriched
    assert "不要" in enriched and ("图片" in enriched or "png" in enriched)
    # Present for any ordinary (non-command) message so the model knows the policy.
    assert "公式" in add_runtime_policy_hints("你好")


def test_slash_commands_get_no_hints():
    # Commands are parsed downstream from this same text; hints must not corrupt them.
    assert add_runtime_policy_hints("/model opus") == "/model opus"
    assert add_runtime_policy_hints("/new") == "/new"
    assert add_runtime_policy_hints("  /help") == "  /help"


def test_campus_intent_allows_web_and_appends_service_urls():
    # 块2 校园网页意图路由：口语化校园问题不带 URL 也放行 web 工具并附已知地址。
    text = "东区班车几点发车"
    assert allowed_tools_for_text(text) == WEB_ALLOWED_TOOLS
    enriched = add_runtime_policy_hints(text)
    assert "校园服务地址" in enriched
    assert "busTimetable" in enriched


def test_non_campus_text_gets_no_campus_hint():
    assert "校园服务地址" not in add_runtime_policy_hints("总结这段文字")
    assert allowed_tools_for_text("总结这段文字") is None
