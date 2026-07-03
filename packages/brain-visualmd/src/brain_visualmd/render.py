# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Deterministic rendering: source -> page PNGs + page count + source hash.

Uses Poppler (``pdfinfo`` / ``pdftoppm``) and, for Office inputs, LibreOffice
(``soffice``) — the same tool contract as ``brain-docpack``. This module does
not reimplement parsing; it is a thin, dependency-free wrapper so the standalone
package can render without importing docpack internals. If a shared renderer is
later extracted from docpack, swap the body of :func:`render_source` for it.
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
import tempfile
from pathlib import Path

from .spec import page_image_name

PDF_SUFFIXES = {".pdf"}
OFFICE_SUFFIXES = {".ppt", ".pptx", ".doc", ".docx", ".xls", ".xlsx", ".odp", ".odt"}
IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp", ".gif", ".tif", ".tiff", ".bmp"}


class RenderError(RuntimeError):
    pass


def have(tool: str) -> bool:
    return shutil.which(tool) is not None


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def pdf_page_count(pdf: Path) -> int:
    if not have("pdfinfo"):
        raise RenderError("pdfinfo not found (install poppler)")
    out = subprocess.run(
        ["pdfinfo", str(pdf)], capture_output=True, text=True, check=True
    ).stdout
    for line in out.splitlines():
        if line.lower().startswith("pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RenderError(f"could not read page count from pdfinfo for {pdf}")


def _soffice_to_pdf(src: Path, out_dir: Path) -> Path:
    if not have("soffice"):
        raise RenderError("soffice not found (install LibreOffice) for Office input")
    subprocess.run(
        [
            "soffice",
            "--headless",
            "--convert-to",
            "pdf",
            "--outdir",
            str(out_dir),
            str(src),
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    pdf = out_dir / (src.stem + ".pdf")
    if not pdf.exists():
        raise RenderError(f"soffice did not produce {pdf}")
    return pdf


def _render_pdf_pages(pdf: Path, images_dir: Path, dpi: int) -> int:
    if not have("pdftoppm"):
        raise RenderError("pdftoppm not found (install poppler)")
    images_dir.mkdir(parents=True, exist_ok=True)
    # pdftoppm writes <prefix>-<n>.png; we then normalize names to p-NNN.png.
    prefix = images_dir / "raw"
    subprocess.run(
        ["pdftoppm", "-png", "-r", str(dpi), str(pdf), str(prefix)],
        capture_output=True,
        text=True,
        check=True,
    )
    raw = sorted(images_dir.glob("raw-*.png"))
    if not raw:
        raise RenderError(f"pdftoppm produced no pages for {pdf}")
    for i, src_png in enumerate(raw, start=1):
        src_png.rename(images_dir / page_image_name(i))
    return len(raw)


def render_source(source: Path, docpack_dir: Path, *, dpi: int = 150) -> dict:
    """Render ``source`` into ``docpack_dir/images`` and return scaffold facts.

    Returns ``{"pages": int, "source_sha256": str, "kind": str}``. Supports PDF,
    Office (via soffice), and single images. Never deletes the source.
    """
    source = source.resolve()
    if not source.exists():
        raise RenderError(f"source not found: {source}")
    images_dir = docpack_dir / "images"
    images_dir.mkdir(parents=True, exist_ok=True)
    suffix = source.suffix.lower()
    sha = sha256_file(source)

    if suffix in IMAGE_SUFFIXES:
        shutil.copyfile(source, images_dir / page_image_name(1))
        return {"pages": 1, "source_sha256": sha, "kind": "image"}

    if suffix in PDF_SUFFIXES:
        pages = _render_pdf_pages(source, images_dir, dpi)
        declared = pdf_page_count(source)
        if declared != pages:
            raise RenderError(f"page mismatch: pdfinfo={declared} rendered={pages}")
        return {"pages": pages, "source_sha256": sha, "kind": "pdf"}

    if suffix in OFFICE_SUFFIXES:
        with tempfile.TemporaryDirectory() as tmp:
            pdf = _soffice_to_pdf(source, Path(tmp))
            pages = _render_pdf_pages(pdf, images_dir, dpi)
        return {"pages": pages, "source_sha256": sha, "kind": "office"}

    raise RenderError(f"unsupported source type: {suffix}")


def doctor() -> dict:
    """Report availability of the deterministic toolchain."""
    return {
        tool: have(tool) for tool in ("pdfinfo", "pdftoppm", "soffice", "tesseract")
    }
