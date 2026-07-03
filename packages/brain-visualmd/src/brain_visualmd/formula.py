# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Per-region formula recognition: crop formula regions (from layout) → LaTeX.

Uses the pre-analysis bboxes (``layout.json``) to crop each formula region and
recognize it with a specialist (CDM ~0.97 on isolated formulas) — more reliable
on dense/isolated formulas than the whole-page doc model. The recognized LaTeX
fills the page's 公式 section. Pluggable + optional deps:

- ``pix2text`` — Pix2Text MFD(公式检测)+MFR(LatexOCR). **中文感知**, MIT. Unlike
  ``rapid``, it self-detects formulas on a full page, so it fills the 公式 section
  WITHOUT a pre-computed ``layout.json``. ``pip install pix2text`` (extra: ``formula-zh``).
- ``rapid`` — RapidLaTeXOCR (ONNX, light, ~2-3s/formula CPU). **No Chinese** —
  crop-only (needs ``layout.json``). ``pip install rapid_latex_ocr`` (extra: ``formula``).
- ``none``  — disabled (default); 公式 section stays a pointer to inline LaTeX.

Cropping needs Pillow (the ``downscale`` extra). Enable via env
``VISUALMD_FORMULA_RECOGNIZER=pix2text``.
"""

from __future__ import annotations

import abc
import io
from pathlib import Path
from typing import Any

from .layout import FORMULA, PageLayout


class FormulaRecognizer(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def recognize(self, image_bytes: bytes) -> str:
        """Recognize a single CROPPED formula image → LaTeX."""
        ...

    def detect_and_recognize(self, page_image_bytes: bytes) -> list[str] | None:
        """Self-detect every formula on a FULL page → LaTeX list (reading order).

        Only recognizers with a built-in math-formula detector (e.g. pix2text)
        override this; it lets them fill the 公式 section WITHOUT a pre-computed
        ``layout.json``. Returns None for crop-only recognizers (they need layout)."""
        return None


class NullFormulaRecognizer(FormulaRecognizer):
    name = "none"

    def recognize(self, image_bytes: bytes) -> str:
        return ""


class RapidLatexRecognizer(FormulaRecognizer):
    """RapidLaTeXOCR (ONNX). Optional dep, import-guarded."""

    name = "rapid"

    def __init__(self) -> None:
        try:
            from rapid_latex_ocr import LaTeXOCR
        except ImportError as exc:  # pragma: no cover - only without the dep
            raise RuntimeError(
                "rapid formula recognizer needs RapidLaTeXOCR: pip install rapid_latex_ocr "
                "(or `pip install -e 'packages/brain-visualmd[formula]'`)."
            ) from exc
        self._model = LaTeXOCR()

    def recognize(self, image_bytes: bytes) -> str:  # pragma: no cover - needs the dep
        out = self._model(image_bytes)
        res = out[0] if isinstance(out, tuple) else out
        return str(res or "").strip()


class Pix2TextRecognizer(FormulaRecognizer):
    """Pix2Text — MFD(公式检测) + MFR(LatexOCR). Chinese-aware, MIT.

    Replaces the Chinese-blind RapidLaTeXOCR. Because it ships a math-formula
    detector, it self-detects formulas on a whole page (``detect_and_recognize``)
    and fills the 公式 section even when no ``layout.json`` exists. Optional dep,
    import-guarded; models download on first use.
    """

    name = "pix2text"

    def __init__(self) -> None:
        try:
            from pix2text import LatexOCR, MathFormulaDetector
        except ImportError as exc:  # pragma: no cover - only without the dep
            raise RuntimeError(
                "pix2text formula recognizer needs Pix2Text: pip install pix2text "
                "(or `pip install -e 'packages/brain-visualmd[formula-zh]'`)."
            ) from exc
        self._LatexOCR = LatexOCR
        self._MFD = MathFormulaDetector
        self._mfr: Any = None
        self._mfd: Any = None

    def _ensure(self) -> None:  # pragma: no cover - downloads models, needs the dep
        if self._mfr is None:
            self._mfr = self._LatexOCR()
        if self._mfd is None:
            self._mfd = self._MFD()

    def recognize(self, image_bytes: bytes) -> str:  # pragma: no cover - needs the dep
        from PIL import Image

        self._ensure()
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
        return _first_latex(self._mfr(img))

    def detect_and_recognize(
        self, page_image_bytes: bytes
    ) -> list[str]:  # pragma: no cover - needs the dep
        from PIL import Image

        self._ensure()
        page = Image.open(io.BytesIO(page_image_bytes)).convert("RGB")
        dets = self._mfd.detect(page) or []
        out: list[str] = []
        for box in _sorted_boxes(dets, page.size):
            tex = _first_latex(self._mfr(page.crop(box)))
            if tex and not _looks_garbled(tex) and _is_real_formula(tex):
                out.append(tex)
        return out


_STRUCT_TOKENS = (
    "=",
    "\\frac",
    "\\sum",
    "\\int",
    "\\prod",
    "\\sqrt",
    "\\partial",
    "\\nabla",
    "\\times",
    "\\cdot",
    "\\approx",
    "\\propto",
    "\\rightarrow",
)


def _is_real_formula(tex: str) -> bool:
    """Keep substantive formulas; drop MFD's single-symbol "embedding" noise.

    Pix2Text's detector also boxes inline single variables (``e``, ``z_1``, ``α``,
    ``n``) — useless as standalone 公式 entries. Keep a detection only if it shows
    a relation/operator, or it's a genuinely long expression.
    """
    t = tex.strip()
    if len(t) < 5:
        return False
    return any(tok in t for tok in _STRUCT_TOKENS) or len(t) >= 16


def _first_latex(out) -> str:
    """Normalize LatexOCR's varied return (str / list / dict{'text'}) → one LaTeX str."""
    if isinstance(out, list):
        out = out[0] if out else ""
    if isinstance(out, dict):
        out = out.get("text", "")
    return str(out or "").strip()


