# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import hashlib
import importlib.util
import json
import shutil
import sys
import zipfile
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
BUILDER = ROOT / "scripts" / "build-docpack.py"
VALIDATOR = ROOT / "scripts" / "validate-docpack.py"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_minimal_pdf(path: Path, text: str, *, title: str | None = None):
    escaped = text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(f'BT /F1 12 Tf 72 96 Td ({escaped}) Tj ET'.encode('ascii'))} >>\nstream\nBT /F1 12 Tf 72 96 Td ({escaped}) Tj ET\nendstream".encode("ascii"),
    ]
    info_obj_num: int | None = None
    if title is not None:
        # PDF text strings hold non-ASCII as UTF-16BE with a byte-order mark,
        # encoded here as a hex string so pdfinfo reports a Chinese /Title.
        title_hex = (b"\xfe\xff" + title.encode("utf-16-be")).hex().upper()
        objects.append(f"<< /Title <{title_hex}> >>".encode("ascii"))
        info_obj_num = len(objects)
    chunks = [b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n"]
    offsets = [0]
    for index, obj in enumerate(objects, start=1):
        offsets.append(sum(len(chunk) for chunk in chunks))
        chunks.append(f"{index} 0 obj\n".encode("ascii"))
        chunks.append(obj)
        chunks.append(b"\nendobj\n")
    xref_offset = sum(len(chunk) for chunk in chunks)
    chunks.append(f"xref\n0 {len(objects) + 1}\n".encode("ascii"))
    chunks.append(b"0000000000 65535 f \n")
    for offset in offsets[1:]:
        chunks.append(f"{offset:010d} 00000 n \n".encode("ascii"))
    info_ref = f" /Info {info_obj_num} 0 R" if info_obj_num is not None else ""
    chunks.append(
        f"trailer\n<< /Size {len(objects) + 1} /Root 1 0 R{info_ref} >>\nstartxref\n{xref_offset}\n%%EOF\n".encode("ascii")
    )
    path.write_bytes(b"".join(chunks))


def _write_minimal_docx(path: Path, text: str):
    escaped = (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )
    files = {
        "[Content_Types].xml": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
  <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
  <Default Extension="xml" ContentType="application/xml"/>
  <Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>
""",
        "_rels/.rels": """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
  <Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>
</Relationships>
""",
        "word/document.xml": f"""<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p>
      <w:r>
        <w:t>{escaped}</w:t>
      </w:r>
    </w:p>
    <w:sectPr>
      <w:pgSz w:w="11906" w:h="16838"/>
      <w:pgMar w:top="1440" w:right="1440" w:bottom="1440" w:left="1440" w:header="720" w:footer="720" w:gutter="0"/>
    </w:sectPr>
  </w:body>
</w:document>
""",
    }
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)


def test_build_markdown_docpack_and_validate(tmp_path):
    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    source = tmp_path / "lesson.md"
    source.write_text("# Lesson\n\nA stable learning note for testing.\n", encoding="utf-8")
    out = tmp_path / "lesson.docpack"

    result = builder.build_docpack(source, out, docpack_id="lesson-test")

    assert result == out.resolve()
    assert source.exists()
    assert validator.validate_docpack(out) == []

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["docpack_id"] == "lesson-test"
    assert manifest["source"]["sha256"] == _sha256(source)
    assert manifest["outputs"]["layout_json"] == "layout.json"

    content = (out / "content.md").read_text(encoding="utf-8")
    assert "# lesson.md" in content
    assert "A stable learning note" in content

    chunk = json.loads((out / "chunks.jsonl").read_text(encoding="utf-8"))
    assert chunk["citations"] == ["p1"]


def test_build_pdf_docpack_with_page_image_and_text(tmp_path):
    if not all(shutil.which(tool) for tool in ("pdfinfo", "pdftotext", "pdftoppm")):
        raise AssertionError("Poppler tools are required for this PDF builder test")

    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    source = tmp_path / "lesson.pdf"
    _write_minimal_pdf(source, "DocPack PDF smoke text for page extraction.")
    out = tmp_path / "lesson-pdf.docpack"

    builder.build_docpack(source, out, docpack_id="lesson-pdf")

    assert validator.validate_docpack(out) == []
    assert (out / "display" / "display.pdf").exists()
    assert (out / "pages" / "page-0001.png").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    verify = json.loads((out / "verify.json").read_text(encoding="utf-8"))
    chunk = json.loads((out / "chunks.jsonl").read_text(encoding="utf-8"))

    assert manifest["display"]["page_count"] == 1
    assert manifest["display"]["display_pdf"] == "display/display.pdf"
    assert verify["pages"][0]["render_status"] == "ok"
    assert verify["pages"][0]["text_status"] == "ok"
    assert "DocPack PDF smoke text" in chunk["text"]
    assert chunk["assets"] == ["pages/page-0001.png"]


def test_build_pdf_docpack_with_chinese_filename_and_title(tmp_path):
    if not all(shutil.which(tool) for tool in ("pdfinfo", "pdftotext", "pdftoppm")):
        raise AssertionError("Poppler tools are required for this PDF builder test")

    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    # Chinese filename + Chinese /Title metadata make pdfinfo emit UTF-8 bytes
    # that the Windows default (GBK/cp936) decoder cannot read; that used to
    # crash the subprocess reader thread and blow up _pdf_page_count.
    source = tmp_path / "中文讲义-测试.pdf"
    _write_minimal_pdf(source, "DocPack PDF smoke text for page extraction.", title="中文标题：传热学讲义")
    out = tmp_path / "中文.docpack"

    builder.build_docpack(source, out, docpack_id="cn-pdf")

    assert validator.validate_docpack(out) == []
    assert (out / "display" / "display.pdf").exists()
    assert (out / "pages" / "page-0001.png").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    chunk = json.loads((out / "chunks.jsonl").read_text(encoding="utf-8"))
    assert manifest["display"]["page_count"] == 1
    assert "DocPack PDF smoke text" in chunk["text"]


def test_pdf_subprocess_calls_force_utf8_decoding(tmp_path, monkeypatch):
    # Locale-independent guard: the GBK crash is Windows-only, so pin the fix by
    # asserting every poppler call decodes stdout as UTF-8 with replacement,
    # regardless of the host locale running the suite.
    builder = _load_module("build_docpack", BUILDER)

    calls: list[dict] = []

    class _Result:
        returncode = 0
        stderr = ""

        def __init__(self, stdout: str):
            self.stdout = stdout

    def fake_run(command, **kwargs):
        calls.append(kwargs)
        return _Result("Pages:          1\n")

    monkeypatch.setattr(builder.subprocess, "run", fake_run)

    pdf = tmp_path / "doc.pdf"
    assert builder._pdf_page_count(pdf, "pdfinfo") == 1
    builder._pdf_page_text(pdf, "pdftotext", 1)
    builder._render_pdf_page(pdf, "pdftoppm", tmp_path, 1)

    assert len(calls) == 3
    for kwargs in calls:
        assert kwargs.get("encoding") == "utf-8"
        assert kwargs.get("errors") == "replace"


def test_build_docx_docpack_via_libreoffice(tmp_path):
    if not (shutil.which("soffice") or shutil.which("libreoffice")):
        pytest.skip("LibreOffice is required for Office DocPack build")
    if not all(shutil.which(tool) for tool in ("pdfinfo", "pdftotext", "pdftoppm")):
        raise AssertionError("Poppler tools are required for this Office builder test")

    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    source = tmp_path / "lesson.docx"
    _write_minimal_docx(source, "DocPack DOCX smoke text for LibreOffice conversion.")
    out = tmp_path / "lesson-docx.docpack"

    builder.build_docpack(source, out, docpack_id="lesson-docx")

    assert validator.validate_docpack(out) == []
    assert (out / "source" / "original.docx").exists()
    assert (out / "display" / "display.pdf").exists()
    assert (out / "pages" / "page-0001.png").exists()

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    verify = json.loads((out / "verify.json").read_text(encoding="utf-8"))
    content = (out / "content.md").read_text(encoding="utf-8")

    assert manifest["display"]["display_pdf"] == "display/display.pdf"
    assert "converted_via_libreoffice" in manifest["risks"]
    assert verify["status"] == "needs_review"
    assert "converted_via_libreoffice" in verify["pages"][0]["risks"]
    assert "DocPack DOCX smoke text" in content


def test_build_refuses_nonempty_output_without_force(tmp_path):
    builder = _load_module("build_docpack", BUILDER)
    source = tmp_path / "lesson.md"
    source.write_text("# Lesson\n\nA stable learning note for testing.\n", encoding="utf-8")
    out = tmp_path / "lesson.docpack"
    out.mkdir()
    (out / "keep.txt").write_text("existing", encoding="utf-8")

    try:
        builder.build_docpack(source, out)
    except FileExistsError as exc:
        assert "not empty" in str(exc)
    else:
        raise AssertionError("expected FileExistsError")


def test_build_force_replaces_output(tmp_path):
    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    source = tmp_path / "lesson.md"
    source.write_text("# Lesson\n\nA stable learning note for testing.\n", encoding="utf-8")
    out = tmp_path / "lesson.docpack"
    out.mkdir()
    (out / "old.txt").write_text("old", encoding="utf-8")

    builder.build_docpack(source, out, force=True)

    assert not (out / "old.txt").exists()
    assert validator.validate_docpack(out) == []


def test_cli_builds_docpack(tmp_path):
    builder = _load_module("build_docpack", BUILDER)
    validator = _load_module("validate_docpack", VALIDATOR)
    source = tmp_path / "note.txt"
    source.write_text("A plain text learning note for DocPack testing.", encoding="utf-8")
    out = tmp_path / "note.docpack"

    assert builder.main([str(source), "--out", str(out), "--docpack-id", "plain-note"]) == 0
    assert validator.validate_docpack(out) == []
