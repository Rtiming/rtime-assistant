# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Reusable dialogue-audit report templates for course intake rehearsals."""

from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path
from typing import Sequence


def render_template(
    *,
    course_id: str,
    course_title: str,
    source_root: Path,
    brain_root: Path,
    entry: str,
    executor: str = "kimi",
    created_at: str | None = None,
) -> str:
    timestamp = created_at or datetime.now().replace(microsecond=0).isoformat()
    course_root = brain_root / "knowledge" / "courses" / course_id
    return "\n".join(
        [
            f"# {course_title} Obsidian 对话式入库审计",
            "",
            f"- created_at: {timestamp}",
            f"- entry: `{entry}`",
            f"- course_id: `{course_id}`",
            f"- executor: `{executor}`",
            "- auditor: `codex`",
            f"- source_root: `{source_root}`",
            f"- brain_root: `{brain_root}`",
            f"- course_root: `{course_root}`",
            "",
            "## 目标",
            "",
            "- 模拟用户在 Obsidian 侧栏与助手自然对话发起课程资料入库。",
            f"- 默认由 `{executor}` 在用户设备/Orange Pi 侧执行真实文件操作；Codex 负责工具修复、剧本生成、审计和测试。",
            "- 先确认文件清单、分类、风险和权限边界，再允许真实写入 brain。",
            "- 留存每轮对话、工具调用、路径、sha256、manifest 变化和验证结果，供后续课程批次复用。",
            "",
            "## 对话轮次",
            "",
            "| 轮次 | 用户话术 | 助手回复要点 | 允许动作 | 证据/日志 | 结论 |",
            "|---|---|---|---|---|---|",
            "| 1 |  | 识别源目录、课程名、文件数量、需要确认的问题 | 只读 |  |  |",
            "| 2 |  | dry-run 分类、`confirmation_questions`、默认安全动作和风险汇总 | 只读/写报告 |  |  |",
            "| 3 |  | 用户逐条确认后执行 apply，并显式带 `--approved` | 写课程目录/manifest/入口 note |  |  |",
            "| 4 |  | 查询验证和 Obsidian 显示检查 | 只读 |  |  |",
            "",
            "## 确认问题",
            "",
            "| id | severity | question | reason | default_action | related_files | user_answer |",
            "|---|---|---|---|---|---|---|",
            "|  |  |  |  |  |  |  |",
            "",
            "## 工具调用",
            "",
            "记录实际命令，不记录 secrets、原始聊天隐私或模型内部推理。",
            "真实执行优先由 executor 运行；Codex 只在工具修复、dry-run 设计、审计或用户明确授权时直接操作。",
            "",
            "```text",
            "# health",
            "python3 apps/assistant-gateway/rtime_chat.py --health",
            "",
            "# dry-run",
            "PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-intake <source_root> --brain-root <brain_root> --course-id <course_id> --course-title <course_title> --include-all --out <report_dir> --json",
            "",
            "# read-only MCP planning gate",
            "printf '{\"jsonrpc\":\"2.0\",\"id\":1,\"method\":\"tools/call\",\"params\":{\"name\":\"docpack.course_intake_plan\",\"arguments\":{\"source_root\":\"<source_root>\",\"brain_root\":\"<brain_root>\",\"course_id\":\"<course_id>\",\"course_title\":\"<course_title>\",\"include_all\":true}}}\\n' | PYTHONPATH=packages/brain-docpack/src python -m brain_docpack.mcp_server",
            "",
            "# apply",
            "PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-intake <source_root> --brain-root <brain_root> --course-id <course_id> --course-title <course_title> --include-all --apply --approved --write-md --update-pdf-manifest --obsidian-note <vault_relative_note> --obsidian-course-dir <vault_course_dir> --out <report_dir> --json",
            "",
            "# repair Obsidian visible layer from existing canonical course root",
            "PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-mirror-obsidian --brain-root <brain_root> --course-id <course_id> --obsidian-course-dir <vault_course_dir> --out <report_dir> --json",
            "",
            "# persistent Obsidian course-view manifest refresh",
            "python scripts/brain-intake/m4_link.py --plan --brain-root <brain_root> --vault-root <vault_root> --run-dir <run_dir>",
            "python scripts/brain-intake/m4_link.py --apply --brain-root <brain_root> --vault-root <vault_root> --run-dir <run_dir> --approved-plan <run_dir>/link-plan.json",
            "```",
            "",
            "## 权限边界",
            "",
            "- 允许写入：课程目录、课程 `_intake/`、`brain/_indexes/pdf-manifest.jsonl`、课程 Obsidian 入口 note、课程 vault 可见文件夹。",
            "- 不允许：删除源文件、改 Zotero、写 personal-data、提交运行报告或课程资料进 git。",
            "- 聊天附件和 `_inbox` 只代表收件，不代表最终入库。",
            "- 若执行者需要方案或脚本，先向 Codex 请求明确命令；Codex 返回命令后仍由执行者运行并回报证据。",
            "",
            "## 入库证据",
            "",
            "| 文件 | sha256 | 目标路径 | 分类 | Markdown 策略 | 风险 |",
            "|---|---|---|---|---|---|",
            "|  |  |  |  |  |  |",
            "",
            "## 验证",
            "",
            "- [ ] 文件数与源目录一致。",
            "- [ ] `1832_1_online.pdf` 未遗漏。",
            "- [ ] 扫描/低文本层 PDF 未被误标为已可靠 Markdown。",
            "- [ ] `pdf-manifest.jsonl` 新增或更新记录可追溯。",
            "- [ ] `confirmation_questions` 已逐条问用户，且 apply 命令带 `--approved`。",
            "- [ ] Obsidian 侧栏课程文件夹能看到 `课件/`、`参考资料/`、`文稿/`。",
            "- [ ] `.stignore` 未把课程物化目录加入忽略，只保留 `sync:false` 本机入口。",
            "- [ ] Mac Obsidian 可打开课程入口和 PDF 正本链接。",
            "- [ ] 复问“静电探针和 Mach probe 相关资料在哪里？”能返回路径或页码来源。",
            "",
            "## 可复用结论",
            "",
            "- 对话脚本需要保留的问题：",
            "- 工具需要修复或增强的点：",
            "- 下次课程批次可直接复用的命令：",
            "- 需要用户确认才可执行的动作：",
            "",
        ]
    )


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="brain-docpack dialogue-audit-template",
        description="Write a Markdown audit template for Obsidian-style course intake rehearsals.",
    )
    parser.add_argument("--course-id", required=True)
    parser.add_argument("--course-title", required=True)
    parser.add_argument("--source-root", type=Path, required=True)
    parser.add_argument("--brain-root", type=Path, required=True)
    parser.add_argument("--entry", default="obsidian")
    parser.add_argument("--executor", default="kimi")
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        render_template(
            course_id=args.course_id,
            course_title=args.course_title,
            source_root=args.source_root.expanduser().resolve(),
            brain_root=args.brain_root.expanduser().resolve(),
            entry=args.entry,
            executor=args.executor,
        ),
        encoding="utf-8",
    )
    print(str(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
