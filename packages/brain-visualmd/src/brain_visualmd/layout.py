# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Pre-analysis: detect page layout regions (formula / table / figure / text + order).

A fast CPU layout pass before transcription. The detector emits, per page,
``{cls, bbox, score, order}`` regions. This is the foundation for (a) routing —
send formula/table crops to specialists, give the doc model structured input —
and (b) a smarter escalation signal (a page WITH formula/table regions is the
one that needs the strong model), instead of guessing from the output text.

Detectors are pluggable and the real ones are OPTIONAL deps (import-guarded):
- ``paddle``  — PP-DocLayout (PaddleOCR, Apache-2.0): ~20 Chinese-doc classes +
  reading order, ~0.5s/page CPU. ``pip install paddleocr`` (extra: ``layout``).
- ``none``    — no detector (empty); the pipeline still works, just unrouted.

See ``docs/brain-visualmd-tools.zh-CN.md`` §0.5 and ``docs/brain-visualmd-module.zh-CN.md`` §6c.
"""

from __future__ import annotations

import abc
import dataclasses
import json
from dataclasses import dataclass, field
from pathlib import Path

# Normalized region classes we care about (detector labels are mapped onto these).
FORMULA = "formula"
TABLE = "table"
FIGURE = "figure"
TEXT = "text"
TITLE = "title"

_PADDLE_LABEL_MAP = {
    "formula": FORMULA,
    "formula_number": FORMULA,
    "isolate_formula": FORMULA,
    "table": TABLE,
    "table_caption": TABLE,
    "table_title": TABLE,
    "figure": FIGURE,
    "image": FIGURE,
    "chart": FIGURE,
    "figure_caption": FIGURE,
    "text": TEXT,
    "paragraph_title": TITLE,
    "doc_title": TITLE,
    "title": TITLE,
}


@dataclass
class LayoutRegion:
    page_no: int
    cls: str
    bbox: list[float]  # [x0, y0, x1, y1]
    score: float = 0.0
    order: int = 0

    def to_dict(self) -> dict:
        return dataclasses.asdict(self)


@dataclass
class PageLayout:
    page_no: int
    regions: list[LayoutRegion] = field(default_factory=list)

    @property
    def has_formula(self) -> bool:
        return any(r.cls == FORMULA for r in self.regions)

    @property
    def counts(self) -> dict:
        out: dict[str, int] = {}
        for r in self.regions:
            out[r.cls] = out.get(r.cls, 0) + 1
        return out


class LayoutDetector(abc.ABC):
    name = "base"

    @abc.abstractmethod
    def detect(self, image_path: Path, page_no: int) -> list[LayoutRegion]: ...


class NullLayoutDetector(LayoutDetector):
    name = "none"

    def detect(self, image_path: Path, page_no: int) -> list[LayoutRegion]:
        return []


class PaddleLayoutDetector(LayoutDetector):
    """PP-DocLayout via PaddleOCR (optional dep). CPU by default."""

    name = "paddle"

    def __init__(
        self, model_name: str = "PP-DocLayout_plus-L", device: str = "cpu"
    ) -> None:
        try:
            from paddleocr import LayoutDetection
        except ImportError as exc:  # pragma: no cover - exercised only with the dep
            raise RuntimeError(
                "paddle layout detector needs PaddleOCR: pip install paddleocr "
                "(or `pip install -e 'packages/brain-visualmd[layout]'`)."
            ) from exc
        self._model = LayoutDetection(model_name=model_name, device=device)

    def detect(
        self, image_path: Path, page_no: int
    ) -> list[LayoutRegion]:  # pragma: no cover
        results = self._model.predict(str(image_path), layout_nms=True)
        regions: list[LayoutRegion] = []
        for res in results:
            boxes = res["boxes"] if "boxes" in res else getattr(res, "boxes", [])
            for order, box in enumerate(boxes):
                label = str(box.get("label", "")).lower()
                regions.append(
                    LayoutRegion(
                        page_no=page_no,
                        cls=_PADDLE_LABEL_MAP.get(label, TEXT),
                        bbox=[float(x) for x in box.get("coordinate", [0, 0, 0, 0])],
                        score=float(box.get("score", 0.0)),
                        order=order,
                    )
                )
        return regions


_DETECTORS = {
    "none": NullLayoutDetector,
    "paddle": PaddleLayoutDetector,
}


def get_detector(name: str) -> LayoutDetector:
    try:
        return _DETECTORS[name]()
    except KeyError:
        raise KeyError(
            f"unknown layout detector {name!r}; available: {', '.join(_DETECTORS)}"
        ) from None


def analyze_docpack(docpack_dir: Path, detector: LayoutDetector, pages: int) -> Path:
    """Detect layout for every rendered page; write ``layout.json``."""
    docpack_dir = Path(docpack_dir)
    images = docpack_dir / "images"
    page_layouts: list[PageLayout] = []
    for page_no in range(1, pages + 1):
        img = images / f"p-{page_no:03d}.png"
        regions = detector.detect(img, page_no) if img.exists() else []
        page_layouts.append(PageLayout(page_no=page_no, regions=regions))
    payload = {
        "detector": detector.name,
        "pages": [
            {
                "page_no": pl.page_no,
                "counts": pl.counts,
                "regions": [r.to_dict() for r in pl.regions],
            }
            for pl in page_layouts
        ],
    }
    out = docpack_dir / "layout.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def load_layout(docpack_dir: Path) -> dict[int, PageLayout]:
    """Read ``layout.json`` into ``{page_no: PageLayout}`` (empty if absent)."""
    f = Path(docpack_dir) / "layout.json"
    if not f.exists():
        return {}
    data = json.loads(f.read_text("utf-8"))
    out: dict[int, PageLayout] = {}
    for page in data.get("pages", []):
        regions = [
            LayoutRegion(
                page_no=int(page["page_no"]),
                cls=r["cls"],
                bbox=list(r.get("bbox", [])),
                score=float(r.get("score", 0.0)),
                order=int(r.get("order", 0)),
            )
            for r in page.get("regions", [])
        ]
        out[int(page["page_no"])] = PageLayout(
            page_no=int(page["page_no"]), regions=regions
        )
    return out
