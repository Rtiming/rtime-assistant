# Overview

Rtime Assistant for Obsidian is a thin side-panel adapter for the local
`rtime-assistant` runtime. It is meant for users who keep notes in Obsidian and
want to ask a local assistant about the active note, a text selection, or a
vault-scoped context hint without moving model execution into Obsidian.

## What It Owns

- Right-sidebar chat UI.
- Active note, selection, headings, tags, and link metadata collection.
- Bilingual UI labels for Simplified Chinese and English.
- Local HTTP calls to configured chat and health endpoints.
- Markdown-rendered answers and source cards.
- Private update checks from a trusted `release.json`, limited to the current
  vault plugin files.
- Explicit insertion of the last assistant answer at the cursor.

## What It Does Not Own

- Model keys or provider credentials.
- Retrieval, indexing, citation validation, or DocPack generation.
- Zotero sync or note auto-rewrites.
- Runtime process supervision.
- General-purpose downloads or automatic mobile enablement.

## Main References

- `docs/architecture.md`: module map and boundaries.
- `docs/workflows.md`: local development, simulation, packaging, and install.
- `docs/ui-guide.md`: sidebar controls and settings.
- `docs/troubleshooting.md`: common failures and checks.
