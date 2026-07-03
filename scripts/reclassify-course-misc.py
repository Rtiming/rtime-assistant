#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""把已入库课程 misc/ 里的文件用改进后的 classify_file 重分类到正确目录。

对 <course>/misc/ 的每个文件重判类别:非 misc 的移到 <course>/<category>/,
同时把伴生 md(md/misc/<stem>.md)移到 md/<category>/ 并修正其 frontmatter
source 路径;最后用 `brain-docpack course-index` 重生成 materials_index。
不删原件(move,不 copy)。pdf-manifest 的 brain_path 可能滞后(另行重建)。

用法: python reclassify-course-misc.py <brain根> [course-slug ...]
不给 slug 则处理 knowledge/courses 下全部课程。
"""
from __future__ import annotations
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "packages" / "brain-docpack" / "src"))
from brain_docpack.course_intake import classify_file  # noqa: E402


CAT_DIRS = ["slides", "lectures", "exercises", "exams", "solutions", "references", "misc"]


def reclassify_course(course: Path) -> dict:
    """遍历所有类别目录,按当前 classify_file 把放错的文件归位(自愈)。"""
    moved: dict[str, int] = {}
    for cur_cat in CAT_DIRS:
        d = course / cur_cat
        if not d.is_dir():
            continue
        for f in sorted(d.iterdir()):
            if not f.is_file():
                continue
            cat, _mtype = classify_file(f, course_id=course.name)
            if cat == cur_cat:
                continue
            dest_dir = course / cat
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest = dest_dir / f.name
            if dest.exists():
                continue
            f.rename(dest)
            # 伴生 md: md/<cur_cat>/<stem>.md -> md/<cat>/<stem>.md,修 source 路径
            md_src = course / "md" / cur_cat / (f.stem + ".md")
            if md_src.is_file():
                md_dst_dir = course / "md" / cat
                md_dst_dir.mkdir(parents=True, exist_ok=True)
                md_dst = md_dst_dir / md_src.name
                if not md_dst.exists():
                    try:
                        text = md_src.read_text(encoding="utf-8", errors="ignore")
                        text = text.replace(cur_cat + "/" + f.name, cat + "/" + f.name)
                        md_dst.write_text(text, encoding="utf-8")
                        md_src.unlink()
                    except OSError:
                        pass
            moved[f"{cur_cat}->{cat}"] = moved.get(f"{cur_cat}->{cat}", 0) + 1
    return moved


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("用法: reclassify-course-misc.py <brain根> [slug ...]")
        return 2
    brain = Path(sys.argv[1])
    courses_root = brain / "knowledge" / "courses"
    slugs = sys.argv[2:] or [p.name for p in sorted(courses_root.iterdir()) if p.is_dir()]
    for slug in slugs:
        course = courses_root / slug
        if not course.is_dir():
            continue
        moved = reclassify_course(course)
        if moved:
            print(f"  {slug}: " + ", ".join(f"{k} x{v}" for k, v in sorted(moved.items())))
        else:
            print(f"  {slug}: (已就位)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
