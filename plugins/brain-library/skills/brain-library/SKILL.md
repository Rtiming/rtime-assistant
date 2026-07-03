---
name: brain-library
description: Use when auditing, indexing, reviewing, or planning display modules for the rtime `brain` library, especially Obsidian vault signals, Zotero/Better BibTeX links, DocPack citation readiness, SQLite/BM25 index readiness, or Mac/orangepi read-only library workflows.
---

# Brain Library

Use this skill for library indexing and display-readiness checks around the
rtime `brain` directory. The stable command surface is the
`brain-library` CLI package in `packages/brain-library`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/context-fabric-modules.zh-CN.md`
3. `docs/tooling-packaging.md`
4. `docs/brain-library-index.md`
5. `docs/logging-and-audit.md` when MCP run logs or audit archives are involved

For `brain`-side behavior, read `brain/CLAUDE.md` from the mounted library.

## Rules

- Keep scans and MCP tools read-only.
- Build SQLite/BM25 indexes only with an explicit `--out`; default to an
  output path outside `brain`.
- Do not edit Obsidian files unless the user explicitly confirms that write
  operation.
- Treat `brain` Markdown/filesystem as the human-editable source, while JSON
  manifests, SQLite/BM25, and vector indexes are derived surfaces.
- Use `brain-library scan` before proposing Obsidian, Zotero, or Web console
  display changes.
- Citation and DocPack quality must be reported as evidence fields, not hidden
  behind a single green status.
- Secrets, private identity data, runtime logs, generated indexes, and
  external Zotero credentials stay out of git.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool brain-library --profile mac
python -m pip install -e packages/brain-library
brain-library doctor <brain-root>
```

Read-only checks on Mac:

```bash
brain-library scan <brain-root> --sample-limit 20
brain-library docpacks <brain-root> --sample-limit 20
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | brain-library-mcp
```

Explicit derived index build and query:

```bash
# 首建/全量用 --force；日常重建用 --incremental(复用未变文档的行与向量,
# 只重处理新增/改动/删除,3万文档从~20min降到~20s;换嵌入模型时用 --force)。
brain-library index build <brain-root> --out /tmp/brain-library.sqlite --incremental
brain-library index status /tmp/brain-library.sqlite
brain-library index query /tmp/brain-library.sqlite "stellarator coil" --limit 5
```

Read-only checks on orangepi:

```bash
brain-library scan <brain-root> --sample-limit 20
brain-library docpacks <brain-root> --sample-limit 20
```

## 全文检索（已建索引，2026-06-18 起）

brain 已建好可用的 BM25 中文全文索引（jieba+FTS5，覆盖 md/txt/bib，约2021篇）。要"某概念/某事在哪个文档"时直接查，别再人工 grep 全库：

```bash
scripts/brain-search "守敬书院 院长助理"          # 包装脚本，默认索引，自动UTF-8
scripts/brain-search "仿星器 HTS 线圈" --limit 8
scripts/rebuild-brain-index.sh                    # brain 变动后重建（派生缓存）
```

- 索引在 brain/Obsidian 之外：`~/.local/state/rtime-assistant/brain-library/brain-library.sqlite`（不入 git、不显示在 ob）。
- 对外/跨进程 agent 走 MCP `library.index_query`，**index 参数用原生路径**（Windows 用 `C:/Users/...`，勿用 git-bash `/c/`）。
- 局限：**PDF/图片型课件未纳入**（无文本层），课程精读检索需先做 DocPack/OCR。
- 详见 `docs/brain-search-quickstart.md`。CLI 输出已强制 UTF-8（`cli.py:_force_utf8_streams`）。

## 向量混合检索（schema 4）与课程结构化查询

- **混合检索**：索引带向量时 `lib.search`/`index query` 默认走 hybrid（BM25 字面 + 语义向量，RRF 融合），同义/口语化提问也能命中。`--mode bm25|vector|hybrid` 可显式切换；无向量/缺模型自动降级纯 BM25，绝不报错。模型可插拔（默认 bge-small，可换 Qwen3-0.6B），用 `scripts/fetch-embed-model.sh` 拉模型、`pip install -e 'packages/brain-library[vector]'` 装依赖、`index build --embed` 重建。详见 `docs/brain-library-index.md` 的“Schema 4”节。
- **课程查询**：培养方案（`type: ustc-program`）课程表已结构化进 `courses` 表，用 `lib.courses` / `index courses`（`--code` 查哪些专业开某课、`--dept`+`--grade` 列某专业课表、`--min-credits`、`--required-only`）做精确查询，别用全文检索凑。

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/brain-library/src/brain_library/*.py
PYTHONPATH=packages/brain-library/src python -m pytest tests/test_brain_library_cli.py tests/test_brain_library_mcp.py tests/test_brain_library_vector.py -q
scripts/validate-codex-plugin.py plugins/brain-library
git diff --check
```
