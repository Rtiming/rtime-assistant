# AGENTS.md - apps/obsidian-rtime-assistant

This directory contains the Obsidian entry adapter for rtime-assistant.

## Boundaries

- Keep the Obsidian plugin a thin UI adapter.
- Do not put model keys, provider tokens, runtime logs, session stores, or
  generated indexes in this directory.
- Do not make the plugin mutate `brain`, Zotero, DocPacks, or rtime-hub facts
  by default.
- User-visible note writes must stay explicit, such as inserting the last
  assistant answer at the active editor cursor.
- Heavy retrieval, model execution, citation review, and context planning
  should remain in rtime-assistant backend surfaces.

## Validation

```bash
npm install
npm run check
```

For live local-gateway verification when `127.0.0.1:8765` is running:

```bash
npm run simulate:live
```

For standalone packaging:

```bash
npm run package:plugin
```

## Local Read Order

1. `README.md`
2. `docs/overview.md`
3. `docs/architecture.md`
4. `docs/workflows.md`
5. `docs/ui-guide.md` when changing sidebar controls or settings
6. `docs/troubleshooting.md` when changing endpoints, build/install behavior,
   or diagnostics
7. `docs/release-checklist.md` when packaging or extracting this plugin as its
   own project
8. `src/main.ts`
9. `src/view.ts`
10. `src/services/assistant-client.ts` and `src/services/http.ts` when changing
   request behavior
11. `src/composer-contract.ts` when changing task templates, target modules, or
   route hints

For repository handoff, also run from the repo root:

```bash
git diff --check
```