def _sorted_boxes(dets: list, page_size) -> list:
    """MFD detections → (x0,y0,x1,y1) int boxes, reading order (top→bottom, left→right)."""
    w, h = page_size
    boxes = []
    for d in dets:
        raw = d.get("box") if isinstance(d, dict) else d
        if raw is None:
            continue
        xs = [float(p[0]) for p in raw]
        ys = [float(p[1]) for p in raw]
        x0, y0, x1, y1 = min(xs), min(ys), max(xs), max(ys)
        if x1 - x0 < 4 or y1 - y0 < 4:  # drop slivers
            continue
        boxes.append(
            (max(0, int(x0)), max(0, int(y0)), min(w, int(x1)), min(h, int(y1)))
        )
    boxes.sort(key=lambda b: (b[1], b[0]))
    return boxes


_RECOGNIZERS = {
    "none": NullFormulaRecognizer,
    "rapid": RapidLatexRecognizer,
    "pix2text": Pix2TextRecognizer,
}


def get_formula_recognizer(name: str) -> FormulaRecognizer:
    try:
        return _RECOGNIZERS[name]()
    except KeyError:
        raise KeyError(
            f"unknown formula recognizer {name!r}; available: {', '.join(_RECOGNIZERS)}"
        ) from None


def crop_png(image_path: Path, bbox: list[float]) -> bytes | None:
    """Crop ``bbox`` from the page PNG → PNG bytes. Needs Pillow; None if absent."""
    try:
        from PIL import Image
    except ImportError:
        return None
    box = (int(bbox[0]), int(bbox[1]), int(bbox[2]), int(bbox[3]))
    with Image.open(image_path) as img:
        crop = img.crop(box)
        buf = io.BytesIO()
        crop.save(buf, format="PNG")
        return buf.getvalue()


def recognize_page_formulas(
    image_path: Path | None,
    page_layout: PageLayout | None,
    recognizer: FormulaRecognizer,
) -> list[str]:
    """LaTeX for each formula region on the page, in reading order.

    Two paths: (a) layout-driven — crop the FORMULA regions from ``layout.json``
    and recognize each (any recognizer); (b) layout-free — when no ``layout.json``
    exists, let a self-detecting recognizer (pix2text) find + recognize formulas
    on the whole page. Crop-only recognizers (rapid) need (a).
    """
    if recognizer.name == "none" or image_path is None:
        return []
    if page_layout is None:  # layout-free: only self-detecting recognizers
        page_bytes = Path(image_path).read_bytes()
        found = recognizer.detect_and_recognize(page_bytes)
        if not found:
            return []
        return [t for t in found if t and not _looks_garbled(t)]
    regions = _dedup_formula_regions(
        sorted(
            (r for r in page_layout.regions if r.cls == FORMULA), key=lambda r: r.order
        )
    )
    latexes: list[str] = []
    for region in regions:
        png = crop_png(Path(image_path), region.bbox)
        if png is None:
            return []  # no Pillow -> can't crop; skip the whole pass
        tex = recognizer.recognize(png)
        if tex and not _looks_garbled(tex):
            latexes.append(tex)
    return latexes


def _area(r) -> float:
    return max(0.0, r.bbox[2] - r.bbox[0]) * max(0.0, r.bbox[3] - r.bbox[1])


def _dedup_formula_regions(regions: list) -> list:
    """Drop "merged" formula boxes that engulf tighter ones.

    PP-DocLayout sometimes emits a big box covering a formula PLUS an adjacent
    annotation, on top of the tight per-formula boxes. Cropping the big one drags
    in junk. A region is a parent (dropped) if it contains the center of another,
    smaller formula region; the tight leaves are kept.
    """

    def center(r):
        return ((r.bbox[0] + r.bbox[2]) / 2, (r.bbox[1] + r.bbox[3]) / 2)

    def contains_center(a, b) -> bool:
        cx, cy = center(b)
        return a.bbox[0] <= cx <= a.bbox[2] and a.bbox[1] <= cy <= a.bbox[3]

    kept = []
    for r in regions:
        is_parent = any(
            o is not r and _area(r) > _area(o) and contains_center(r, o)
            for o in regions
        )
        if not is_parent:
            kept.append(r)
    return kept


def _looks_garbled(latex: str) -> bool:
    """A loose layout bbox can drag adjacent text into a formula crop, producing a
    multi-cell ``\\begin{array}`` of junk. Drop those rather than emit garbage."""
    return (
        "begin{array}" in latex
        and latex.count("\\bar") + latex.count("\\overline") >= 3
    )
