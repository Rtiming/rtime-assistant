# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from brain_visualmd.spec import render_frontmatter
from brain_visualmd.validate import is_ok, validate_document

GOOD_META = {
    "title": "t",
    "source": "x.pdf",
    "source_sha256": "abc",
    "tier": "strict-visual",
    "status": "draft",
    "pages": 1,
    "backend": "stub:1",
    "spec_version": "1.0",
    "created": "2026-06-22",
}


def page_block(n=1, sections=("文字", "公式", "图表", "存疑")):
    lines = [
        f"<!-- page: {n:03d} -->",
        f"## 第 {n} 页：t",
        "",
        f"![第{n}页](images/p-{n:03d}.png)",
        "",
    ]
    for s in sections:
        lines += [f"### {s}", "- 无", ""]
    return "\n".join(lines)


def doc(meta=None, body=None):
    meta = {**GOOD_META, **(meta or {})}
    body = body if body is not None else page_block(1)
    return render_frontmatter(meta) + f"\n# t\n\n{body}\n"


def codes(issues):
    return {i.code for i in issues}


def test_good_document_passes():
    issues = validate_document(doc(), expected_pages=1)
    assert is_ok(issues), [i.message for i in issues]


def test_missing_section_fails():
    body = page_block(1, sections=("文字", "公式", "存疑"))  # drop 图表
    issues = validate_document(doc(body=body), expected_pages=1)
    assert "missing_section" in codes(issues)
    assert not is_ok(issues)


def test_inline_dollar_fails():
    body = page_block(1).replace("## 第 1 页：t", "## 第 1 页：价格 $5 元")
    issues = validate_document(doc(body=body), expected_pages=1)
    assert "inline_dollar" in codes(issues)


def test_block_dollar_is_allowed():
    body = page_block(1).replace("### 公式\n- 无", "### 公式\n$$\nk_B T\n$$")
    issues = validate_document(doc(body=body), expected_pages=1)
    assert "inline_dollar" not in codes(issues)
    assert is_ok(issues), [i.message for i in issues]


def test_page_count_mismatch_fails():
    issues = validate_document(doc(body=page_block(1)), expected_pages=2)
    assert "page_count" in codes(issues)


def test_missing_png_ref_fails():
    body = page_block(1).replace("![第1页](images/p-001.png)", "(no image)")
    issues = validate_document(doc(body=body), expected_pages=1)
    assert "missing_png_ref" in codes(issues)


def test_bad_tier_fails():
    issues = validate_document(doc(meta={"tier": "draft"}), expected_pages=1)
    assert "bad_tier" in codes(issues)


def test_no_frontmatter_fails():
    issues = validate_document(page_block(1), expected_pages=1)
    assert "no_frontmatter" in codes(issues)


def test_replacement_char_fails():
    body = page_block(1).replace("- 无", "- 乱�码", 1)
    issues = validate_document(doc(body=body), expected_pages=1)
    assert "replacement_char" in codes(issues)
