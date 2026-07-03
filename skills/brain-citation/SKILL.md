---
name: brain-citation
description: Use when auditing, reviewing, or planning Obsidian/Zotero citation workflows for the rtime `brain` library, especially citekey crosswalks, BibTeX coverage, Zotero URI clues, Obsidian wikilinks, DocPack citation anchors, or Mac/orangepi read-only citation review.
---

# Brain Citation

Use this skill for read-only citation crosswalk checks around the rtime
`brain` directory. The stable command surface is the `brain-citation` CLI
package in `packages/brain-citation`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/context-fabric-modules.zh-CN.md`
3. `docs/tooling-packaging.md`
4. `docs/brain-citation.md`
5. `docs/brain-library-index.md`
6. `docs/logging-and-audit.md` when MCP run logs or audit archives are involved

For `brain`-side behavior, read `brain/CLAUDE.md` from the mounted library.

## Rules

- Keep scans and MCP tools read-only.
- Treat Obsidian and Zotero as linked review surfaces; do not edit Obsidian
  files or sync Zotero from this tool.
- Report missing citekey/BibTeX coverage as review risks with path and line
  evidence.
- Keep API keys, Zotero credentials, local runtime logs, and private exports
  out of git and ordinary logs.
- Use `brain-citation panel` before proposing Obsidian plugin or Zotero sync
  changes that depend on citation readiness.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool brain-citation --profile mac
python -m pip install -e packages/brain-citation
brain-citation doctor <brain-root>
```

Read-only checks on Mac:

```bash
brain-citation scan <brain-root> --sample-limit 20
brain-citation panel <brain-root> --sample-limit 20
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | brain-citation-mcp
```

Read-only checks on orangepi:

```bash
brain-citation scan <brain-root> --sample-limit 20
brain-citation panel <brain-root> --sample-limit 20
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/brain-citation/src/brain_citation/*.py
PYTHONPATH=packages/brain-citation/src python -m pytest tests/test_brain_citation_cli.py tests/test_brain_citation_mcp.py -q
scripts/validate-codex-plugin.py plugins/brain-citation
git diff --check
```
