# brain-citation

Read-only Obsidian/Zotero citation crosswalk diagnostics.

## Purpose

`brain-citation` audits citekey and source-link consistency across Markdown,
BibTeX exports, Zotero URI clues, wikilinks, and DocPack citation anchors. It
is useful as a standalone quality gate before relying on notes for literature
review, DocPack evidence, or assistant answers.

## Entry Commands

```bash
PYTHONPATH=packages/brain-citation/src python -m brain_citation doctor <brain-root>
PYTHONPATH=packages/brain-citation/src python -m brain_citation scan <brain-root> --sample-limit 20
PYTHONPATH=packages/brain-citation/src python -m brain_citation panel <brain-root> --sample-limit 20
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | PYTHONPATH=packages/brain-citation/src python -m brain_citation.mcp_server
```

## Validation

```bash
python -m py_compile packages/brain-citation/src/brain_citation/*.py
PYTHONPATH=packages/brain-citation/src python -m pytest tests/test_brain_citation_cli.py tests/test_brain_citation_mcp.py -q
scripts/module-submit-check.py --module brain-citation
```

## Boundaries

- Read-only by default.
- Do not edit Obsidian notes, mutate DocPacks, access Zotero private databases,
  sync Zotero, or read secrets.
- Return paths, citekeys, counts, and review risks rather than copying source
  document bodies.
