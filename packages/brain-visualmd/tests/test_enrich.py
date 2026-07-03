# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import brain_visualmd.enrich as E
from brain_visualmd.enrich import _replace_formula_section, enrich_docpack
from brain_visualmd.formula import FormulaRecognizer
from brain_visualmd.validate import is_ok, validate_page_block
from PIL import Image

PAGE = (
    "<!-- page: 001 -->\n## 第 1 页：t\n\n![第1页](images/p-001.png)\n\n"
    "### 文字\n- a\n\n### 公式\n- 见正文内 LaTeX\n\n### 图表\n- 无\n\n### 存疑\n- 无\n"
)


def _docpack(tmp_path):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    Image.new("RGB", (40, 40), "white").save(d / "images" / "p-001.png")
    (d / "slide.md").write_text("---\ntitle: slide\n---\n# slide\n\n" + PAGE, "utf-8")
    return d


def test_replace_formula_section_fills_and_preserves_gate():
    out = _replace_formula_section(PAGE, ["E=mc^2", "a=b"])
    assert "$$\nE=mc^2\n$$" in out and "$$\na=b\n$$" in out
    assert "见正文内 LaTeX" not in out
    assert is_ok(validate_page_block(out, 1)), [
        i.message for i in validate_page_block(out, 1)
    ]
    # empty -> "- 无", still gate-conformant
    empty = _replace_formula_section(PAGE, [])
    assert "### 公式\n- 无\n\n### 图表" in empty
    assert is_ok(validate_page_block(empty, 1))


class _Mock(FormulaRecognizer):
    name = "mock"

    def recognize(self, image_bytes: bytes) -> str:
        return "x"

    def detect_and_recognize(self, page_image_bytes: bytes) -> list[str]:
        return ["k_B T", "\\nabla\\cdot E"]


def test_enrich_docpack_fills_formulas(tmp_path, monkeypatch):
    monkeypatch.setattr(E, "get_formula_recognizer", lambda name: _Mock())
    d = _docpack(tmp_path)
    summary = enrich_docpack(d, "mock")
    assert summary["pages_filled"] == 1 and summary["formulas"] == 2
    md = (d / "slide.md").read_text("utf-8")
    assert "$$\nk_B T\n$$" in md and "$$\n\\nabla\\cdot E\n$$" in md
    assert "见正文内 LaTeX" not in md
    assert "# slide" in md  # head/frontmatter untouched


def test_enrich_none_recognizer_sets_wu(tmp_path):
    d = _docpack(tmp_path)
    enrich_docpack(d, "none")  # NullRecognizer -> no formulas -> "- 无"
    assert "### 公式\n- 无\n\n### 图表" in (d / "slide.md").read_text("utf-8")
