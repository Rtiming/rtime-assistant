# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Course-material intake planning for the brain knowledge library."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence


SUPPORTED_EXTENSIONS = {
    ".pdf",
    ".ppt",
    ".pptx",
    ".doc",
    ".docx",
    ".txt",
    ".md",
}

# KEEP IN SYNC: scripts/brain-intake/m4_link.py (COURSE_VIEW_NAMES) and
# docs/maintainability-standards.zh-CN.md. The category list below and the
# Chinese view vocab (课件/讲义/习题/试卷/参考资料/答案/文稿) are mirrored there;
# changing a category or its visible name must update both files + the doc.
OBSIDIAN_CATEGORY_FOLDERS = {
    "slides": "课件",
    "lectures": "讲义",
    "exercises": "习题",
    "exams": "试卷",
    "references": "参考资料",
    "solutions": "答案",
    "misc": "资料",
}

OBSIDIAN_MARKDOWN_FOLDER = "文稿"

# 旧版工具标准把"作业/习题"放 homework/;新标准统一进 exercises/。为不扰动已归位的旧文件,
# 把 homework/ 当作 exercises 的物理别名:索引、Obsidian 习题入口都按逻辑分类 "exercises"(习题)
# 归并,但文件留在原 homework/ 目录,不迁移。键=旧物理目录,值=新逻辑分类。
LEGACY_CATEGORY_DIRS = {"homework": "exercises"}

# course-view-manifest 草稿用:精选入口(用途)的 brain 子目录。misc/资料 是临时兜底,
# 不作默认 Obsidian 入口。globs 与 scripts/brain-intake/m4_link.py 对齐。
COURSE_VIEW_SUBDIRS = ("slides", "lectures", "exercises", "exams", "references", "solutions")
_VIEW_ORIGINAL_GLOBS = ["*.pdf", "*.ppt", "*.pptx", "*.doc", "*.docx", "*.jpg", "*.jpeg", "*.png", "*.gif", "*.md"]
_VIEW_DERIVED_EXCLUDES = [
    "images/**", "text/**", "md/**", "pdf/**", "source/**",
    "**/images/**", "**/text/**", "**/md/**", "**/pdf/**", "**/source/**",
]

SENSITIVE_FILENAME_PATTERNS = (
    "身份证",
    "证件",
    "姓名",
    "名单",
    "联系方式",
    "手机号",
    "电话",
    "报名表",
    "学号",
    "成绩",
    "账号",
    "密码",
    "隐私",
    "保密",
)

# 课程入库确认门策略。--auto-approve 按本策略放行:severity 在 auto_approve_severities
# 里的问题自动通过;blocker(疑似敏感文件名 / 同名异内容冲突 / 空计划)默认不在其中,
# 仍需人工。可用 --policy 指向 JSON 覆盖;按 id 微调:auto_approve_ids 强制放行,
# always_block_ids 强制拦截。
DEFAULT_INTAKE_POLICY = {
    "auto_approve_severities": ["confirm"],
    "auto_approve_ids": [],
    "always_block_ids": [],
}

CHAPTER_NUMBERS = {
    "一": "01",
    "二": "02",
    "三": "03",
    "四": "04",
    "五": "05",
    "六": "06",
    "七": "07",
    "八": "08",
    "九": "09",
    "十": "10",
}

EXPERIMENTAL_NOTICE = (
    "This course-intake output is a conservative draft for one course-material "
    "batch. It records filing, conversion, and review evidence; the original "
    "source files remain the source of truth."
)

EXPERIMENTAL_NOTICE_ZH = (
    "本批次是课程资料入库整理草稿，用于记录归位、PDF 文本层判断、轻量 Markdown "
    "和后续 DocPack/OCR 风险；原始文件仍是正本，Markdown 和索引不替代原件。"
)


@dataclass
class CourseFile:
    source_path: str
    relative_source: str
    file_name: str
    extension: str
    sha256: str
    size_bytes: int
    category: str
    material_type: str
    destination_path: str
    md_path: str | None
    duplicate_of: str | None
    pdf_pages: int | None
    first_page_text_chars: int | None
    first_page_text_sample: str
    md_strategy: str
    risks: list[str]


@dataclass
class ConfirmationQuestion:
    id: str
    severity: str
    question: str
    reason: str
    default_action: str
    related_files: list[str]


@dataclass
class IntakePlan:
    schema_version: str
    created_at: str
    course_id: str
    course_title: str
    source_root: str
    brain_root: str
    course_root: str
    apply: bool
    files: list[CourseFile]
    summary: dict[str, int]
    auto_apply_allowed: bool
    confirmation_questions: list[ConfirmationQuestion]


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_text(command: Sequence[str], *, timeout: int = 30) -> str:
    try:
        # Always decode tool output as UTF-8: the platform default (GBK/cp936 on
        # Windows) mangles or crashes on Chinese title metadata and page text.
        return subprocess.check_output(
            list(command),
            encoding="utf-8",
            errors="replace",
            stderr=subprocess.DEVNULL,
            timeout=timeout,
        )
    except Exception:
        return ""


def pdf_pages(path: Path) -> int | None:
    output = _run_text(["pdfinfo", "-enc", "UTF-8", str(path)], timeout=20)
    match = re.search(r"^Pages:\s+(\d+)", output, re.MULTILINE)
    return int(match.group(1)) if match else None


def pdf_first_page_text(path: Path) -> str:
    output = _run_text(
        ["pdftotext", "-enc", "UTF-8", "-f", "1", "-l", "1", "-layout", str(path), "-"],
        timeout=30,
    )
    return re.sub(r"\s+", " ", output).strip()


def pdf_page_text(path: Path, page: int) -> str:
    output = _run_text(
        ["pdftotext", "-enc", "UTF-8", "-f", str(page), "-l", str(page), "-layout", str(path), "-"],
        timeout=30,
    )
    return output.rstrip()


def normalize_filename(name: str) -> str:
    stem = Path(name).stem
    suffix = Path(name).suffix.lower()
    stem = stem.strip()
    stem = stem.replace("&", "-")
    stem = re.sub(r"[/:\\]+", "-", stem)
    stem = re.sub(r"\s+", "-", stem)
    stem = re.sub(r"-+", "-", stem)
    return f"{stem}{suffix}"


def _is_plasma_diagnostics_course(course_id: str, course_title: str, name: str) -> bool:
    lower = name.lower()
    return (
        course_id == "plasma-diagnostics-introduction"
        or "等离子体诊断" in course_title
        or "等离子体诊断" in name
        or "plasma_diagnostics" in lower
        or "plasma-diagnostics" in lower
        or "plasma diagnostics" in lower
    )


