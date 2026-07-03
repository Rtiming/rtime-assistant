#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""无依赖抽取 pptx/docx/xlsx 文本为课程 Markdown（搜索辅助）。

本机无 soffice/python-pptx/pymupdf 时的兜底：Office 文件本质是 zip+XML，
直接解析 <a:t>(ppt)/<w:t>(doc)/sharedStrings(xls) 取文本。产出带 frontmatter
的 md，供 brain 全文检索(brain-library)纳入。不替代原件、不保证版式。

用法:
  python extract-office-text.py <file.pptx|docx|xlsx> --course <slug> \
      [--source-rel <brain内相对路径>] [--out <md路径>]
不带 --out 时打印到 stdout。
"""
from __future__ import annotations
import argparse
import html
import re
import sys
import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET


def _texts(xml: bytes, tag: str) -> list[str]:
    out: list[str] = []
    try:
        root = ET.fromstring(xml)
    except Exception:
        for m in re.finditer(r"<[\w]*:?t[ >](.*?)</[\w]*:?t>", xml.decode("utf-8", "ignore"), re.S):
            out.append(html.unescape(re.sub("<.*?>", "", m.group(1))))
        return out
    for el in root.iter():
        if el.tag.split("}")[-1] == tag and el.text:
            out.append(el.text)
    return out


def extract(path: Path) -> tuple[str, str]:
    """returns (kind, body_markdown)"""
    z = zipfile.ZipFile(path)
    names = z.namelist()
    buf: list[str] = []
    if any(n.startswith("ppt/slides/slide") for n in names):
        slides = sorted(
            [n for n in names if re.match(r"ppt/slides/slide\d+\.xml$", n)],
            key=lambda s: int(re.search(r"(\d+)", s).group(1)),
        )
        for i, s in enumerate(slides, 1):
            t = [x.strip() for x in _texts(z.read(s), "t") if x.strip()]
            if t:
                buf.append(f"## Slide {i}\n\n" + " ｜ ".join(t))
        return "pptx", "\n\n".join(buf)
    if "word/document.xml" in names:
        t = [x.strip() for x in _texts(z.read("word/document.xml"), "t") if x.strip()]
        return "docx", "\n".join(t)
    if "xl/sharedStrings.xml" in names:
        t = [x.strip() for x in _texts(z.read("xl/sharedStrings.xml"), "t") if x.strip()]
        return "xlsx", " ｜ ".join(t)
    return "unknown", "(无法识别的 Office 结构)"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("--course", default="")
    ap.add_argument("--source-rel", default="")
    ap.add_argument("--out", default="")
    args = ap.parse_args()
    src = Path(args.file)
    kind, body = extract(src)
    title = src.stem
    fm = (
        "---\n"
        f'type: course-office-text\n'
        f'title: "{title}"\n'
        f"course: {args.course}\n"
        f'source: "{args.source_rel or src.name}"\n'
        f"office_kind: {kind}\n"
        "md_strategy: zip-xml-text-extract\n"
        "status: raw-extracted-untrusted\n"
        "generated_by: extract-office-text.py\n"
        "tags: [course/" + args.course + ", course-office-text, search-aid]\n"
        "---\n\n"
        f"# {title}\n\n"
        "> 无依赖 zip/XML 抽取的文本，仅作检索辅助；版式/公式/图片以原件为准。\n\n"
    )
    out = fm + (body or "")
    if args.out:
        p = Path(args.out)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(out, encoding="utf-8")
        sys.stdout.reconfigure(encoding="utf-8")
        print(f"wrote {args.out} ({len(body)} chars, {kind})")
    else:
        sys.stdout.reconfigure(encoding="utf-8")
        print(out)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
