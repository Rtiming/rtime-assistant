# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from brain_visualmd.backends.doc import DocOcrBackend, _wrap_doc_page
from brain_visualmd.formula import (
    FormulaRecognizer,
    NullFormulaRecognizer,
    crop_png,
    get_formula_recognizer,
    recognize_page_formulas,
)
from brain_visualmd.layout import (
    FORMULA,
    TEXT,
    LayoutRegion,
    PageLayout,
    analyze_docpack,
)
from brain_visualmd.models import PageRequest
from PIL import Image  # the `downscale` extra; installed in the dev env


class _MockRecognizer(FormulaRecognizer):
    name = "mock"

    def recognize(self, image_bytes: bytes) -> str:
        return "f=ma"


class _MockSelfDetect(FormulaRecognizer):
    """A recognizer that self-detects formulas on a whole page (like pix2text)."""

    name = "selfdetect"

    def recognize(self, image_bytes: bytes) -> str:
        return "x"

    def detect_and_recognize(self, page_image_bytes: bytes) -> list[str]:
        return ["E=mc^2", "a^2+b^2=c^2"]


def _png(path):
    Image.new("RGB", (40, 40), "white").save(path)
    return path


def test_get_formula_recognizer_none():
    assert isinstance(get_formula_recognizer("none"), NullFormulaRecognizer)
    assert get_formula_recognizer("none").recognize(b"x") == ""


def test_crop_png(tmp_path):
    p = _png(tmp_path / "p.png")
    b = crop_png(p, [0, 0, 20, 20])
    assert b is not None and b[:4] == b"\x89PNG"


def test_recognize_page_formulas_only_formula_regions(tmp_path):
    p = _png(tmp_path / "p.png")
    layout = PageLayout(
        1,
        [
            LayoutRegion(1, FORMULA, [0, 0, 20, 20], 0.9, 0),
            LayoutRegion(1, TEXT, [0, 20, 20, 40], 0.9, 1),
        ],
    )
    assert recognize_page_formulas(p, layout, _MockRecognizer()) == ["f=ma"]


def test_recognize_skips_when_disabled_or_no_layout(tmp_path):
    p = _png(tmp_path / "p.png")
    # crop-only recognizer (base detect_and_recognize -> None) + no layout -> []
    assert recognize_page_formulas(p, None, _MockRecognizer()) == []
    assert recognize_page_formulas(p, PageLayout(1, []), NullFormulaRecognizer()) == []


def test_page_level_self_detection_without_layout(tmp_path):
    # a self-detecting recognizer (pix2text-style) fills formulas WITHOUT layout.json
    p = _png(tmp_path / "p.png")
    assert recognize_page_formulas(p, None, _MockSelfDetect()) == [
        "E=mc^2",
        "a^2+b^2=c^2",
    ]


def test_doc_backend_formula_section_self_detect_no_layout(tmp_path):
    # doc backend with a self-detecting recognizer + NO analyze -> 公式 section filled
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    _png(d / "images" / "p-001.png")
    backend = DocOcrBackend(base_url="http://x/v1", model="m")
    backend._formula = _MockSelfDetect()  # inject (no layout.json present)
    req = PageRequest(
        page_no=1,
        page_png="images/p-001.png",
        page_png_path=str(d / "images" / "p-001.png"),
    )
    sec = backend._formula_section(req)
    assert "$$\nE=mc^2\n$$" in sec and "$$\na^2+b^2=c^2\n$$" in sec


def test_dedup_drops_merged_parent_box():
    from brain_visualmd.formula import _dedup_formula_regions

    big = LayoutRegion(1, FORMULA, [0, 0, 100, 100], 0.9, 0)  # merged parent
    tight = LayoutRegion(1, FORMULA, [10, 10, 30, 30], 0.9, 1)  # leaf inside big
    kept = _dedup_formula_regions([big, tight])
    assert tight in kept and big not in kept


def test_garbled_crop_is_dropped(tmp_path):
    from brain_visualmd.formula import _looks_garbled

    assert _looks_garbled(
        r"\begin{array}{c}f_0=1 & \bar{z}\bar{N}\bar{\Omega}\end{array}"
    )
    assert not _looks_garbled(r"f_0=\frac{1}{\exp(x)+1}")


def test_is_real_formula_filters_single_symbols():
    from brain_visualmd.formula import _is_real_formula

    # real formulas (relation / operator) kept
    assert _is_real_formula(r"u(r)=\frac{z_1 z_2 e^2}{4\pi r}")
    assert _is_real_formula(r"\alpha=\sum_{i}^{N}\frac{1}{a_i}")
    # MFD single-symbol "embedding" noise dropped
    for noise in ("e", r"\alpha", r"\mathrm{z}_{1}", r"\mathrm{n}", r"\dot{\alpha}"):
        assert not _is_real_formula(noise), noise


def test_wrap_doc_page_uses_formula_section():
    block = _wrap_doc_page(1, "正文一句", "$$\nf=ma\n$$")
    assert "$$\nf=ma\n$$" in block
    assert "见正文内 LaTeX" not in block  # replaced by the real formula section


def test_doc_backend_formula_section_from_layout(tmp_path):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    _png(d / "images" / "p-001.png")

    class _MockDetector:
        name = "mock"

        def detect(self, img, page_no):
            return [LayoutRegion(1, FORMULA, [0, 0, 20, 20], 0.9, 0)]

    analyze_docpack(d, _MockDetector(), 1)
    backend = DocOcrBackend(base_url="http://x/v1", model="m")
    backend._formula = _MockRecognizer()  # inject (env default is none)
    req = PageRequest(
        page_no=1,
        page_png="images/p-001.png",
        page_png_path=str(d / "images" / "p-001.png"),
    )
    assert backend._formula_section(req) == "$$\nf=ma\n$$"