def classify_file(path: Path, *, course_id: str = "", course_title: str = "") -> tuple[str, str]:
    name = path.name
    lower = name.lower()
    suffix = path.suffix.lower()
    is_plasma = _is_plasma_diagnostics_course(course_id, course_title, name)

    if suffix in {".ppt", ".pptx"}:
        return "slides", "slides-source"
    if is_plasma:
        if name == "1832_1_online.pdf":
            return "references", "paper"
        if (
            "principles_of_plasma_diagnostics" in lower
            or "高温等离子体诊断技术" in name
            or "上册" in name
            or "下册" in name
        ):
            return "references", "textbook"
        if "等离子体" in name and "诊断导论" in name:
            return "slides", "lecture-pdf"
    if suffix in {".doc", ".docx"} and re.match(r"20\d{2}", name):
        return "exams", "exam-source"
    if "习题解答" in name or "课后题答案" in name:
        return "solutions", "solution-manual"
    if "习题课" in name or "exclec" in lower:
        return "slides", "exercise-session"
    if re.search(r"第[一二三四五六七八九十]+章", name):
        return "lectures", "chapter-lecture"
    if re.match(r"20\d{2}( \(\d+\))?\.pdf$", name) or "tsa_mid" in lower:
        return "exams", "exam"
    if "tsa_fin" in lower:
        return "exams", "exam"
    if "汪志诚" in name or "上册" in name or "下册" in name:
        return "references", "textbook"

    # 通用分类(中文/阿拉伯数字皆认),在 misc 兜底前。顺序:作业先于章节,
    # 试卷/答案先于讲义,避免"第N章作业"被当成讲义。
    if re.search(r"试卷|期末|期中|真题|样卷|考试题|往年题|月考|测验", name):
        return "exams", "exam"
    # 仅当文件名(去后缀)本身就是"年份[季节][(序号)]"才算历年卷,避免误判
    # 文件名里带学期标注的讲义(如 计算物理B_2025秋_蒙卡)。
    if re.fullmatch(r"20\d{2}\s*[秋春夏冬]?(\s*\(\d+\))?", path.stem.strip()):
        return "exams", "exam"
    if re.search(r"答案|解答|参考解|题解|solution", lower):
        return "solutions", "solution-manual"
    if re.search(r"作业|练习|homework|\bhw\s*\d", lower) or "习题" in name:
        return "exercises", "exercise"
    if (
        re.search(r"第\s*\d+\s*章|chapter\s*\d+|\bch\s*\d+|讲义|handout|lecture[\s_-]*note", lower)
        or re.search(r"第\s*\d+\s*章|讲义", name)
    ):
        return "lectures", "chapter-lecture"
    if (
        re.search(r"lecture[\s_-]*\d+|\blec[\s_-]*\d+|slide|课件", lower)
        or re.search(r"课件|第\s*\d+\s*讲|第[一二三四五六七八九十]+讲", name)
    ):
        return "slides", "lecture-pdf"
    if re.search(r"教材|参考书|参考资料|textbook|手册|读本|复习|提要|速查|小抄|题典|大全", name):
        return "references", "textbook"
    return "misc", "course-material"


def destination_name(path: Path, category: str, material_type: str, *, course_id: str = "") -> str:
    name = path.name
    suffix = path.suffix.lower()

    if course_id == "plasma-diagnostics-introduction":
        if name == "1832_1_online.pdf":
            return "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf"
        if name == "Principles_of_plasma_diagnostics(1).pdf":
            return "reference_principles-of-plasma-diagnostics.pdf"
        if "高温等离子体诊断技术 上册" in name:
            return "reference_高温等离子体诊断技术-上册-项志遴-俞昌旋.pdf"
        if "高温等离子体诊断技术 下册" in name:
            return "reference_高温等离子体诊断技术-下册-项志遴-俞昌旋.pdf"

    if course_id == "thermal-statistical-physics" and re.match(r"20\d{2}( \(\d+\))?\.pdf$", name):
        year = name[:4]
        variant = re.search(r"\((\d+)\)", name)
        tail = f"_variant-{variant.group(1)}" if variant else ""
        return f"{year}_thermal-statistical-physics_exam{tail}{suffix}"

    if course_id == "thermal-statistical-physics" and name == "25Spring_TSA_mid.pdf":
        return "2025-spring_thermal-statistical-physics_midterm.pdf"
    if course_id == "thermal-statistical-physics" and name == "25Spring_TSA_fin.pdf":
        return "2025-spring_thermal-statistical-physics_final.pdf"
    if course_id == "thermal-statistical-physics" and name == "2017.docx":
        return "2017_thermal-statistical-physics_exam-source.docx"

    match = re.search(r"第([一二三四五六七八九十]+)章[-_ ]*(.+)", Path(name).stem)
    if match:
        chapter = CHAPTER_NUMBERS.get(match.group(1), "xx")
        title = normalize_filename(match.group(2) + suffix)
        return f"ch{chapter}_{title}"

    normalized = normalize_filename(name)
    if category in {"lectures", "slides"} and material_type == "exercise-session":
        return f"exercise-session_{normalized}"
    if category == "solutions":
        return f"solution_{normalized}"
    if category == "references" and material_type == "paper":
        return f"paper_{normalized}"
    if category == "references":
        return f"reference_{normalized}"
    return normalized


def md_strategy_for(path: Path, first_page_text: str) -> tuple[str, list[str]]:
    suffix = path.suffix.lower()
    risks: list[str] = []

    if suffix == ".pdf":
        if len(first_page_text) >= 80:
            return "docpack-content-md", risks
        if len(first_page_text) > 0:
            risks.append("low_first_page_text")
            return "docpack-content-md-needs-review", risks
        risks.append("no_pdf_text_layer_or_scanned")
        return "pdf-original-plus-page-images-ocr-later", risks

    if suffix in {".ppt", ".pptx", ".doc", ".docx"}:
        risks.append("office_conversion_review_required")
        return "docpack-via-libreoffice-needs-review", risks

    return "plain-markdown-or-text", risks


def default_keywords(course_id: str, course_title: str) -> list[str]:
    words: list[str] = []
    for token in re.split(r"[-_\s/]+", course_id):
        if token:
            words.append(token.lower())
    title = course_title.strip()
    if title:
        words.append(title.lower())
        words.extend(part.lower() for part in re.split(r"[\s/：:]+", title) if part)
    if course_id == "thermal-statistical-physics" or "热力学" in course_title:
        words.extend(["热力学", "热学", "热统", "统计", "thermo", "thermal", "tsa"])
    if course_id == "plasma-diagnostics-introduction" or "等离子体诊断" in course_title:
        words.extend(["等离子体", "诊断", "plasma", "diagnostics"])
    seen: set[str] = set()
    deduped: list[str] = []
    for word in words:
        if word and word not in seen:
            seen.add(word)
            deduped.append(word)
    return deduped


def discover_files(source_root: Path, keywords: Sequence[str], *, include_all: bool = False) -> list[Path]:
    found: list[Path] = []
    lowered_keywords = [keyword.lower() for keyword in keywords]
    for path in source_root.rglob("*"):
        if not path.is_file() or path.name.startswith(".~"):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
            continue
        if include_all:
            found.append(path)
            continue
        haystack = str(path.relative_to(source_root)).lower()
        if lowered_keywords and not any(keyword in haystack for keyword in lowered_keywords):
            # Year-only exam files in a small course download batch are often
            # named without the course title; chapter handouts may only be
            # named "第X章-..."; keep those as candidates.
            if (
                not re.match(r"20\d{2}( \(\d+\))?\.pdf$", path.name)
                and not re.search(r"第[一二三四五六七八九十]+章", path.name)
                and path.name
                not in {
                    "25Spring_TSA_mid.pdf",
                    "25Spring_TSA_fin.pdf",
                    "MidTermExcLec.pptx",
                    "2017.docx",
                }
            ):
                continue
        found.append(path)
    return sorted(found)


