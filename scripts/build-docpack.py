#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Build a deterministic v0.1 DocPack from Markdown/text, PDF, or Office inputs."""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import mimetypes
import re
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
VALIDATOR_PATH = ROOT / "scripts" / "validate-docpack.py"


TEXT_SUFFIXES = {".md", ".markdown", ".txt", ".text"}
PDF_SUFFIXES = {".pdf"}
OFFICE_SUFFIXES = {".doc", ".docx", ".ppt", ".pptx", ".xls", ".xlsx"}
PDFINFO_TIMEOUT_SECONDS = 30
PDF_PAGE_TIMEOUT_SECONDS = 60
OFFICE_CONVERSION_TIMEOUT_SECONDS = 120


@dataclass(frozen=True)
class PageRecord:
    page: int
    text: str
    image: str = ""
    render_status: str = "not_applicable"
    text_status: str = "ok"
    risks: tuple[str, ...] = ()


def _load_validator():
    spec = importlib.util.spec_from_file_location("validate_docpack", VALIDATOR_PATH)
    module = importlib.util.module_from_spec(spec)
    if spec.loader is None:
        raise RuntimeError(f"cannot load validator: {VALIDATOR_PATH}")
    spec.loader.exec_module(module)
    return module


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _slugify(value: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-._")
    return slug or "document"


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json(path: Path, data: dict[str, Any]):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _copy_source(source: Path, docpack: Path) -> str:
    target = docpack / "source" / f"original{source.suffix.lower()}"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target.relative_to(docpack).as_posix()


def _copy_display_pdf(source: Path, docpack: Path) -> Path:
    target = docpack / "display" / "display.pdf"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return target


def _media_type(source: Path) -> str:
    guessed, _encoding = mimetypes.guess_type(source.name)
    if guessed:
        return guessed
    if source.suffix.lower() in TEXT_SUFFIXES:
        return "text/markdown" if source.suffix.lower() in {".md", ".markdown"} else "text/plain"
    return "application/octet-stream"


def _text_status(text: str) -> tuple[str, tuple[str, ...]]:
    stripped = text.strip()
    if not stripped:
        return "low_text", ("no_extracted_text",)
    if len(stripped) < 20:
        return "low_text", ("low_text",)
    return "ok", ()


def _read_markdown_or_text(source: Path) -> list[PageRecord]:
    text = source.read_text(encoding="utf-8", errors="replace")
    status, risks = _text_status(text)
    return [
        PageRecord(
            page=1,
            text=text,
            render_status="not_applicable",
            text_status=status,
            risks=risks,
        )
    ]


def _require_tool(name: str) -> str:
    tool = shutil.which(name)
    if not tool:
        raise RuntimeError(f"{name} is required for PDF DocPack build")
    return tool


def _find_office_converter() -> str:
    for name in ("soffice", "libreoffice"):
        tool = shutil.which(name)
        if tool:
            return tool
    raise RuntimeError("LibreOffice is required for Office DocPack build")


def _pdf_page_count(source: Path, pdfinfo: str) -> int:
    # Force poppler to emit UTF-8 and decode it as UTF-8: the platform default
    # (GBK/cp936 on Windows) crashes the subprocess reader thread on Chinese
    # title metadata, leaving stdout as None.
    result = subprocess.run(
        [pdfinfo, "-enc", "UTF-8", str(source)],
        capture_output=True,
        check=True,
        timeout=PDFINFO_TIMEOUT_SECONDS,
        encoding="utf-8",
        errors="replace",
    )
    for line in (result.stdout or "").splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError("pdfinfo did not report page count")


def _pdf_page_text(source: Path, pdftotext: str, page: int) -> str:
    result = subprocess.run(
        [pdftotext, "-enc", "UTF-8", "-f", str(page), "-l", str(page), "-layout", str(source), "-"],
        capture_output=True,
        check=True,
        timeout=PDF_PAGE_TIMEOUT_SECONDS,
        encoding="utf-8",
        errors="replace",
    )
    return (result.stdout or "").strip()


def _render_pdf_page(source: Path, pdftoppm: str, docpack: Path, page: int) -> str:
    prefix = docpack / "pages" / f"page-{page:04d}"
    prefix.parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [pdftoppm, "-f", str(page), "-l", str(page), "-singlefile", "-png", str(source), str(prefix)],
        capture_output=True,
        check=True,
        timeout=PDF_PAGE_TIMEOUT_SECONDS,
        encoding="utf-8",
        errors="replace",
    )
    return f"pages/page-{page:04d}.png"


