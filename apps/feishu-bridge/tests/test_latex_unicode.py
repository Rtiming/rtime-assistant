# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Tests for latex_unicode: faithful LaTeX -> Unicode rendering for Feishu chat."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from latex_unicode import (  # noqa: E402
    contains_latex_math,
    render_math_for_feishu,
    _convert_expr,
    _looks_mathy,
)


def conv(s: str) -> str:
    text, ok = _convert_expr(s)
    return text


# --- detection ------------------------------------------------------------

def test_contains_detects_math():
    assert contains_latex_math("能量 $E=mc^2$ 守恒")
    assert contains_latex_math("$$\\int x\\,dx$$")
    assert contains_latex_math("用 \\frac{1}{2} 表示")
    assert contains_latex_math("\\(a+b\\)")


def test_contains_ignores_non_math():
    assert not contains_latex_math("这是一段普通中文,没有公式")
    assert not contains_latex_math("纯文本 hello world")


def test_looks_mathy_guards_prices():
    assert not _looks_mathy("5 和 ")
    assert not _looks_mathy("100")
    assert _looks_mathy("E = mc^2")
    assert _looks_mathy("a+b")
    assert _looks_mathy("x")
    assert _looks_mathy("\\alpha")


# --- superscript / subscript ---------------------------------------------

def test_superscript_digits():
    assert conv("E = mc^2") == "E = mc²"
    assert conv("x^{n+1}") == "xⁿ⁺¹"
    assert conv("a^{abc}") == "aᵃᵇᶜ"


def test_subscript():
    assert conv("x_i") == "xᵢ"
    assert conv("x_{ij}") == "xᵢⱼ"
    assert conv("T_{max}") == "Tₘₐₓ"
    assert conv("a_{n+1}") == "aₙ₊₁"


def test_subscript_nonmappable_falls_to_paren():
    # 'q' has no subscript glyph -> parenthesized form, never dropped.
    out = conv("x_{pq}")
    assert out == "x_(pq)"


# --- greek + symbols ------------------------------------------------------

def test_greek():
    assert conv("\\alpha + \\beta") == "α + β"
    assert conv("\\Omega") == "Ω"
    assert conv("\\hbar\\omega") == "ℏω"


def test_symbols():
    assert conv("a \\leq b") == "a ≤ b"
    assert conv("\\partial") == "∂"
    assert conv("\\nabla^2") == "∇²"
    assert conv("\\infty") == "∞"
    assert conv("a \\times b") == "a × b"


# --- fractions ------------------------------------------------------------

def test_fraction_vulgar():
    assert conv("\\frac{1}{2}") == "½"
    assert conv("\\frac{3}{4}") == "¾"


def test_fraction_atomic():
    assert conv("\\frac{a}{b}") == "a/b"


def test_fraction_compound():
    assert conv("\\frac{\\partial f}{\\partial x}") == "(∂ f)/(∂ x)"
    assert conv("\\frac{\\hbar^2}{2m}") == "(ℏ²)/(2m)"


# --- sqrt -----------------------------------------------------------------

def test_sqrt():
    assert conv("\\sqrt{\\pi}") == "√(π)"
    assert conv("\\sqrt{x+1}") == "√(x+1)"


def test_nth_root():
    assert conv("\\sqrt[3]{x}") == "³√(x)"


# --- accents / styles -----------------------------------------------------

def test_accents():
    assert conv("\\vec{p}") == "p⃗"
    assert conv("\\hat{H}") == "Ĥ"
    assert conv("\\dot{x}") == "ẋ"


def test_mathcal_mathbb():
    assert conv("\\mathcal{H}") == "ℋ"
    assert conv("\\mathbb{R}") == "ℝ"
    assert conv("\\mathrm{d}x") == "dx"


# --- sums / integrals with limits ----------------------------------------

def test_sum_with_limits():
    assert conv("\\sum_{i=1}^{n}") == "∑ᵢ₌₁ⁿ"


def test_integral():
    out = conv("\\int_0^\\infty e^{-x} dx")
    assert "∫" in out and "∞" in out and "e⁻ˣ" in out


# --- physics formulas -----------------------------------------------------

def test_schrodinger_like():
    out = conv("i\\hbar\\frac{\\partial \\psi}{\\partial t} = \\hat{H}\\psi")
    assert "ℏ" in out and "ψ" in out and "Ĥ" in out or "Ĥ" in out


def test_kinetic_energy():
    assert conv("E_k = \\frac{1}{2}mv^2") == "Eₖ = ½mv²"


# --- fallback for unrenderable --------------------------------------------

def test_matrix_falls_back_to_source():
    src = "\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}"
    text, ok = _convert_expr(src)
    assert ok is False
    assert text == src


# --- full-message rendering ----------------------------------------------

def test_render_inline_in_text():
    out = render_math_for_feishu("能量 $E = mc^2$ 是守恒量。")
    assert out == "能量 E = mc² 是守恒量。"


def test_render_display_block():
    out = render_math_for_feishu("结论:\n\n$$\nE = mc^2\n$$\n\n完毕")
    assert "E = mc²" in out
    assert "$$" not in out
    assert "完毕" in out


def test_render_preserves_code_fence():
    text = "示例:\n```python\nx = a$b$c\n```\n结束"
    out = render_math_for_feishu(text)
    assert "x = a$b$c" in out  # inside fence untouched


def test_render_ignores_price_dollars():
    text = "这件 $5 那件 $10,很便宜"
    out = render_math_for_feishu(text)
    assert out == text


def test_render_no_math_unchanged():
    text = "## 标题\n\n**粗体** 和 普通文字"
    assert render_math_for_feishu(text) == text


def test_render_keeps_markdown_table():
    text = "| 课程 | 状态 |\n| --- | --- |\n| 热统 | 有 |"
    assert render_math_for_feishu(text) == text


def test_render_inline_double_dollar_midline():
    # $$...$$ on the same line as text must convert cleanly with no stray $.
    out = render_math_for_feishu("场方程 $$E = mc^2$$ 完毕")
    assert out == "场方程 E = mc² 完毕"
    assert "$" not in out


def test_render_inline_double_dollar_einstein():
    out = render_math_for_feishu("场方程 $$R = \\frac{1}{2}g$$")
    assert "$" not in out
    assert "½g" in out