def existing_hashes(course_root: Path) -> dict[str, str]:
    hashes: dict[str, str] = {}
    if not course_root.exists():
        return hashes
    for path in course_root.rglob("*"):
        try:
            relative = path.relative_to(course_root)
        except ValueError:
            continue
        if relative.parts and relative.parts[0] in {"docpacks", "md", "_intake"}:
            continue
        if path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS:
            try:
                hashes[sha256_file(path)] = str(path)
            except OSError:
                continue
    return hashes


def _filename_sensitive_risks(path: Path) -> list[str]:
    if any(pattern in path.name for pattern in SENSITIVE_FILENAME_PATTERNS):
        return ["possible_personal_or_sensitive_filename"]
    return []


def build_confirmation_questions(
    *,
    files: list[CourseFile],
    summary: dict[str, int],
    source_root: Path,
    course_root: Path,
    include_all: bool,
) -> list[ConfirmationQuestion]:
    questions: list[ConfirmationQuestion] = []

    if not files:
        questions.append(
            ConfirmationQuestion(
                id="empty-intake-plan",
                severity="blocker",
                question="本次没有发现可入库的课程资料。是否需要调整 source_root、keywords，或改用 --include-all?",
                reason="空计划不能证明资料已整理。",
                default_action="停止 apply，重新确认来源目录或筛选规则。",
                related_files=[],
            )
        )

    if not course_root.exists():
        questions.append(
            ConfirmationQuestion(
                id="new-course-root",
                severity="confirm",
                question=f"将新建课程正本目录 `{course_root}`，课程 id 和学期/名称是否正确?",
                reason="新课程目录会成为长期知识库路径，错误 course_id 会产生难以清理的入口。",
                default_action="确认 course_id/course_title 后再 apply。",
                related_files=[],
            )
        )

    if include_all and len(files) > 10:
        questions.append(
            ConfirmationQuestion(
                id="large-include-all-batch",
                severity="confirm",
                question=f"`--include-all` 将处理 `{source_root}` 下 {len(files)} 个支持文件；这个目录是否已经只包含同一门课的资料?",
                reason="include-all 会绕过关键词过滤，混入下载目录中的其他课程资料会污染课程目录。",
                default_action="若来源目录不纯，先拆分目录或改用 keyword 过滤。",
                related_files=[item.relative_source for item in files[:10]],
            )
        )

    misc_files = [item.relative_source for item in files if item.category == "misc"]
    if misc_files:
        questions.append(
            ConfirmationQuestion(
                id="misc-classification",
                severity="confirm",
                question="这些文件无法稳定归入课件/讲义/试卷/答案/参考资料，是否接受先放入 `misc/资料`?",
                reason="`misc` 是临时兜底分类，长期会降低检索和 Obsidian 展示质量。",
                default_action="请给出更准确分类，或确认本批次先归入 misc。",
                related_files=misc_files[:20],
            )
        )

    sensitive_files = [
        item.relative_source
        for item in files
        if "possible_personal_or_sensitive_filename" in item.risks
    ]
    if sensitive_files:
        questions.append(
            ConfirmationQuestion(
                id="possible-sensitive-files",
                severity="blocker",
                question="这些文件名疑似包含个人身份、名单、联系方式、成绩或账号信息。是否仍按课程资料入 knowledge，还是转入 personal-data/高敏路径?",
                reason="高敏材料默认只读，不能自动归入公开课程知识层。",
                default_action="暂停这些文件，等待用户确认最终落点。",
                related_files=sensitive_files[:20],
            )
        )

    target_conflicts = [
        item.relative_source
        for item in files
        if "target_exists_different_content" in item.risks
    ]
    if target_conflicts:
        questions.append(
            ConfirmationQuestion(
                id="target-path-conflicts",
                severity="blocker",
                question="这些文件的目标路径已存在但 sha256 不同。是否重命名保留两份、替换旧文件，还是跳过?",
                reason="原件永不覆盖；同名异内容必须人工决策。",
                default_action="默认跳过冲突文件，不覆盖已有正本。",
                related_files=target_conflicts[:20],
            )
        )

    duplicate_files = [item.relative_source for item in files if item.duplicate_of]
    if duplicate_files:
        questions.append(
            ConfirmationQuestion(
                id="duplicate-files",
                severity="confirm",
                question="这些文件 sha256 已存在于课程目录。是否只记录为重复并跳过复制?",
                reason="重复文件不应再存一份正本，但用户可能需要知道来源目录是否可以后续清理。",
                default_action="跳过复制，仅在报告中保留重复证据。",
                related_files=duplicate_files[:20],
            )
        )

    conversion_risk_files = [
        item.relative_source
        for item in files
        if any(
            risk in item.risks
            for risk in (
                "no_pdf_text_layer_or_scanned",
                "low_first_page_text",
                "office_conversion_review_required",
            )
        )
    ]
    if conversion_risk_files:
        questions.append(
            ConfirmationQuestion(
                id="conversion-depth",
                severity="confirm",
                question="这些文件存在扫描/OCR/Office版式风险。本次是否只归档原件并把 OCR/DocPack/视觉复核列为后续任务?",
                reason="不能把低置信文本抽取或 Office 转换结果当成已验证内容。",
                default_action="先归档原件；Markdown/DocPack/OCR 后续按需单独做。",
                related_files=conversion_risk_files[:20],
            )
        )

    if summary.get("category_slides", 0) and summary.get("category_lectures", 0):
        questions.append(
            ConfirmationQuestion(
                id="slides-vs-handouts",
                severity="confirm",
                question="本批次同时包含课堂课件和长文本讲义。是否按 `slides/课件` 与 `lectures/讲义` 分开?",
                reason="原始文件夹名常把讲义和课件混用，长期整理应按材料用途而不是下载目录名分类。",
                default_action="课堂展示 deck 或按讲次 PDF 放 `slides/`，连续正文讲义放 `lectures/`。",
                related_files=[
                    item.relative_source
                    for item in files
                    if item.category in {"slides", "lectures"}
                ][:20],
            )
        )

    classroom_materials = summary.get("category_slides", 0) + summary.get("category_lectures", 0)
    if summary.get("category_references", 0) and classroom_materials:
        questions.append(
            ConfirmationQuestion(
                id="mixed-course-material-types",
                severity="confirm",
                question="本批次同时包含课堂课件和参考资料。Obsidian 展示层是否按 `课件/参考资料` 分开展示?",
                reason="课程正本层和 Obsidian 展示层分类需要一致，否则后续侧栏会混乱。",
                default_action="按用途分开展示；不要把课程根目录整体暴露到 Obsidian。",
                related_files=[],
            )
        )

    return questions


