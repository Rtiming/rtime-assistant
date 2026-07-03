# brain-library

Read-only library diagnostics and explicit derived index tools for a `brain`
workspace.

## Purpose

`brain-library` scans a `brain` root for Obsidian signals, Zotero clues,
DocPack readiness, PDF-manifest state, and derived SQLite/BM25 index readiness.
It can build an explicit derived index when `--out` is provided; it does not
modify Obsidian notes or sync Zotero data.

## Entry Commands

```bash
PYTHONPATH=packages/brain-library/src python -m brain_library doctor <brain-root>
PYTHONPATH=packages/brain-library/src python -m brain_library scan <brain-root> --sample-limit 20
PYTHONPATH=packages/brain-library/src python -m brain_library docpacks <brain-root>
PYTHONPATH=packages/brain-library/src python -m brain_library index build <brain-root> --out <derived.sqlite>
# 重建已有索引：--incremental 复用未变文档的行与向量(按 path+size+mtime 判定)，
# 只重处理新增/改动/删除，3万文档 ~20min → ~20s；换嵌入模型时改用 --force 全量。
PYTHONPATH=packages/brain-library/src python -m brain_library index build <brain-root> --out <derived.sqlite> --incremental
PYTHONPATH=packages/brain-library/src python -m brain_library index query <derived.sqlite> "query text"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | PYTHONPATH=packages/brain-library/src python -m brain_library.mcp_server
```

## Validation

```bash
python -m py_compile packages/brain-library/src/brain_library/*.py
PYTHONPATH=packages/brain-library/src python -m pytest tests/test_brain_library_cli.py tests/test_brain_library_mcp.py -q
scripts/module-submit-check.py --module brain-library
```

## Boundaries

- Default behavior is read-only.
- Derived SQLite/BM25 outputs must be explicit and rebuildable.
- Do not write memory, mutate Obsidian files, sync Zotero, or store secrets.