OCR_LANG = "chi_sim+eng"


def _ocr_page_image(docpack: Path, image_rel: str, lang: str = OCR_LANG) -> str:
    """OCR a rendered page PNG with tesseract; '' if tesseract/image unavailable or it fails."""
    tess = shutil.which("tesseract")
    if not tess or not image_rel:
        return ""
    img = docpack / image_rel
    if not img.is_file():
        return ""
    try:
        result = subprocess.run(
            [tess, str(img), "stdout", "-l", lang],
            capture_output=True,
            timeout=PDF_PAGE_TIMEOUT_SECONDS,
            encoding="utf-8",
            errors="replace",
        )
        return (result.stdout or "").strip()
    except (OSError, subprocess.SubprocessError):
        return ""


def _read_pdf(source: Path, docpack: Path, *, ocr: bool = False, ocr_max_pages: int = 0) -> list[PageRecord]:
    pdfinfo = _require_tool("pdfinfo")
    pdftotext = _require_tool("pdftotext")
    pdftoppm = _require_tool("pdftoppm")

    page_count = _pdf_page_count(source, pdfinfo)
    pages: list[PageRecord] = []
    for page in range(1, page_count + 1):
        risks: list[str] = []
        try:
            image = _render_pdf_page(source, pdftoppm, docpack, page)
            render_status = "ok"
        except subprocess.CalledProcessError as exc:
            image = ""
            render_status = "failed"
            risks.append(f"render_failed:{exc.returncode}")
        except subprocess.TimeoutExpired:
            image = ""
            render_status = "failed"
            risks.append("render_timeout")

        try:
            text = _pdf_page_text(source, pdftotext, page)
            text_status, text_risks = _text_status(text)
            risks.extend(text_risks)
        except subprocess.CalledProcessError as exc:
            text = ""
            text_status = "failed"
            risks.append(f"text_extract_failed:{exc.returncode}")
        except subprocess.TimeoutExpired:
            text = ""
            text_status = "failed"
            risks.append("text_extract_timeout")

        # OCR fallback: a scanned page has no text layer (low_text) but renders fine —
        # OCR the rendered PNG so the DocPack content.md is searchable. Opt-in (slow).
        if (ocr and (ocr_max_pages <= 0 or page <= ocr_max_pages)
                and text_status == "low_text" and render_status == "ok" and image):
            ocr_text = _ocr_page_image(docpack, image)
            if ocr_text.strip():
                text = ocr_text
                text_status, ocr_risks = _text_status(ocr_text)
                risks = [r for r in risks if r not in ("no_extracted_text", "low_text")]
                risks.extend(("ocr_fallback", *ocr_risks))

        pages.append(
            PageRecord(
                page=page,
                text=text,
                image=image,
                render_status=render_status,
                text_status=text_status,
                risks=tuple(risks),
            )
        )
    return pages


def _with_page_risks(pages: list[PageRecord], risks: tuple[str, ...]) -> list[PageRecord]:
    if not risks:
        return pages
    return [
        PageRecord(
            page=page.page,
            text=page.text,
            image=page.image,
            render_status=page.render_status,
            text_status=page.text_status,
            risks=tuple(dict.fromkeys((*page.risks, *risks))),
        )
        for page in pages
    ]