def build_plan(
    source_root: Path,
    brain_root: Path,
    course_id: str,
    course_title: str,
    *,
    keywords: Sequence[str],
    include_all: bool = False,
    apply: bool = False,
) -> IntakePlan:
    course_root = brain_root / "knowledge" / "courses" / course_id
    course_root_existed = course_root.exists()
    seen_hashes = existing_hashes(course_root)
    files: list[CourseFile] = []

    for source in discover_files(source_root, keywords, include_all=include_all):
        file_hash = sha256_file(source)
        category, material_type = classify_file(
            source,
            course_id=course_id,
            course_title=course_title,
        )
        destination_dir = course_root / category
        if category == "slides" and source.suffix.lower() in {".ppt", ".pptx"}:
            destination_dir = destination_dir / "source"
        destination = destination_dir / destination_name(
            source,
            category,
            material_type,
            course_id=course_id,
        )
        pages: int | None = None
        first_text = ""
        if source.suffix.lower() == ".pdf":
            pages = pdf_pages(source)
            first_text = pdf_first_page_text(source)
        strategy, risks = md_strategy_for(source, first_text)
        risks = [*risks, *_filename_sensitive_risks(source)]
        md_path = None
        if source.suffix.lower() == ".pdf" and strategy.startswith("docpack-content-md"):
            md_path = str(course_root / "md" / category / f"{destination.stem}.md")
        existing = seen_hashes.get(file_hash)
        duplicate = None
        if existing and Path(existing).resolve() != destination.resolve():
            duplicate = existing
            risks = [*risks, "duplicate_sha256_in_course"]
        if destination.exists():
            try:
                if sha256_file(destination) != file_hash:
                    risks = [*risks, "target_exists_different_content"]
            except OSError:
                risks = [*risks, "target_exists_unreadable"]
        if not existing:
            seen_hashes[file_hash] = str(destination)

        files.append(
            CourseFile(
                source_path=str(source),
                relative_source=str(source.relative_to(source_root)),
                file_name=source.name,
                extension=source.suffix.lower(),
                sha256=file_hash,
                size_bytes=source.stat().st_size,
                category=category,
                material_type=material_type,
                destination_path=str(destination),
                md_path=md_path,
                duplicate_of=duplicate,
                pdf_pages=pages,
                first_page_text_chars=len(first_text) if source.suffix.lower() == ".pdf" else None,
                first_page_text_sample=first_text[:240],
                md_strategy=strategy,
                risks=risks,
            )
        )

    summary: dict[str, int] = {
        "files": len(files),
        "duplicates": sum(1 for item in files if item.duplicate_of),
        "docpack_content_md": sum(
            1 for item in files if item.md_strategy.startswith("docpack-content-md")
        ),
        "ocr_later": sum(1 for item in files if "ocr-later" in item.md_strategy),
        "office_review": sum(1 for item in files if "libreoffice" in item.md_strategy),
        "markdown_candidates": sum(1 for item in files if item.md_path),
        "target_conflicts": sum(
            1 for item in files if "target_exists_different_content" in item.risks
        ),
        "possible_sensitive": sum(
            1 for item in files if "possible_personal_or_sensitive_filename" in item.risks
        ),
        "source_only_files": sum(
            1
            for item in files
            if Path(item.destination_path).relative_to(course_root).parts[:2] == ("slides", "source")
        ),
        "materials_index_csv": 1,
        "materials_index_md": 1,
    }
    for item in files:
        summary[f"category_{item.category}"] = summary.get(f"category_{item.category}", 0) + 1

    questions = build_confirmation_questions(
        files=files,
        summary=summary,
        source_root=source_root,
        course_root=course_root,
        include_all=include_all,
    )
    auto_apply_allowed = not questions and course_root_existed
    summary["confirmation_questions"] = len(questions)
    summary["auto_apply_allowed"] = int(auto_apply_allowed)

    return IntakePlan(
        schema_version="course-intake-plan-v1",
        created_at=datetime.now().replace(microsecond=0).isoformat(),
        course_id=course_id,
        course_title=course_title,
        source_root=str(source_root),
        brain_root=str(brain_root),
        course_root=str(course_root),
        apply=apply,
        files=files,
        summary=summary,
        auto_apply_allowed=auto_apply_allowed,
        confirmation_questions=questions,
    )


