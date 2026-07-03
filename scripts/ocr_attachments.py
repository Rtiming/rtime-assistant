#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""附件文本抽取/OCR -> 旁车 .md，让 brain 检索能命中文件内容。

brain-library 索引只吃 md/txt/bib/csl，PDF/Office/图片正文搜不到。本工具为每个
附件生成同目录旁车 `<file>.md`(带 frontmatter + 抽取文本)，indexer 自然收录，
于是 lib.search 能搜到附件内容、命中后顺藤摸到原文件。是 DocPack(L3 精处理)之外
的轻量批量补充。

抽取路线(全用 runtime 主机已装的 CLI):
  pdf   -> pdftotext;文本过少(扫描件)-> ocrmypdf --sidecar OCR(chi_sim+eng)
  office-> docx 用 pandoc;doc/ppt/pptx/xls/xlsx 用 soffice 转 txt
  image -> tesseract(chi_sim+eng);默认跳过(多为装饰图/照片),--images 且过尺寸阈值才做

增量:旁车已存在且比源新则跳过(--force 重做)。低优先级建议外面 nice 调用。

用法:
  python3 scripts/ocr_attachments.py --root /mnt/brain/knowledge/institutions/ustc/sources/files
  python3 scripts/ocr_attachments.py --root <dir> --images --min-image-kb 80 --limit 50
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path