def _convert_office_to_display_pdf(source: Path, docpack: Path) -> Path:
    converter = _find_office_converter()
    display_dir = docpack / "display"
    display_dir.mkdir(parents=True, exist_ok=True)

    try:
        with tempfile.TemporaryDirectory(prefix="docpack-libreoffice-") as raw_profile:
            profile = Path(raw_profile).resolve()
            result = subprocess.run(
                [
                    converter,
                    "--headless",
                    f"-env:UserInstallation={profile.as_uri()}",
                    "--convert-to",
                    "pdf",
                    "--outdir",
                    str(display_dir),
                    str(source),
                ],
                capture_output=True,
                check=False,
                timeout=OFFICE_CONVERSION_TIMEOUT_SECONDS,
                encoding="utf-8",
                errors="replace",
            )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"LibreOffice conversion timed out after {OFFICE_CONVERSION_TIMEOUT_SECONDS}s") from exc
    if result.returncode != 0:
        detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise RuntimeError(f"LibreOffice conversion failed with exit {result.returncode}: {detail}")

    expected = display_dir / f"{source.stem}.pdf"
    candidates = sorted(display_dir.glob("*.pdf"), key=lambda path: path.stat().st_mtime, reverse=True)
    converted = expected if expected.exists() else (candidates[0] if candidates else None)
    if converted is None:
        detail = "\n".join(part for part in (result.stdout.strip(), result.stderr.strip()) if part)
        raise RuntimeError(f"LibreOffice conversion did not produce a PDF: {detail}")

    target = display_dir / "display.pdf"
    if converted != target:
        if target.exists():
            target.unlink()
        converted.replace(target)
    return target


def _page_content(source_name: str, pages: list[PageRecord]) -> str:
    parts = [
        f"# {source_name}",
        "",
        "> Generated DocPack content. Keep source, layout, verification, and citations as evidence.",
        "",
    ]
    for page in pages:
        parts.append(f"## Page {page.page}")
        if page.image:
            parts.append(f"![Page {page.page}]({page.image})")
            parts.append("")
        if page.risks:
            parts.append("Risks: " + ", ".join(page.risks))
            parts.append("")
        parts.append(page.text.strip() or "_No extracted text._")
        parts.append("")
    return "\n".join(parts).rstrip() + "\n"


def _build_layout(pages: list[PageRecord]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "pages": [
            {
                "page": page.page,
                "blocks": [
                    {
                        "block_id": f"p{page.page}-text",
                        "type": "text",
                        "page": page.page,
                        "text": page.text.strip(),
                        "confidence": 1.0 if page.text.strip() else 0.0,
                        "risks": list(page.risks),
                    }
                ],
            }
            for page in pages
        ],
    }


def _build_verify(source_hash: str, pages: list[PageRecord]) -> dict[str, Any]:
    all_risks = sorted({risk for page in pages for risk in page.risks})
    status = "needs_review" if all_risks else "ok"
    return {
        "schema_version": 1,
        "status": status,
        "source_sha256": source_hash,
        "risks": all_risks,
        "pages": [
            {
                "page": page.page,
                "image": page.image,
                "render_status": page.render_status,
                "text_status": page.text_status,
                "risks": list(page.risks),
            }
            for page in pages
        ],
    }


def _build_citations(source_rel: str, pages: list[PageRecord]) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "anchors": [
            {
                "anchor_id": f"p{page.page}",
                "target_type": "page",
                "source_path": source_rel,
                "page": page.page,
                "confidence": 1.0 if page.text.strip() else 0.5,
                "risks": list(page.risks),
            }
            for page in pages
        ],
    }


def _chunks(pages: list[PageRecord]) -> list[dict[str, Any]]:
    records = []
    for page in pages:
        text = page.text.strip() or "No extracted text."
        records.append(
            {
                "chunk_id": f"chunk-p{page.page}",
                "kind": "page",
                "text": text,
                "page_start": page.page,
                "page_end": page.page,
                "assets": [page.image] if page.image else [],
                "citations": [f"p{page.page}"],
                "confidence": 1.0 if page.text.strip() else 0.5,
                "risks": list(page.risks),
            }
        )
    return records


def _write_chunks(path: Path, records: list[dict[str, Any]]):
    with path.open("w", encoding="utf-8") as handle:
        for record in records:
            handle.write(json.dumps(record, ensure_ascii=False, sort_keys=True) + "\n")


