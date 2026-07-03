# Rtime Assistant Obsidian Plugin Architecture

This directory is designed to stand on its own as an Obsidian plugin project.
The parent repository owns broader runtime, brain-library, citation, review,
and deployment surfaces; this plugin remains the Obsidian entry adapter.

## Module Map

```text
src/main.ts
  Plugin lifecycle, commands, settings tab, view activation, explicit insert.

src/view.ts
  Right-sidebar panel composition and chat state.

src/settings.ts
  User-facing preferences and defaults.

src/context.ts
  Active Obsidian file, editor selection, note body, and metadata collection.

src/pdf.ts
  Obsidian PDF/view introspection (private API): live current page via the pdf.js
  PDFViewer.currentPageNumber, the page a text selection sits on, and a DOM
  most-visible-page fallback. DOM access is feature-detected for the Node tests.

src/composer-contract.ts
  Composer templates, target module options, folder suggestions, route hints.

src/services/
  assistant-client.ts  Request body construction, health cache, assistant API.
  http.ts              Timeout, localhost normalization, retry policy.
  plugin-updater.ts    Private release manifest download and SHA-256 checks.
  response.ts          Compatible answer/source parsing.

src/ui/
  elements.ts          Reusable button and metadata pill creation.
  format.ts            Role/source icons and source metadata formatting.
  markdown.ts          Pure answer transforms: math-delimiter normalization
                       (\(…\)/\[…\] → $…$/$$…$$) and trailing source-block strip.

dev/
  smoke-gateway.mjs              Deterministic local HTTP gateway.
  local-gateway.mjs              Local runner bridge for real assistant calls.
  simulate-plugin.mjs            Build/artifact/contract simulation.
  simulate-composer-request.mjs  Focused composer request simulation.
  package-plugin.mjs             Copies installable plugin files into dist/.
```

## Boundaries

- The plugin may render UI, collect active-note context, call configured local
  HTTP endpoints, display answers/sources, and insert the last answer only after
  an explicit user action.
- It must not store provider keys, run model processes, mutate Zotero, write
  DocPacks, rewrite notes automatically, or become the retrieval/index engine.
- Its private updater may write only the current vault plugin files
  `manifest.json`, `main.js`, and `styles.css` after verifying a trusted
  `release.json`; it must not become a general-purpose file downloader.
- Backend services must treat `target_module` and `target_folder` as routing
  hints, not as authorization.

## Network Behavior

`src/services/http.ts` centralizes request behavior:

- `localhost` HTTP endpoints are normalized to `127.0.0.1` to avoid avoidable
  IPv6/IPv4 resolution delays on local setups.
- Requests use explicit timeouts.
- Transient failures retry only for network errors, `408`, `429`, and `5xx`.
- Retry delay is short and bounded; defaults favor one quick retry rather than
  long repeated waits.

`src/services/assistant-client.ts` adds a small health-check cache. Repeated
clicks on the sidebar Health button reuse the last result for the configured
cache window, while chat requests always call the configured chat endpoint.

`src/view.ts` prevents duplicate in-flight submissions from repeated Enter or
button clicks. This avoids accidental parallel requests without changing the
backend protocol.

## Standalone Project Checks

```bash
npm install
npm run check
npm run package:plugin
```

`npm run package:plugin` writes generated install files to
`dist/rtime-assistant/`. Keep `dist/` out of source commits unless a release
process explicitly asks for generated artifacts.
