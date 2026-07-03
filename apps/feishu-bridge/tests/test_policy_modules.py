# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# access_policy.is_allowed_actor moved to packages/rtime-chat-runtime
# (tested there in tests/test_access_policy.py). This file now covers the
# Feishu-specific output_policy only.
from output_policy import (
    extract_options,
    format_tool,
    normalize_markdown_for_feishu_post,
    split_markdown_for_feishu_post,
    segmented_output_enabled,
    show_tool_calls,
)


def test_output_policy_extracts_numbered_and_yes_no_options():
    numbered = extract_options("Choose:\n1. Build candidate\n2. Wait")
    assert numbered == [("1. Build candidate", "1"), ("2. Wait", "2")]

    assert extract_options("确认继续？Y/N") == [("Yes", "yes"), ("No", "no")]


def test_output_policy_formats_common_tools():
    assert segmented_output_enabled("segmented")
    assert not segmented_output_enabled("card")
    assert show_tool_calls(1)
    assert "pytest" in format_tool("bash", {"command": "pytest tests -q"})
    assert "README.md" in format_tool("read", {"path": "README.md"})


def test_markdown_post_normalization_separates_gfm_tables():
    text = "总结如下：\n| 课 | 状态 |\n| --- | --- |\n| 热统 | 有 |\n后续处理。"

    normalized = normalize_markdown_for_feishu_post(text)

    assert "总结如下：\n\n| 课 | 状态 |" in normalized
    assert "| 热统 | 有 |\n\n后续处理。" in normalized


def test_markdown_post_split_keeps_chunks_under_byte_limit():
    chunks = split_markdown_for_feishu_post("甲乙丙丁\n第二行", max_bytes=10)

    assert len(chunks) >= 2
    assert "".join(chunks).replace("\n", "") == "甲乙丙丁第二行"
    assert all(len(chunk.encode("utf-8")) <= 10 for chunk in chunks)