def build_docpack(
    source: Path,
    out: Path,
    *,
    docpack_id: str = "",
    force: bool = False,
    validate: bool = True,
    ocr: bool = False,
    ocr_max_pages: int = 0,
) -> Path:
    source = source.resolve()
    out = out.resolve()
    if not source.exists():
        raise FileNotFoundError(source)
    if not source.is_file():
        raise IsADirectoryError(source)
    if out.exists() and any(out.iterdir()) and not force:
        raise FileExistsError(f"output directory is not empty: {out}")
    if force and out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True, exist_ok=True)
    (out / "display").mkdir(parents=True, exist_ok=True)
    (out / "pages").mkdir(parents=True, exist_ok=True)

    suffix = source.suffix.lower()
    display_pdf: Path | None = None
    if suffix in TEXT_SUFFIXES:
        pages = _read_markdown_or_text(source)
    elif suffix in PDF_SUFFIXES:
        display_pdf = _copy_display_pdf(source, out)
        pages = _read_pdf(display_pdf, out, ocr=ocr, ocr_max_pages=ocr_max_pages)
    elif suffix in OFFICE_SUFFIXES:
        display_pdf = _convert_office_to_display_pdf(source, out)
        pages = _with_page_risks(_read_pdf(display_pdf, out, ocr=ocr, ocr_max_pages=ocr_max_pages), ("converted_via_libreoffice",))
    else:
        raise RuntimeError(f"unsupported source type for v0.1: {source.suffix or source.name}")

    source_rel = _copy_source(source, out)
    source_hash = _sha256(source)
    doc_id = _slugify(docpack_id or source.stem)

    display = {
        "pages_dir": "pages",
        "page_count": len(pages),
    }
    if display_pdf is not None:
        display["display_pdf"] = display_pdf.relative_to(out).as_posix()

    manifest = {
        "schema_version": 1,
        "docpack_id": doc_id,
        "created_at": _utc_now(),
        "tool": {
            "name": "build-docpack.py",
            "version": "0.1",
        },
        "source": {
            "path": source_rel,
            "sha256": source_hash,
            "media_type": _media_type(source),
            "size_bytes": source.stat().st_size,
        },
        "display": display,
        "outputs": {
            "content_md": "content.md",
            "layout_json": "layout.json",
            "verify_json": "verify.json",
            "citations_json": "citations.json",
            "chunks_jsonl": "chunks.jsonl",
        },
        "risks": sorted({risk for page in pages for risk in page.risks}),
    }

    (out / "content.md").write_text(_page_content(source.name, pages), encoding="utf-8")
    _write_json(out / "manifest.json", manifest)
    _write_json(out / "layout.json", _build_layout(pages))
    _write_json(out / "verify.json", _build_verify(source_hash, pages))
    _write_json(out / "citations.json", _build_citations(source_rel, pages))
    _write_chunks(out / "chunks.jsonl", _chunks(pages))

    if validate:
        validator = _load_validator()
        errors = validator.validate_docpack(out)
        if errors:
            raise RuntimeError("generated DocPack failed validation:\n" + "\n".join(errors))
    return out


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Build a v0.1 DocPack from Markdown/text, PDF, or Office input.")
    parser.add_argument("source", type=Path, help="Source material file")
    parser.add_argument("--out", type=Path, required=True, help="Output <slug>.docpack directory")
    parser.add_argument("--docpack-id", default="", help="Stable DocPack id; defaults to source stem")
    parser.add_argument("--force", action="store_true", help="Replace existing output directory")
    parser.add_argument("--no-validate", action="store_true", help="Skip post-build DocPack validation")
    parser.add_argument("--ocr", action="store_true", help="OCR scanned pages (no text layer) with tesseract — slow")
    parser.add_argument("--ocr-max-pages", type=int, default=0, help="OCR at most N pages (0 = all); pages still render")
    args = parser.parse_args(argv)

    try:
        result = build_docpack(
            args.source,
            args.out,
            docpack_id=args.docpack_id,
            force=args.force,
            validate=not args.no_validate,
            ocr=args.ocr,
            ocr_max_pages=args.ocr_max_pages,
        )
    except Exception as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    print(f"DocPack built: {result}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
