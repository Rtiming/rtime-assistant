# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""The transcription prompt — the contract any backend (agent/local/api) must honor.

This text is embedded into agent task specs and can be reused verbatim as the
system/user instruction for an API or local-VLM backend. It encodes
``docs/ai-readable-markdown-standard.zh-CN.md`` §4 (format) + §5 (acceptance)
+ §7 (backend constraints).
"""

from __future__ import annotations

from .spec import SECTIONS

TRANSCRIBE_INSTRUCTION = """\
你是严格视觉转写后端。看给定的整页 PNG,产出该页的 Markdown,作为 AI 可读正本。

铁律:
- 以你"看到的整页图像"为唯一事实;若提供了 OCR 草稿,只用于定位脉络,不得照抄。
- 不得静默改公式/数值/专业术语;看不清就写进「存疑」,不要猜。
- 公式一律块级 $$ 独立成行;行内量用 \\( ... \\)。保留正确下标/上标(如 k_B 不得错成 k_R)。
- 每页必须保留整页 PNG 引用。
- 四段(文字/公式/图表/存疑)齐全,缺项写"无",不得省略小节。

每页严格按此结构输出(N 为页码,NNN 为三位零填充):

<!-- page: NNN -->
## 第 N 页：<本页标题>

![第N页](images/p-NNN.png)

### 文字
- <逐条正文,忠实原页>

### 公式
$$
<块级 LaTeX,逐式独立成行;无公式则写: - 无>
$$

### 图表
- <逐一描述页面上的每个图/表/框/高亮/箭头:坐标轴、曲线趋势、标注、物理含义。仅当页面确无任何图形元素时才写: - 无>

### 存疑
- <核对公式是否符合物理常识(例:Fermi-Dirac 分布应为 1/(exp(·)+1));看不清/疑似课件错误逐条写出;确认无则写: - 无>
"""


def build_batch_task(
    *,
    slug: str,
    doc_title: str,
    pages: list[int],
    image_refs: list[str],
    out_path: str,
    draft_note: str = "",
) -> str:
    """Render a self-contained task spec for an agent to transcribe one batch.

    The agent reads each listed PNG, writes the merged per-page Markdown for the
    whole range to ``out_path``, following TRANSCRIBE_INSTRUCTION exactly.
    """
    lines = [
        f"# 转写任务:{slug} / {pages[0]:03d}-{pages[-1]:03d}",
        "",
        f"资料标题:{doc_title}",
        f"本批页码:{pages[0]}–{pages[-1]}(共 {len(pages)} 页)",
        f"输出写到:{out_path}",
        f"四段固定为:{' / '.join(SECTIONS)}",
        "",
        "## 要看的整页 PNG(逐页看,逐页转写,按页码顺序拼接)",
    ]
    for ref in image_refs:
        lines.append(f"- {ref}")
    if draft_note:
        lines += ["", "## 草稿说明", draft_note]
    lines += ["", "## 转写规范(必须逐条遵守)", "", TRANSCRIBE_INSTRUCTION]
    return "\n".join(lines) + "\n"
