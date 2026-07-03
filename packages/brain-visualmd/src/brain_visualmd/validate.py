# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Machine acceptance gates — ``docs/ai-readable-markdown-standard.zh-CN.md`` §5.1.

Deterministic, scriptable checks. A document with any ERROR is rejected
(``ok == False``); WARNINGs are surfaced but do not block. The semantic review
gate (§5.2) is separate and handled by a backend's self-report plus adversarial
visual review — not here.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from .render import sha256_file
from .spec import (
    REQUIRED_FRONTMATTER,
    SECTIONS,
    TIER,
    VALID_STATUS,
    page_image_name,
    parse_frontmatter,
)

_PAGE_MARKER = re.compile(r"<!--\s*page:\s*(\d+)\s*-->")
_DOLLAR_BLOCK = re.compile(r"\$\$.*?\$\$", re.DOTALL)
_FENCE = re.compile(r"```.*?```", re.DOTALL)
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass
class Issue:
    severity: str  # "error" | "warning"
    code: str
    message: str
    page: int | None = None


def _split_pages(body: str) -> list[tuple[int, str]]:
    """Return [(page_no, block_text), ...] split on page markers, in order."""
    matches = list(_PAGE_MARKER.finditer(body))
    pages: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        pages.append((int(m.group(1)), body[start:end]))
    return pages


def validate_page_block(
    block: str, page_no: int, docpack_dir: Path | None = None
) -> list[Issue]:
    """Machine checks for ONE page's Markdown block. Used for backend retry."""
    issues: list[Issue] = []
    if f"## 第 {page_no} 页" not in block:
        issues.append(
            Issue("error", "missing_heading", "缺少 '## 第 N 页' 标题", page_no)
        )
    img = page_image_name(page_no)
    if f"](images/{img})" not in block:
        issues.append(
            Issue("error", "missing_png_ref", f"缺少整页 PNG 引用 {img}", page_no)
        )
    elif docpack_dir is not None and not (docpack_dir / "images" / img).exists():
        issues.append(
            Issue("error", "png_file_missing", f"PNG 文件不存在 images/{img}", page_no)
        )
    for section in SECTIONS:
        if f"### {section}" not in block:
            issues.append(
                Issue("error", "missing_section", f"缺少小节 ### {section}", page_no)
            )
    stripped = _FENCE.sub("", _DOLLAR_BLOCK.sub("", block))
    if "$" in stripped:
        issues.append(
            Issue("error", "inline_dollar", "行内 $...$;公式须块级 $$", page_no)
        )
    if "�" in block:
        issues.append(Issue("error", "replacement_char", "替换字符(疑似乱码)", page_no))
    return issues


def validate_document(
    text: str,
    *,
    docpack_dir: Path | None = None,
    expected_pages: int | None = None,
    source_path: Path | None = None,
) -> list[Issue]:
    issues: list[Issue] = []
    meta, body = parse_frontmatter(text)

    # 1) frontmatter
    if not meta:
        issues.append(Issue("error", "no_frontmatter", "缺少 YAML frontmatter"))
    else:
        for key in REQUIRED_FRONTMATTER:
            if key not in meta:
                issues.append(
                    Issue("error", "frontmatter_missing", f"frontmatter 缺字段:{key}")
                )
        if meta.get("tier") != TIER:
            issues.append(Issue("error", "bad_tier", f"tier 必须为 {TIER}"))
        if meta.get("status") not in VALID_STATUS:
            issues.append(Issue("error", "bad_status", f"status 必须为 {VALID_STATUS}"))
        sha = meta.get("source_sha256", "")
        if source_path is not None and source_path.is_file() and sha:
            if sha != sha256_file(source_path):
                issues.append(
                    Issue("error", "sha_mismatch", "source_sha256 与源文件不一致")
                )

    # 2) pages present + contiguous
    pages = _split_pages(body)
    page_nos = [p for p, _ in pages]
    if not pages:
        issues.append(Issue("error", "no_pages", "没有任何 <!-- page: N --> 页块"))
    if expected_pages is not None and len(pages) != expected_pages:
        issues.append(
            Issue("error", "page_count", f"页数 {len(pages)} != 期望 {expected_pages}")
        )
    if page_nos and page_nos != list(range(1, len(page_nos) + 1)):
        issues.append(Issue("error", "page_sequence", f"页码不连续/不从1起:{page_nos}"))

    # 3) per-page: heading, PNG ref (+ file), four sections
    for page_no, block in pages:
        if f"## 第 {page_no} 页" not in block:
            issues.append(
                Issue("error", "missing_heading", "缺少 '## 第 N 页' 标题", page_no)
            )
        img = page_image_name(page_no)
        if f"](images/{img})" not in block:
            issues.append(
                Issue("error", "missing_png_ref", f"缺少整页 PNG 引用 {img}", page_no)
            )
        elif docpack_dir is not None and not (docpack_dir / "images" / img).exists():
            issues.append(
                Issue(
                    "error", "png_file_missing", f"PNG 文件不存在 images/{img}", page_no
                )
            )
        for section in SECTIONS:
            if f"### {section}" not in block:
                issues.append(
                    Issue(
                        "error", "missing_section", f"缺少小节 ### {section}", page_no
                    )
                )

    # 5) block-level formulas only (no inline single-$)
    stripped = _FENCE.sub("", _DOLLAR_BLOCK.sub("", body))
    if "$" in stripped:
        issues.append(
            Issue(
                "error",
                "inline_dollar",
                "检测到行内 $...$;公式须块级 $$,行内量用 \\(...\\)",
            )
        )

    # 6) garbled heuristic
    if "�" in body:
        issues.append(
            Issue("error", "replacement_char", "存在替换字符 \\ufffd(疑似乱码)")
        )
    if _CONTROL.search(body):
        issues.append(Issue("warning", "control_chars", "存在控制字符(疑似乱码)"))

    return issues


def is_ok(issues: list[Issue]) -> bool:
    return not any(i.severity == "error" for i in issues)


def write_verify(
    docpack_dir: Path, issues: list[Issue], *, backend_id: str = ""
) -> Path:
    payload = {
        "ok": is_ok(issues),
        "errors": sum(1 for i in issues if i.severity == "error"),
        "warnings": sum(1 for i in issues if i.severity == "warning"),
        "backend": backend_id,
        "issues": [asdict(i) for i in issues],
    }
    out = docpack_dir / "verify.json"
    out.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    return out


def validate_docpack(docpack_dir: Path) -> list[Issue]:
    """Validate the merged ``<slug>.md`` inside a docpack dir against its plan."""
    from .models import Plan

    docpack_dir = Path(docpack_dir)
    plan = Plan.read(docpack_dir)
    md = docpack_dir / f"{plan.slug}.md"
    if not md.exists():
        return [Issue("error", "no_merged_md", f"未找到合并产物 {md.name}(先 merge)")]
    src = Path(plan.source) if plan.source else None
    return validate_document(
        md.read_text("utf-8"),
        docpack_dir=docpack_dir,
        expected_pages=plan.pages,
        source_path=src,
    )
