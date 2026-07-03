# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json

from brain_visualmd.layout import (
    FORMULA,
    TEXT,
    LayoutDetector,
    LayoutRegion,
    NullLayoutDetector,
    analyze_docpack,
    get_detector,
    load_layout,
)


def _docpack(tmp_path, pages=2):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    for i in range(1, pages + 1):
        (d / "images" / f"p-{i:03d}.png").write_bytes(b"\x89PNG")
    return d


class _MockDetector(LayoutDetector):
    name = "mock"

    def detect(self, image_path, page_no):
        cls = FORMULA if page_no == 1 else TEXT
        return [
            LayoutRegion(
                page_no=page_no, cls=cls, bbox=[0, 0, 10, 10], score=0.9, order=0
            )
        ]


def test_analyze_writes_layout_json(tmp_path):
    d = _docpack(tmp_path, 2)
    path = analyze_docpack(d, _MockDetector(), 2)
    data = json.loads(path.read_text("utf-8"))
    assert data["detector"] == "mock"
    assert data["pages"][0]["counts"].get("formula") == 1
    assert data["pages"][1]["counts"].get("text") == 1


def test_load_layout_roundtrip(tmp_path):
    d = _docpack(tmp_path, 2)
    analyze_docpack(d, _MockDetector(), 2)
    layout = load_layout(d)
    assert layout[1].has_formula is True
    assert layout[2].has_formula is False


def test_load_layout_absent_is_empty(tmp_path):
    assert load_layout(tmp_path) == {}


def test_get_detector():
    assert isinstance(get_detector("none"), NullLayoutDetector)
    assert get_detector("none").detect(None, 1) == []


def test_null_detector_pipeline_safe(tmp_path):
    d = _docpack(tmp_path, 1)
    path = analyze_docpack(d, NullLayoutDetector(), 1)
    data = json.loads(path.read_text("utf-8"))
    assert (
        data["pages"][0]["regions"] == []
    )  # no detector -> empty, pipeline still fine
