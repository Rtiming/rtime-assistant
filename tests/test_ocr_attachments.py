# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# -*- coding: utf-8 -*-
"""ocr_attachments 离线单测：分类/OCR判定/旁车命名/渲染/process_file 路由(打桩抽取)。"""

import importlib.util
import os
from pathlib import Path

_SPEC = importlib.util.spec_from_file_location(
    "ocr_attachments",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "ocr_attachments.py"),
)
ocr = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(ocr)


def test_classify():
    assert ocr.classify("pdf") == "doc"
    assert ocr.classify(".DOCX") == "doc"
    assert ocr.classify("jpg") == "image"
    assert ocr.classify("txt") is None
    assert ocr.classify("md") is None


def test_needs_ocr_and_useful():
    assert ocr.needs_ocr("") is True
    assert ocr.needs_ocr("x" * 10) is True
    assert ocr.needs_ocr("正文" * 100) is False
    assert ocr.is_useful("短") is False
    assert ocr.is_useful("这是一段足够长的有效正文内容用于测试判定") is True


def test_companion_path():
    assert ocr.companion_path(Path("/a/b/审批表.pdf")) == Path("/a/b/审批表.pdf.md")


def test_render_companion_has_frontmatter_and_body():
    md = ocr.render_companion("通知.pdf", "pdf", "正文内容XYZ", ocr=True)
    assert md.startswith("---")
    assert "type: ustc-attachment-text" in md
    assert "source_file: 通知.pdf" in md
    assert "ocr: true" in md
    assert "# 通知.pdf" in md
    assert "正文内容XYZ" in md


def test_render_companion_uses_real_title_over_uuid():
    md = ocr.render_companion(
        "9f3a-uuid.pdf", "pdf", "正文", ocr=False, title="本科生奖学金申请表"
    )
    # uuid 文件名仍记在 source_file，但标题/正标题用可读真标题
    assert "source_file: 9f3a-uuid.pdf" in md
    assert "title: 本科生奖学金申请表" in md
    assert "# 本科生奖学金申请表" in md


def test_load_titles_maps_local_path(tmp_path):
    import json
    idx = tmp_path / "files_index.jsonl"
    idx.write_text(
        "\n".join([
            json.dumps({"local_path": "/b/x.pdf", "title": "审批表"}, ensure_ascii=False),
            json.dumps({"local_path": "/b/y.doc", "title": "通知"}, ensure_ascii=False),
            "",
        ]),
        encoding="utf-8",
    )
    titles = ocr.load_titles(str(idx))
    assert titles == {"/b/x.pdf": "审批表", "/b/y.doc": "通知"}
    assert ocr.load_titles(None) == {}


def test_process_file_skips_type_and_image(tmp_path):
    (tmp_path / "a.txt").write_text("x", encoding="utf-8")
    assert ocr.process_file(tmp_path / "a.txt", do_images=False, min_image_kb=80,
                            force=False) == "skip-type"
    img = tmp_path / "p.jpg"
    img.write_bytes(b"\xff" * 1000)
    assert ocr.process_file(img, do_images=False, min_image_kb=80,
                            force=False) == "skip-image"


def test_process_file_writes_companion(tmp_path, monkeypatch):
    pdf = tmp_path / "doc.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(ocr, "extract_pdf", lambda p: ("这是抽取出来的足够长的正文内容用于单元测试判定逻辑", False))
    st = ocr.process_file(pdf, do_images=False, min_image_kb=80, force=False)
    assert st == "written"
    comp = tmp_path / "doc.pdf.md"
    assert comp.exists() and "抽取出来" in comp.read_text(encoding="utf-8")
    # 增量：再次处理应跳过(旁车更新)
    assert ocr.process_file(pdf, do_images=False, min_image_kb=80,
                            force=False) == "skip-exists"


def test_process_file_skips_empty(tmp_path, monkeypatch):
    pdf = tmp_path / "blank.pdf"
    pdf.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(ocr, "extract_pdf", lambda p: ("", False))
    assert ocr.process_file(pdf, do_images=False, min_image_kb=80,
                            force=False) == "skip-empty"
