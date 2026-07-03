# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Format constants for the strict visual transcription standard.

Mirrors ``docs/ai-readable-markdown-standard.zh-CN.md`` §4 (output format) and
§5.1 (machine acceptance gates). Keep this file the single source of the
on-disk shape so backends, merge, and validate agree.
"""

from __future__ import annotations

SPEC_VERSION = "1.0"

TIER = "strict-visual"

# The four mandatory per-page sections, in order.
SECTIONS = ("文字", "公式", "图表", "存疑")

# Banner placed right after the H1 title.
BANNER = "> 严格视觉转写;每页保留整页 PNG 引用。看不清/疑似错误见各页「存疑」。"

# Required frontmatter keys for a finished file.
REQUIRED_FRONTMATTER = (
    "title",
    "source",
    "source_sha256",
    "tier",
    "status",
    "pages",
    "backend",
    "spec_version",
)

VALID_STATUS = ("draft", "verified")


def page_marker(page_no: int) -> str:
    """HTML comment anchor for a page, e.g. ``<!-- page: 003 -->``."""
    return f"<!-- page: {page_no:03d} -->"


def page_image_name(page_no: int) -> str:
    return f"p-{page_no:03d}.png"


def page_image_ref(page_no: int) -> str:
    return f"![第{page_no}页](images/{page_image_name(page_no)})"


def empty_page_block(page_no: int, title: str = "") -> str:
    """A spec-conformant skeleton for one page with every section present.

    Used by stub/echo backends and as the template a real backend must fill.
    """
    return (
        f"{page_marker(page_no)}\n"
        f"## 第 {page_no} 页：{title}\n\n"
        f"{page_image_ref(page_no)}\n\n"
        "### 文字\n- \n\n"
        "### 公式\n- 无\n\n"
        "### 图表\n- \n\n"
        "### 存疑\n- 无\n"
    )


def render_frontmatter(meta: dict) -> str:
    """Render a minimal, deterministic YAML frontmatter block (no yaml dep)."""
    lines = ["---"]
    for key, value in meta.items():
        if value is None:
            continue
        lines.append(f"{key}: {value}")
    lines.append("---")
    return "\n".join(lines) + "\n"


def parse_frontmatter(text: str) -> tuple[dict, str]:
    """Parse a leading ``---`` frontmatter block. Returns (meta, body).

    Tiny, dependency-free parser: ``key: value`` per line, values kept as str.
    Mirrors how ``brain_library.indexer`` reads frontmatter into meta columns.
    """
    if not text.startswith("---"):
        return {}, text
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return {}, text
    meta: dict = {}
    body_start = None
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            body_start = i + 1
            break
        raw = lines[i]
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if ":" in raw:
            key, _, value = raw.partition(":")
            meta[key.strip()] = value.strip()
    if body_start is None:
        return {}, text
    body = "\n".join(lines[body_start:])
    return meta, body
