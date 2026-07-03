# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Per-channel output renderer (output_render): plain_text downgrade guarantees,
rich/markdown dispatch, and the policy enum."""

from rtime_chat_runtime.output_render import (
    RenderPolicy,
    render,
    strip_markdown_plain_text,
)


# --- plain_text: markdown -> plain text downgrade guarantees ------------------
def test_plain_text_downgrades_markdown():
    out = strip_markdown_plain_text(
        "## 核学院\n"
        "- **教学秘书**：王示例\n"
        "- 邮箱：`office-test@example.edu`\n"
        "> 来源：[教务处](https://teach.ustc.edu.cn/x)\n"
        "---\n"
        "* 备注：先电话确认"
    )
    assert "**" not in out and "##" not in out and "`" not in out
    assert "> " not in out  # blockquote marker removed
    assert "教学秘书：王示例" in out
    assert "office-test@example.edu" in out
    assert "教务处（https://teach.ustc.edu.cn/x）" in out  # link -> label（url）
    assert "- 备注：先电话确认" in out  # '*' bullet normalized to '-'


def test_plain_text_preserves_plain_and_paths():
    # snake_case paths, single '*', bare urls and emoji must survive untouched
    src = "见 knowledge/institutions/ustc/a_b_c.md ，2*3=6，电话 63603982 📞"
    assert strip_markdown_plain_text(src) == src


def test_plain_text_code_fence_dropped_body_kept():
    out = strip_markdown_plain_text("```python\nprint(1)\n```")
    assert "```" not in out
    assert "print(1)" in out


def test_plain_text_underscore_bold_and_italic():
    assert strip_markdown_plain_text("__粗__ 和 *斜* 文字") == "粗 和 斜 文字"


def test_plain_text_hr_and_blank_collapse():
    out = strip_markdown_plain_text("上\n\n\n\n***\n下")
    assert "***" not in out
    assert "\n\n\n" not in out


def test_plain_text_empty_passthrough():
    assert strip_markdown_plain_text("") == ""


# --- render() dispatch --------------------------------------------------------
def test_render_plain_text_equals_strip():
    src = "## 标题\n**粗** 和 `代码`"
    assert render(src, RenderPolicy.PLAIN_TEXT) == strip_markdown_plain_text(src)


def test_render_markdown_passthrough():
    src = "## 标题\n\n**粗体** 和 `代码` 与 [链接](https://e.com)"
    assert render(src, RenderPolicy.MARKDOWN) == src  # frontend renders, no processing


def test_render_rich_uses_injected_renderer():
    src = "能量 $E=mc^2$"
    called = {}

    def fake_rich(text: str) -> str:
        called["text"] = text
        return "能量 E=mc²"

    out = render(src, RenderPolicy.RICH, rich_renderer=fake_rich)
    assert out == "能量 E=mc²"
    assert called["text"] == src


def test_render_rich_without_renderer_is_passthrough():
    src = "$$E=mc^2$$"
    assert render(src, RenderPolicy.RICH) == src  # runtime keeps no app dependency


def test_render_accepts_string_policy():
    src = "## H\n**b**"
    assert render(src, "plain_text") == strip_markdown_plain_text(src)
    assert render(src, "markdown") == src


def test_render_defaults_to_markdown_passthrough():
    src = "## H\n**b**"
    assert render(src) == src


def test_render_none_and_empty_safe():
    assert render(None, RenderPolicy.MARKDOWN) == ""  # type: ignore[arg-type]
    assert render(None, RenderPolicy.RICH) == ""  # type: ignore[arg-type]
    assert render("", RenderPolicy.PLAIN_TEXT) == ""


# --- policy enum --------------------------------------------------------------
def test_policy_enum_values():
    assert RenderPolicy.PLAIN_TEXT.value == "plain_text"
    assert RenderPolicy.RICH.value == "rich"
    assert RenderPolicy.MARKDOWN.value == "markdown"
    assert RenderPolicy("rich") is RenderPolicy.RICH


def test_policy_enum_rejects_unknown():
    import pytest

    with pytest.raises(ValueError):
        render("x", "bogus")
