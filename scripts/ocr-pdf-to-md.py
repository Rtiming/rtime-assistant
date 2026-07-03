#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""OCR 无文本层/扫描 PDF → 课程 Markdown(检索辅助)。

pdftoppm 渲染页面图 + tesseract(chi_sim+eng)逐页 OCR。仅作全文检索辅助:
OCR 对公式/手写/版式不可靠,原 PDF 仍是真值;md frontmatter 标 untrusted。

用法:
  python ocr-pdf-to-md.py <pdf> --course <slug> [--source-rel R] [--out md]
      [--max-pages N] [--dpi 200] [--lang chi_sim+eng]
"""
from __future__ import annotations
import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path


def render_and_ocr(pdf: Path, max_pages: int, dpi: int, lang: str) -> list[tuple[int, str]]:
    pages: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory() as td:
        cmd = ["pdftoppm", "-r", str(dpi), "-png"]
        if max_pages:
            cmd += ["-l", str(max_pages)]
        cmd += [str(pdf), os.path.join(td, "p")]
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        imgs = sorted(Path(td).glob("p*.png"))
        for i, img in enumerate(imgs, 1):
            r = subprocess.run(
                ["tesseract", str(img), "stdout", "-l", lang],
                capture_output=True, text=True, encoding="utf-8", errors="ignore",
            )
            txt = (r.stdout or "").strip()
            if txt:
                pages.append((i, txt))
    return pages


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("pdf")
    ap.add_argument("--course", default="")
    ap.add_argument("--source-rel", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--max-pages", type=int, default=0)
    ap.add_argument("--dpi", type=int, default=200)
    ap.add_argument("--lang", default="chi_sim+eng")
    args = ap.parse_args()
    pdf = Path(args.pdf)
    pages = render_and_ocr(pdf, args.max_pages, args.dpi, args.lang)
    title = pdf.stem
    fm = (
        "---\n"
        "type: course-pdf-ocr-text\n"
        f'title: "{title}"\n'
        f"course: {args.course}\n"
        f'source: "{args.source_rel or pdf.name}"\n'
        "md_strategy: tesseract-ocr\n"
        f"ocr_lang: {args.lang}\n"
        "status: ocr-extracted-untrusted\n"
        "generated_by: ocr-pdf-to-md.py\n"
        f"pages_ocred: {len(pages)}\n"
        "tags: [course/" + args.course + ", course-pdf-ocr-text, search-aid]\n"
        "---\n\n"
        f"# {title}\n\n"
        "> OCR 文本,公式/手写/版式不可靠,以原 PDF 为准。仅作检索辅助。\n\n"
    )
    body = "\n\n".join(f"## 第{i}页\n\n{t}" for i, t in pages)
    out = fm + body
    sys.stdout.reconfigure(encoding="utf-8")
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        print(f"wrote {args.out} ({len(pages)} pages OCR'd)")
    else:
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
