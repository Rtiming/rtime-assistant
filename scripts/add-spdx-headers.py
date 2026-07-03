#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""K-license: 给仓库代码文件盖 SPDX 开源注明头(幂等,可当校验门)。

  python3 scripts/add-spdx-headers.py           # 盖章(只改缺头的文件)
  python3 scripts/add-spdx-headers.py --check   # 只检查:缺头则列出并 exit 1(CI 门)

规则(与 deploy/publish-manifest.json / 风险台账对齐):
  - 只处理 git 跟踪的文件(git ls-files),绝不碰工作区未跟踪内容。
  - 扩展名 .py/.sh 用 ``#``,.ts/.js/.mjs 用 ``//``;无扩展名但首行是 shebang 的
    脚本(deploy/bin/* 等)按 shebang 判注释风格(node → //,其余 → #)。
  - 跳过:vendored 第三方(tools/chat-intake/wechat-mp-rss,MIT——不得盖 AGPL 头)、
    发布排除件(chat-mcp/qq-export/wechat-export)、生成物(头几行含 GENERATED/
    do not edit/Built from)、已有 SPDX 的文件。
  - 插入位置:shebang 之后(无 shebang 则文件首);Python 模块 docstring 之前插注释
    不影响 docstring 语义。

上游许可注明(NOTICE 负责,不在文件头):apps/feishu-bridge fork 自 MIT 上游、
tools/chat-intake/wechat-mp-rss vendored MIT——见 NOTICE。
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

SPDX = "SPDX-License-Identifier: AGPL-3.0-only"
COPYRIGHT = "Copyright (C) 2026 rtime-assistant contributors (see NOTICE)"

SKIP_PREFIXES = (
    "tools/chat-intake/wechat-mp-rss/",  # vendored MIT(保留上游许可,不盖AGPL)
    "tools/chat-intake/chat-mcp/",       # 发布排除件(publish-manifest exclude_always)
    "tools/chat-intake/qq-export/",
    "tools/chat-intake/wechat-export/",
)
# 生成物/构建产物标记(头 5 行内命中即跳过;例:deploy/bin/model-defaults.sh、
# apps/obsidian-rtime-assistant/main.js)
GENERATED_MARKERS = ("GENERATED", "do not edit", "DO NOT EDIT", "Built from")

HASH_EXTS = {".py", ".sh"}
SLASH_EXTS = {".ts", ".js", ".mjs"}


def tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, capture_output=True, text=True, check=True
    ).stdout
    return [line for line in out.splitlines() if line.strip()]


def comment_style(rel: str, first_line: str) -> str | None:
    """'#' / '//' / None(不处理)。"""
    ext = Path(rel).suffix
    if ext in HASH_EXTS:
        return "#"
    if ext in SLASH_EXTS:
        return "//"
    if not ext and first_line.startswith("#!"):
        return "//" if "node" in first_line else "#"
    return None


def eligible(rel: str) -> bool:
    return not any(rel.startswith(p) for p in SKIP_PREFIXES)


def process(rel: str, *, write: bool) -> str:
    """返回 'ok'(已有头)/'stamped'(补了)/'missing'(check模式缺头)/'skip'。"""
    path = ROOT / rel
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return "skip"
    if not text.strip():
        return "skip"
    lines = text.splitlines(keepends=True)
    head = "".join(lines[:5])
    style = comment_style(rel, lines[0] if lines else "")
    if style is None or not eligible(rel):
        return "skip"
    if any(m in head for m in GENERATED_MARKERS):
        return "skip"
    if "SPDX-License-Identifier" in head:
        return "ok"
    if not write:
        return "missing"
    header = f"{style} {SPDX}\n{style} {COPYRIGHT}\n"
    insert_at = 0
    if lines and lines[0].startswith("#!"):
        insert_at = 1
        # Python 编码 cookie(# -*- coding: ... -*-)须紧跟 shebang,再后插
        if len(lines) > 1 and "coding:" in lines[1] and lines[1].lstrip().startswith("#"):
            insert_at = 2
    new = "".join(lines[:insert_at]) + header + "".join(lines[insert_at:])
    path.write_text(new, encoding="utf-8")
    return "stamped"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="SPDX header stamper / checker")
    ap.add_argument("--check", action="store_true", help="只检查不写;缺头 exit 1")
    args = ap.parse_args(argv)
    counts = {"ok": 0, "stamped": 0, "missing": 0, "skip": 0}
    missing: list[str] = []
    for rel in tracked_files():
        state = process(rel, write=not args.check)
        counts[state] += 1
        if state == "missing":
            missing.append(rel)
    print(
        f"spdx: ok={counts['ok']} stamped={counts['stamped']} "
        f"missing={counts['missing']} skipped(不适用/生成物/vendored)={counts['skip']}"
    )
    if args.check and missing:
        for rel in missing[:50]:
            print(f"missing: {rel}", file=sys.stderr)
        if len(missing) > 50:
            print(f"... and {len(missing) - 50} more", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