def write_json(path: Path, data: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def render_report(plan: IntakePlan) -> str:
    lines = [
        f"# {plan.course_title} course intake",
        "",
        f"- created_at: {plan.created_at}",
        f"- course_id: `{plan.course_id}`",
        f"- source_root: `{plan.source_root}`",
        f"- course_root: `{plan.course_root}`",
        f"- applied: `{str(plan.apply).lower()}`",
        "",
        "## Experimental Status",
        "",
        EXPERIMENTAL_NOTICE,
        "",
        "## Summary",
        "",
    ]
    for key in sorted(plan.summary):
        lines.append(f"- {key}: {plan.summary[key]}")

    lines.extend(["", "## Confirmation Gate", ""])
    lines.append(f"- auto_apply_allowed: `{str(plan.auto_apply_allowed).lower()}`")
    if plan.confirmation_questions:
        lines.append("- status: `needs-user-confirmation`")
        lines.append("")
        for question in plan.confirmation_questions:
            lines.append(f"### {question.id}")
            lines.append("")
            lines.append(f"- severity: `{question.severity}`")
            lines.append(f"- question: {question.question}")
            lines.append(f"- reason: {question.reason}")
            lines.append(f"- default_action: {question.default_action}")
            if question.related_files:
                lines.append("- related_files:")
                for related in question.related_files:
                    lines.append(f"  - `{related}`")
            lines.append("")
    else:
        lines.append("- status: `no-confirmation-questions`")

    lines.extend(["", "## Files", ""])
    for item in plan.files:
        size_mb = item.size_bytes / 1024 / 1024
        pages = item.pdf_pages if item.pdf_pages is not None else "-"
        text_chars = item.first_page_text_chars if item.first_page_text_chars is not None else "-"
        duplicate = f"; duplicate_of=`{item.duplicate_of}`" if item.duplicate_of else ""
        risks = ", ".join(item.risks) if item.risks else "none"
        lines.append(
            f"- `{item.relative_source}` -> `{Path(item.destination_path).relative_to(plan.course_root)}` "
            f"({item.category}/{item.material_type}, {size_mb:.2f} MB, pages={pages}, "
            f"first_page_text={text_chars}, md_strategy={item.md_strategy}, risks={risks}{duplicate})"
        )

    lines.extend(
        [
            "",
            "## Markdown Conversion Policy",
            "",
            "- `docpack-content-md`: text layer is usable; generate page-based Markdown for study, but keep PDF as source of truth.",
            "- `docpack-content-md-needs-review`: text exists but may be sparse; use Markdown as search aid only.",
            "- `pdf-original-plus-page-images-ocr-later`: likely scanned/image PDF; keep PDF and page images, add OCR only when needed.",
            "- `docpack-via-libreoffice-needs-review`: Office conversion may change layout; rendered PDF/page images are the display evidence.",
        ]
    )
    return "\n".join(lines) + "\n"


def apply_plan(plan: IntakePlan) -> None:
    course_root = Path(plan.course_root)
    for folder in (
        "slides",
        "slides/source",
        "lectures",
        "exercises",
        "exams",
        "references",
        "solutions",
        "misc",
        "docpacks",
        "md",
        "_intake",
    ):
        (course_root / folder).mkdir(parents=True, exist_ok=True)

    for item in plan.files:
        destination = Path(item.destination_path)
        if item.duplicate_of or "target_exists_different_content" in item.risks:
            continue
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists() and sha256_file(destination) == item.sha256:
            continue
        shutil.copy2(item.source_path, destination)


def _relative_link(from_file: Path, target: Path) -> str:
    return Path(os.path.relpath(target, from_file.parent)).as_posix()


def write_markdown_outputs(plan: IntakePlan, *, max_pages: int) -> None:
    for item in plan.files:
        if not item.md_path or item.duplicate_of:
            continue
        if item.pdf_pages is None:
            continue
        md_path = Path(item.md_path)
        source_pdf = Path(item.destination_path)
        md_path.parent.mkdir(parents=True, exist_ok=True)
        relative_pdf = _relative_link(md_path, source_pdf)

        lines = [
            "---",
            "type: course-material-md",
            f"course: {plan.course_id}",
            f'title: "{Path(item.destination_path).stem}"',
            f"material_type: {item.material_type}",
            f"source_pdf: {relative_pdf}",
            f"brain_path: {source_pdf.relative_to(Path(plan.brain_root)).as_posix()}",
            f"sha256: {item.sha256}",
            f"pdf_pages: {item.pdf_pages}",
            f"md_strategy: {item.md_strategy}",
            "status: experimental-draft",
            "generated_by: brain-docpack course-intake",
            "---",
            "",
            f"# {Path(item.destination_path).stem}",
            "",
            "> 本文件是课程资料入库实验中生成的轻量 Markdown 草稿，仅用于学习、检索和验证流程；不替代 PDF 正本，也不作为后续课程 Markdown 模板。",
            "",
            f"- PDF: [{Path(item.destination_path).name}]({relative_pdf})",
            f"- 页数: {item.pdf_pages}",
            f"- 转换策略: `{item.md_strategy}`",
            "",
            "## 助手可读性",
            "",
            "- 本文件保留 PDF 文本层抽取结果，可用于搜索、问答和复习提示。",
            "- 公式、图、表和版面关系仍以 PDF 或 DocPack 页面图为准。",
            "- 原始抽取文本默认折叠，避免把 Obsidian 阅读界面变成低质量排版稿。",
            "",
        ]
        if item.risks:
            lines.extend(["## 风险", "", *[f"- `{risk}`" for risk in item.risks], ""])
        if item.pdf_pages > max_pages:
            lines.extend(
                [
                    "## 内容",
                    "",
                    f"页数超过 `--md-max-pages={max_pages}`，本次只生成索引入口；需要时再对该 PDF 单独生成完整 Markdown。",
                    "",
                ]
            )
        else:
            lines.extend(
                [
                    "## 提取文本",
                    "",
                    "以下是按页保留的原始文本层，主要给搜索和助手读取；人工阅读、做题和核对版式请打开 PDF。",
                    "",
                ]
            )
            for page in range(1, item.pdf_pages + 1):
                text = pdf_page_text(source_pdf, page)
                lines.extend(
                    [
                        "<details>",
                        f"<summary>Page {page}</summary>",
                        "",
                        f"[打开PDF第{page}页]({relative_pdf}#page={page})",
                        "",
                    ]
                )
                if text.strip():
                    lines.extend(["```text", text, "```", ""])
                else:
                    lines.extend(["> 本页未提取到文本；请以PDF原页为准。", ""])
                lines.extend(["</details>", ""])

        md_path.write_text("\n".join(lines), encoding="utf-8")


def obsidian_folder_for_category(category: str) -> str:
    return OBSIDIAN_CATEGORY_FOLDERS.get(category, "资料")


def _course_relative(path: Path, course_root: Path) -> Path:
    return Path(path).resolve().relative_to(course_root.resolve())


def _is_source_only_material(path: Path, course_root: Path) -> bool:
    try:
        relative = _course_relative(path, course_root)
    except ValueError:
        return False
    return relative.parts[:2] == ("slides", "source")


def _markdown_table_cell(value: object) -> str:
    text = "" if value is None else str(value)
    return text.replace("|", "\\|").replace("\n", " ")


def write_materials_indexes(plan: IntakePlan) -> None:
    """Write human- and machine-readable course material indexes."""

    course_root = Path(plan.course_root)
    category_order = {
        "slides": 0,
        "lectures": 1,
        "exercises": 2,
        "solutions": 3,
        "exams": 4,
        "references": 5,
        "misc": 9,
    }
    sorted_files = sorted(
        plan.files,
        key=lambda item: (
            category_order.get(item.category, 99),
            Path(item.destination_path).relative_to(course_root).as_posix(),
        ),
    )
    rows: list[dict[str, str]] = []
    for item in sorted_files:
        destination = Path(item.destination_path)
        md_path = Path(item.md_path) if item.md_path else None
        rows.append(
            {
                "category": item.category,
                "material_type": item.material_type,
                "title": destination.stem,
                "path": destination.relative_to(course_root).as_posix(),
                "md_path": md_path.relative_to(course_root).as_posix() if md_path else "",
                "source_name": item.file_name,
                "sha256": item.sha256,
                "pdf_pages": "" if item.pdf_pages is None else str(item.pdf_pages),
                "md_strategy": item.md_strategy,
                "risks": ";".join(item.risks),
            }
        )

    fields = [
        "category",
        "material_type",
        "title",
        "path",
        "md_path",
        "source_name",
        "sha256",
        "pdf_pages",
        "md_strategy",
        "risks",
    ]
    csv_buffer = io.StringIO()
    writer = csv.DictWriter(csv_buffer, fieldnames=fields)
    writer.writeheader()
    writer.writerows(rows)
    (course_root / "materials_index.csv").write_text(csv_buffer.getvalue(), encoding="utf-8")

    md_lines = [
        f"# {plan.course_title} materials index",
        "",
        f"- course_id: `{plan.course_id}`",
        f"- updated_at: `{plan.created_at}`",
        f"- files: {len(rows)}",
        "",
        "| category | type | title | path | md | pages | risks |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in rows:
        path_link = f"[{_markdown_table_cell(row['path'])}]({row['path']})"
        md_link = f"[md]({row['md_path']})" if row["md_path"] else ""
        md_lines.append(
            "| "
            + " | ".join(
                [
                    _markdown_table_cell(row["category"]),
                    _markdown_table_cell(row["material_type"]),
                    _markdown_table_cell(row["title"]),
                    path_link,
                    md_link,
                    _markdown_table_cell(row["pdf_pages"]),
                    _markdown_table_cell(row["risks"]),
                ]
            )
            + " |"
        )
    (course_root / "materials_index.md").write_text("\n".join(md_lines) + "\n", encoding="utf-8")


def _existing_course_md_path(course_root: Path, category: str, material_path: Path) -> Path | None:
    sibling = material_path.with_suffix(".md")
    if sibling.exists():
        return sibling
    try:
        relative = material_path.relative_to(course_root / category)
    except ValueError:
        relative = Path(material_path.name)
    md_candidate = course_root / "md" / category / relative.with_suffix(".md")
    if md_candidate.exists():
        return md_candidate
    return None


def index_existing_course(
    course_root: Path,
    *,
    brain_root: Path,
    course_id: str,
    course_title: str,
    created_at: str | None = None,
) -> IntakePlan:
    """Build a lightweight index plan from an already-filed course root."""

    created = created_at or datetime.now().replace(microsecond=0).isoformat()
    files: list[CourseFile] = []
    indexed_categories = {
        "slides",
        "lectures",
        "exercises",
        "exams",
        "references",
        "solutions",
        "misc",
    }
    # 物理目录 = 标准分类目录 ∪ 旧版别名目录(如 homework/)。逻辑分类用于索引归并和
    # Obsidian 入口;物理目录用于实际路径和伴生 md 查找,旧文件不迁移。
    scan_dirs = sorted(indexed_categories | set(LEGACY_CATEGORY_DIRS))
    for directory in scan_dirs:
        category = LEGACY_CATEGORY_DIRS.get(directory, directory)
        category_root = course_root / directory
        if not category_root.is_dir():
            continue
        for source in sorted(category_root.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if source.name == "README.md":
                continue
            relative_to_category = source.relative_to(category_root)
            if relative_to_category.parts[:1] in {("images",), ("text",), ("md",)}:
                continue
            try:
                file_hash = sha256_file(source)
            except OSError:
                continue
            pages: int | None = None
            first_text = ""
            if source.suffix.lower() == ".pdf":
                pages = pdf_pages(source)
                first_text = pdf_first_page_text(source)
            md_path = _existing_course_md_path(course_root, directory, source)
            if source.suffix.lower() == ".md":
                strategy = "markdown-source"
            elif source.suffix.lower() == ".pdf" and md_path:
                strategy = "pdf-original-plus-companion-md"
            elif source.suffix.lower() == ".pdf":
                strategy = "pdf-original"
            elif category == "slides" and "source" in source.relative_to(course_root).parts:
                strategy = "source-only"
            else:
                strategy = "original-file"
            files.append(
                CourseFile(
                    source_path=str(source),
                    relative_source=source.relative_to(course_root).as_posix(),
                    file_name=source.name,
                    extension=source.suffix.lower(),
                    sha256=file_hash,
                    size_bytes=source.stat().st_size,
                    category=category,
                    material_type="existing-course-material",
                    destination_path=str(source),
                    md_path=str(md_path) if md_path else None,
                    duplicate_of=None,
                    pdf_pages=pages,
                    first_page_text_chars=len(first_text) if source.suffix.lower() == ".pdf" else None,
                    first_page_text_sample=first_text[:240],
                    md_strategy=strategy,
                    risks=[],
                )
            )

    summary: dict[str, int] = {
        "files": len(files),
        "materials_index_csv": 1,
        "materials_index_md": 1,
        "source_only_files": sum(
            1
            for item in files
            if Path(item.destination_path).relative_to(course_root).parts[:2] == ("slides", "source")
        ),
        "confirmation_questions": 0,
        "auto_apply_allowed": 1,
    }
    for item in files:
        summary[f"category_{item.category}"] = summary.get(f"category_{item.category}", 0) + 1

    return IntakePlan(
        schema_version="course-index-v1",
        created_at=created,
        course_id=course_id,
        course_title=course_title,
        source_root=str(course_root),
        brain_root=str(brain_root),
        course_root=str(course_root),
        apply=True,
        files=files,
        summary=summary,
        auto_apply_allowed=True,
        confirmation_questions=[],
    )


def _file_content_matches(source: Path, target: Path, *, source_hash: str | None = None) -> bool:
    if not target.is_file():
        return False
    try:
        expected = source_hash or sha256_file(source)
        return sha256_file(target) == expected
    except OSError:
        return False


def _copy_visible_course_file(
    source: Path,
    target: Path,
    *,
    source_hash: str | None = None,
) -> bool:
    if target.exists():
        if _file_content_matches(source, target, source_hash=source_hash):
            return False
        digest = (source_hash or sha256_file(source))[:8]
        base_target = target
        target = base_target.with_name(f"{base_target.stem}-{digest}{base_target.suffix}")
        counter = 2
        while target.exists():
            if _file_content_matches(source, target, source_hash=source_hash):
                return False
            target = base_target.with_name(
                f"{base_target.stem}-{digest}-{counter}{base_target.suffix}"
            )
            counter += 1

    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    return True


def mirror_obsidian_course_files(plan: IntakePlan, obsidian_course_dir: Path) -> dict[str, int]:
    """Copy course originals and generated Markdown into a vault-visible course folder."""

    obsidian_course_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    mirrored = 0

    for item in plan.files:
        if item.duplicate_of:
            continue

        destination = Path(item.destination_path)
        if destination.exists() and not _is_source_only_material(destination, Path(plan.course_root)):
            visible_folder = obsidian_course_dir / obsidian_folder_for_category(item.category)
            if item.extension == ".md":
                visible_folder = (
                    obsidian_course_dir
                    / OBSIDIAN_MARKDOWN_FOLDER
                    / obsidian_folder_for_category(item.category)
                )
            mirrored += 1
            if _copy_visible_course_file(
                destination,
                visible_folder / destination.name,
                source_hash=item.sha256,
            ):
                changed += 1

        if item.md_path:
            md_path = Path(item.md_path)
            if md_path.exists():
                markdown_folder = (
                    obsidian_course_dir
                    / OBSIDIAN_MARKDOWN_FOLDER
                    / obsidian_folder_for_category(item.category)
                )
                mirrored += 1
                if _copy_visible_course_file(md_path, markdown_folder / md_path.name):
                    changed += 1

    return {
        "obsidian_mirror_files": mirrored,
        "obsidian_mirror_changed": changed,
    }


def mirror_existing_course_to_obsidian(course_root: Path, obsidian_course_dir: Path) -> dict[str, int]:
    """Rebuild a vault-visible course folder from an existing brain course root."""

    obsidian_course_dir.mkdir(parents=True, exist_ok=True)
    changed = 0
    mirrored = 0

    # 标准分类目录 + 旧版别名目录(homework/ → 习题),让旧作业也进同一可见入口。
    mirror_dirs = list(OBSIDIAN_CATEGORY_FOLDERS.items())
    for legacy_dir, target in LEGACY_CATEGORY_DIRS.items():
        visible = OBSIDIAN_CATEGORY_FOLDERS.get(target)
        if visible:
            mirror_dirs.append((legacy_dir, visible))

    for category, visible_name in mirror_dirs:
        category_root = course_root / category
        if not category_root.is_dir():
            continue
        for source in sorted(category_root.rglob("*")):
            if not source.is_file() or source.suffix.lower() not in SUPPORTED_EXTENSIONS:
                continue
            if _is_source_only_material(source, course_root):
                continue
            target = obsidian_course_dir / visible_name / source.relative_to(category_root)
            mirrored += 1
            if _copy_visible_course_file(source, target):
                changed += 1

        md_category_root = course_root / "md" / category
        if not md_category_root.is_dir():
            continue
        for source in sorted(md_category_root.rglob("*.md")):
            target = (
                obsidian_course_dir
                / OBSIDIAN_MARKDOWN_FOLDER
                / visible_name
                / source.relative_to(md_category_root)
            )
            mirrored += 1
            if _copy_visible_course_file(source, target):
                changed += 1

    return {
        "obsidian_mirror_files": mirrored,
        "obsidian_mirror_changed": changed,
    }


def _load_jsonl(path: Path) -> list[dict[str, object]]:
    entries: list[dict[str, object]] = []
    if not path.exists():
        return entries
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        loaded = json.loads(line)
        if isinstance(loaded, dict):
            entries.append(loaded)
    return entries


def update_pdf_manifest(plan: IntakePlan, *, obsidian_note: str | None = None) -> int:
    brain_root = Path(plan.brain_root)
    manifest_path = brain_root / "_indexes" / "pdf-manifest.jsonl"
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    note_path = obsidian_note or f"课程/{plan.course_title}.md"

    entries = _load_jsonl(manifest_path)
    by_sha: dict[str, int] = {}
    by_path: dict[str, int] = {}
    for index, entry in enumerate(entries):
        sha = entry.get("sha256")
        brain_path = entry.get("brain_path")
        if isinstance(sha, str):
            by_sha[sha] = index
        if isinstance(brain_path, str):
            by_path[brain_path] = index

    changed = 0
    for item in plan.files:
        if item.extension != ".pdf":
            continue
        destination = Path(item.destination_path)
        if not destination.exists():
            continue
        brain_path = destination.relative_to(brain_root).as_posix()
        md_path = None
        if item.md_path and Path(item.md_path).exists():
            md_path = Path(item.md_path).relative_to(brain_root).as_posix()
        entry = {
            "schema_version": "pdf-manifest-v1",
            "sha256": item.sha256,
            "canonical": True,
            "brain_path": brain_path,
            "kind": "course-pdf",
            "title": destination.stem,
            "course": plan.course_id,
            "material_type": item.material_type,
            "attachment_mode": "brain-only",
            "mobile_cache": False,
            "zotero_item_key": None,
            "zotero_linked_attachment_key": None,
            "obsidian_note": note_path,
            "md_path": md_path,
            "created_at": plan.created_at,
        }
        existing_index = by_sha.get(item.sha256, by_path.get(brain_path))
        if existing_index is None:
            entries.append(entry)
            by_sha[item.sha256] = len(entries) - 1
            by_path[brain_path] = len(entries) - 1
            changed += 1
        else:
            existing = entries[existing_index]
            if isinstance(existing.get("created_at"), str):
                entry["created_at"] = existing["created_at"]
            merged = {**existing, **entry}
            if existing != merged:
                entries[existing_index] = merged
                changed += 1

    manifest_path.write_text(
        "".join(json.dumps(entry, ensure_ascii=False, sort_keys=True) + "\n" for entry in entries),
        encoding="utf-8",
    )
    return changed


def write_outputs(plan: IntakePlan, out_dir: Path) -> None:
    out_dir.mkdir(parents=True, exist_ok=True)
    write_json(out_dir / "course-intake-plan.json", asdict(plan))
    (out_dir / "course-intake-report.md").write_text(render_report(plan), encoding="utf-8")


def update_course_readme(plan: IntakePlan) -> None:
    course_root = Path(plan.course_root)
    readme = course_root / "README.md"
    lines = [
        f"# {plan.course_title}",
        "",
        "## 定位",
        "",
        "本目录保存该课程资料正本。PDF、PPT、DOCX 等原件保留在本目录；Markdown 和 DocPack 是学习与检索层，不替代原件。",
        "",
        "## 实验状态",
        "",
        EXPERIMENTAL_NOTICE_ZH,
        "",
        "## 子目录",
        "",
        "- `slides/`: 课堂展示课件、按讲次 PDF 和导出的显示 PDF",
        "- `slides/source/`: PPT/PPTX 等源文件，只作追溯和再导出，不作为 Obsidian 默认入口",
        "- `lectures/`: 连续正文讲义、手册式课程文本",
        "- `exercises/`: 习题、答疑和复习题(旧版 `homework/` 视作同类,索引与习题入口合并显示,旧文件不迁移)",
        "- `exams/`: 历年试卷、中期和期末材料",
        "- `references/`: 教材、参考书和阅读材料",
        "- `solutions/`: 习题解答、课后题答案",
        "- `docpacks/`: 可重建的页面图、Markdown、manifest 和验证结果",
        "- `md/`: Obsidian/AI 学习入口",
        "- `_intake/`: 入库计划、报告和审计记录",
        "",
        "## 当前入库批次",
        "",
        f"- intake time: {plan.created_at}",
        f"- files: {plan.summary.get('files', 0)}",
        f"- docpack-content-md candidates: {plan.summary.get('docpack_content_md', 0)}",
        f"- OCR-later candidates: {plan.summary.get('ocr_later', 0)}",
        f"- lightweight Markdown candidates: {plan.summary.get('markdown_candidates', 0)}",
        "",
        "详细清单见 `materials_index.md`、`materials_index.csv` 和 `_intake/course-intake-report.md`。",
        "",
        "## 转换结论",
        "",
        "- 有文本层的 PDF 可以生成 `md/` 下的按页 Markdown，用于 Obsidian、搜索和 AI 问答。",
        "- 扫描 PDF、教材扫描版和低文本层试卷不适合直接转 Markdown；后续按需 OCR 或建立 DocPack 页面图。",
        "- Markdown 不替代 PDF 正本，做题、看公式和核对版式仍以原 PDF 或 DocPack 页面图为准。",
    ]
    readme.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_obsidian_view_entries(plan: IntakePlan, *, term: str = "<学期>") -> list[dict]:
    """Build paste-ready course-view-manifest entries for the reading folders that
    actually received content.

    The vault term (e.g. ``2026春``) is left as a placeholder for the owner to
    fill. ``misc``/``资料`` is intentionally omitted (it is a temporary bucket, not
    a default Obsidian entry). Globs mirror ``scripts/brain-intake/m4_link.py``.
    """
    present = {item.category for item in plan.files}
    # 反查每个逻辑分类带进来的旧版物理目录(仅当该批次确有文件),用于把 homework/ 合并到习题
    # 入口。m4_link 的 materialize 支持多源 brain_rels 归并到一个 vault 目录。
    legacy_present: dict[str, list[str]] = {}
    for legacy_dir, target in LEGACY_CATEGORY_DIRS.items():
        if any(item.relative_source.startswith(f"{legacy_dir}/") for item in plan.files):
            legacy_present.setdefault(target, []).append(legacy_dir)
    entries: list[dict] = []
    for category in COURSE_VIEW_SUBDIRS:
        if category not in present:
            continue
        view_name = OBSIDIAN_CATEGORY_FOLDERS[category]
        brain_rels = [f"knowledge/courses/{plan.course_id}/{category}"]
        for legacy_dir in sorted(legacy_present.get(category, [])):
            brain_rels.append(f"knowledge/courses/{plan.course_id}/{legacy_dir}")
        source_key = {"brain_rels": brain_rels} if len(brain_rels) > 1 else {"brain_rel": brain_rels[0]}
        entries.append(
            {
                "vault_rel": f"10 课程/{term}/{plan.course_title}/{view_name}",
                **source_key,
                "mode": "materialize",
                "include_globs": list(_VIEW_ORIGINAL_GLOBS),
                "exclude_globs": list(_VIEW_DERIVED_EXCLUDES),
                "prune": True,
            }
        )
    if plan.summary.get("markdown_candidates", 0) or plan.summary.get("docpack_content_md", 0):
        entries.append(
            {
                "vault_rel": f"10 课程/{term}/{plan.course_title}/{OBSIDIAN_MARKDOWN_FOLDER}",
                "brain_rel": f"knowledge/courses/{plan.course_id}/md",
                "mode": "materialize",
                "include_globs": ["**/*.md", "*.md"],
                "exclude_globs": [],
                "prune": True,
            }
        )
    return entries


def write_obsidian_view_stub(plan: IntakePlan, entries: list[dict]) -> Path:
    """Write the paste-ready course-view-manifest entry draft into ``_intake``."""
    out = Path(plan.course_root) / "_intake" / "obsidian-view-entries.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "_note": (
            "把 entries 合并进 brain-notes/80 系统/course-view-manifest.json,填好 <学期>"
            "(如 2026春),再每台客户端跑 m4_link --plan/--apply/--verify(须 ok:true)。"
            "见 docs/obsidian-vault-layout.zh-CN.md。"
        ),
        "schema_version": "rtime-course-view-v1",
        "entries": entries,
    }
    out.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return out


def load_intake_policy(path: Path | None) -> dict:
    """Load the confirmation-gate policy, falling back to the built-in default.

    A missing or unreadable policy file is non-fatal: it warns and uses the
    default (auto-approve only ``confirm``-severity questions, never blockers).
    Unknown keys in the file are ignored.
    """
    policy = {key: list(value) for key, value in DEFAULT_INTAKE_POLICY.items()}
    if path is None:
        return policy
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        print(f"warning: ignoring unreadable intake policy {path}: {exc}", file=sys.stderr)
        return policy
    for key in DEFAULT_INTAKE_POLICY:
        if isinstance(data.get(key), list):
            policy[key] = [str(item) for item in data[key]]
    return policy


def evaluate_confirmation_gate(
    questions: list[ConfirmationQuestion],
    *,
    approved: bool,
    auto_approve: bool,
    policy: dict,
) -> tuple[bool, list[ConfirmationQuestion]]:
    """Decide whether ``--apply`` may proceed, and list what still blocks it.

    - ``approved``: a human reviewed every question -> always proceed.
    - ``auto_approve``: proceed only if no question blocks under ``policy``, so
      blocker-severity questions (sensitive filenames, target conflicts, empty
      plans) still stop a non-interactive / agent run.
    - neither: any open question blocks (legacy strict behaviour).
    """
    if not questions or approved:
        return True, []
    if not auto_approve:
        return False, list(questions)
    severities = set(policy.get("auto_approve_severities", []))
    auto_ids = set(policy.get("auto_approve_ids", []))
    block_ids = set(policy.get("always_block_ids", []))
    blocking: list[ConfirmationQuestion] = []
    for question in questions:
        if question.id in block_ids:
            blocking.append(question)
        elif question.id in auto_ids:
            continue
        elif question.severity in severities:
            continue
        else:
            blocking.append(question)
    return (not blocking), blocking


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain-docpack course-intake",
        description="Plan and optionally apply a course-material intake into brain/knowledge/courses.",
    )
    parser.add_argument("source_root", type=Path)
    parser.add_argument("--brain-root", type=Path, required=True)
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--course-title", required=True)
    parser.add_argument("--out", type=Path)
    parser.add_argument(
        "--include-all",
        action="store_true",
        help="Treat every supported file under source_root as part of this course batch",
    )
    parser.add_argument("--apply", action="store_true", help="Copy candidate files into the course directory")
    parser.add_argument(
        "--approved",
        action="store_true",
        help="Confirm the user reviewed confirmation_questions and approved this apply.",
    )
    parser.add_argument(
        "--auto-approve",
        action="store_true",
        help="Non-interactive apply: auto-approve confirm-level questions per --policy, "
        "but still refuse on blockers (sensitive filenames, target conflicts, empty plan).",
    )
    parser.add_argument(
        "--policy",
        type=Path,
        help="JSON policy file controlling which confirmation questions --auto-approve "
        "passes; a missing file falls back to the built-in default.",
    )
    parser.add_argument(
        "--write-md",
        action="store_true",
        help="After --apply, write lightweight page-based Markdown for text-layer PDFs",
    )
    parser.add_argument("--md-max-pages", type=int, default=120)
    parser.add_argument(
        "--update-pdf-manifest",
        action="store_true",
        help="After --apply, upsert canonical course PDF entries into brain/_indexes/pdf-manifest.jsonl",
    )
    parser.add_argument(
        "--obsidian-note",
        help="Vault-relative course entry note path written into pdf-manifest; defaults to 课程/<course-title>.md",
    )
    parser.add_argument(
        "--obsidian-course-dir",
        type=Path,
        help="Optional vault course directory where visible originals and Markdown study files are copied",
    )
    parser.add_argument(
        "--keyword",
        action="append",
        default=[],
        help="Candidate filename/path keyword. Can be repeated.",
    )
    parser.add_argument("--json", action="store_true", help="Print plan JSON to stdout")
    args = parser.parse_args(argv)

    keywords = args.keyword or default_keywords(args.course_id, args.course_title)
    source_root = args.source_root.expanduser().resolve()
    brain_root = args.brain_root.expanduser().resolve()
    plan = build_plan(
        source_root,
        brain_root,
        args.course_id,
        args.course_title,
        keywords=keywords,
        include_all=args.include_all,
        apply=args.apply,
    )

    policy = load_intake_policy(args.policy.expanduser().resolve() if args.policy else None)
    proceed, blocking = evaluate_confirmation_gate(
        plan.confirmation_questions,
        approved=args.approved,
        auto_approve=args.auto_approve,
        policy=policy,
    )
    if args.apply and not proceed:
        if args.out:
            write_outputs(plan, args.out.expanduser().resolve())
        if args.json:
            print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
        else:
            print(render_report(plan))
        if blocking:
            details = "; ".join(f"{item.id}({item.severity})" for item in blocking)
            print(
                f"Refusing --apply: {len(blocking)} blocking confirmation question(s) "
                f"need human review: {details}. Resolve them, or rerun with --approved "
                "after user confirmation.",
                file=sys.stderr,
            )
        else:
            print(
                "Refusing --apply because confirmation_questions are present; rerun with "
                "--auto-approve (per policy) or --approved after user confirmation.",
                file=sys.stderr,
            )
        return 2

    if args.apply:
        apply_plan(plan)
        if args.write_md:
            write_markdown_outputs(plan, max_pages=args.md_max_pages)
        if args.obsidian_course_dir:
            plan.summary.update(
                mirror_obsidian_course_files(
                    plan,
                    args.obsidian_course_dir.expanduser().resolve(),
                )
            )
        if args.update_pdf_manifest:
            changed = update_pdf_manifest(plan, obsidian_note=args.obsidian_note)
            plan.summary["pdf_manifest_changed"] = changed
        write_materials_indexes(plan)
        course_intake = Path(plan.course_root) / "_intake"
        write_outputs(plan, course_intake)
        update_course_readme(plan)
        view_entries = build_obsidian_view_entries(plan)
        if view_entries:
            write_obsidian_view_stub(plan, view_entries)
            plan.summary["obsidian_view_entries"] = len(view_entries)

    if args.out:
        write_outputs(plan, args.out.expanduser().resolve())

    if args.json:
        print(json.dumps(asdict(plan), ensure_ascii=False, indent=2))
    else:
        print(render_report(plan))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