DOC_SUFFIXES = {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx"}
IMAGE_SUFFIXES = {"png", "jpg", "jpeg", "tif", "tiff", "bmp"}
MIN_USEFUL_CHARS = 20  # 抽取文本短于此视为无内容，不写旁车
PDF_SCANNED_MAX = 80  # pdftotext 文本短于此(可能扫描件)→ 转 OCR
OCR_LANG = "chi_sim+eng"


def classify(suffix: str) -> str | None:
    s = suffix.lower().lstrip(".")
    if s in DOC_SUFFIXES:
        return "doc"
    if s in IMAGE_SUFFIXES:
        return "image"
    return None


def needs_ocr(text: str) -> bool:
    """pdftotext 抽出的文本过少 → 多半是扫描件，需要 OCR。"""
    return len((text or "").strip()) < PDF_SCANNED_MAX


def is_useful(text: str) -> bool:
    return len((text or "").strip()) >= MIN_USEFUL_CHARS


def companion_path(path: Path) -> Path:
    return path.with_suffix(path.suffix + ".md")


def render_companion(filename: str, ext: str, text: str, ocr: bool, title=None) -> str:
    # 标题优先用文件索引里的真标题(很多附件名是 uuid,标题不可读);缺则回退文件名。
    heading = title or filename
    fm = [
        "---",
        "type: ustc-attachment-text",
        "institution: ustc",
        "source_file: %s" % filename,
        "title: %s" % heading,
        "ext: %s" % ext,
        "ocr: %s" % ("true" if ocr else "false"),
        "privacy: public",
        "---",
        "# %s" % heading,
        "",
        text.strip(),
        "",
    ]
    return "\n".join(fm)


def load_titles(index_path):
    """读 files_index.jsonl -> {local_path: title}，给附件旁车配可读标题。"""
    titles = {}
    if not index_path or not os.path.exists(index_path):
        return titles
    import json

    with open(index_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                r = json.loads(line)
            except ValueError:
                continue
            lp, t = r.get("local_path"), r.get("title")
            if lp and t:
                titles[lp] = t
    return titles


def _run(cmd, timeout=300):
    try:
        r = subprocess.run(
            cmd, capture_output=True, text=True, timeout=timeout, check=False
        )
        return r.stdout or ""
    except Exception:  # noqa: BLE001
        return ""


def extract_pdf(path: Path) -> tuple[str, bool]:
    text = _run(["pdftotext", "-enc", "UTF-8", "-q", str(path), "-"])
    if needs_ocr(text):
        with tempfile.TemporaryDirectory() as td:
            side = os.path.join(td, "s.txt")
            outp = os.path.join(td, "o.pdf")
            _run(
                ["ocrmypdf", "-l", OCR_LANG, "--sidecar", side, "--force-ocr",
                 "--optimize", "0", str(path), outp],
                timeout=900,
            )
            if os.path.exists(side):
                try:
                    ocr_text = Path(side).read_text(encoding="utf-8", errors="ignore")
                except OSError:
                    ocr_text = ""
                if len(ocr_text.strip()) > len(text.strip()):
                    return ocr_text, True
    return text, False


def extract_office(path: Path) -> tuple[str, bool]:
    if path.suffix.lower() == ".docx":
        text = _run(["pandoc", "-t", "plain", str(path)])
        if is_useful(text):
            return text, False
    with tempfile.TemporaryDirectory() as td:
        # 每次用独立 UserInstallation 配置目录，否则多个 soffice 并发会抢同一份
        # 用户配置锁、互相失败。-env 必须在子命令名之前。
        _run(
            ["soffice", "-env:UserInstallation=file://%s/lo" % td, "--headless",
             "--convert-to", "txt:Text", "--outdir", td, str(path)],
            timeout=300,
        )
        out = Path(td) / (path.stem + ".txt")
        if out.exists():
            return out.read_text(encoding="utf-8", errors="ignore"), False
    return "", False


def extract_image(path: Path) -> tuple[str, bool]:
    return _run(["tesseract", str(path), "stdout", "-l", OCR_LANG]), True


def process_file(path: Path, *, do_images: bool, min_image_kb: int,
                 force: bool, titles=None) -> str:
    """返回状态: written/skip-exists/skip-image/skip-empty/skip-type。"""
    kind = classify(path.suffix)
    if kind is None:
        return "skip-type"
    if kind == "image" and not do_images:
        return "skip-image"
    comp = companion_path(path)
    if comp.exists() and not force:
        try:
            if comp.stat().st_mtime >= path.stat().st_mtime:
                return "skip-exists"
        except OSError:
            pass
    if kind == "image":
        try:
            if path.stat().st_size < min_image_kb * 1024:
                return "skip-image"
        except OSError:
            return "skip-image"
        text, ocr = extract_image(path)
    elif path.suffix.lower() == ".pdf":
        text, ocr = extract_pdf(path)
    else:
        text, ocr = extract_office(path)
    if not is_useful(text):
        return "skip-empty"
    title = (titles or {}).get(str(path))
    comp.write_text(
        render_companion(path.name, path.suffix.lstrip("."), text, ocr, title=title),
        encoding="utf-8",
    )
    return "written"


def main(argv=None):
    ap = argparse.ArgumentParser(description="附件文本抽取/OCR -> 旁车 md")
    ap.add_argument("--root", required=True)
    ap.add_argument("--images", action="store_true", help="也 OCR 图片(默认跳过)")
    ap.add_argument("--min-image-kb", type=int, default=80, help="图片小于此KB跳过")
    ap.add_argument("--limit", type=int)
    ap.add_argument("--force", action="store_true", help="旁车已存在也重做")
    ap.add_argument("--files-index", help="files_index.jsonl 路径,给旁车配可读标题")
    ap.add_argument("--workers", type=int, default=4, help="并发抽取线程数(pdftotext/soffice/ocrmypdf 是子进程,CPU密集→并发提速)")
    a = ap.parse_args(argv)
    titles = load_titles(a.files_index)
    paths = []
    for cur, _dirs, names in os.walk(a.root):
        for name in sorted(names):
            if not name.endswith(".md"):
                paths.append(Path(cur) / name)
    if a.limit:
        paths = paths[: a.limit]
    counts: dict[str, int] = {}

    def _work(p):
        return process_file(p, do_images=a.images, min_image_kb=a.min_image_kb,
                            force=a.force, titles=titles)

    n_workers = max(1, int(a.workers or 1))
    if n_workers == 1:
        results = (_work(p) for p in paths)
        for p, st in zip(paths, results):
            counts[st] = counts.get(st, 0) + 1
            if st == "written":
                print("ok %s" % p, flush=True)
    else:
        from concurrent.futures import ThreadPoolExecutor

        with ThreadPoolExecutor(max_workers=n_workers) as ex:
            for p, st in zip(paths, ex.map(_work, paths)):
                counts[st] = counts.get(st, 0) + 1
                if st == "written":
                    print("ok %s" % p, flush=True)
    print("counts:", counts, flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
