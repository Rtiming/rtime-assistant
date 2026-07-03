# Troubleshooting

## Sidebar Does Not Appear

Run a production build and confirm the vault plugin folder contains:

```text
manifest.json
main.js
styles.css
```

Then reload the plugin or restart Obsidian.

## Private Update Fails

Confirm the update URL returns a generated release manifest:

```bash
curl -fsS http://127.0.0.1:8088/rtime-assistant/release.json
```

The `release.json` file must sit beside the exact `manifest.json`, `main.js`,
and `styles.css` that it describes. If any file was edited after packaging, the
plugin rejects the update with a SHA-256 or size mismatch. After a successful
install, reload the plugin or restart Obsidian; the running JavaScript bundle
does not hot-swap itself. Starting with `0.6.2`, the reload button refreshes the
whole Obsidian window so the app rereads the files from disk. If the loaded
plugin is still older than `0.6.2`, restart Obsidian once after installing the
update.

If `刷新版本信息` says the remote is current even though `main.js` changed, check
the remote release metadata:

```bash
curl -fsS http://127.0.0.1:8765/api/obsidian/plugin-release/release.json | jq '{version,build_id}'
```

Clients before `0.6.1` only detect updates when `version` is newer than the
loaded `manifest.version`; they ignore `build_id`. For fixes that must reach
those clients, bump `manifest.json` and `package.json` to the next patch
version, run `npm run package:plugin`, and publish the generated
`dist/rtime-assistant/` folder to the Orange Pi release directory.

Android Obsidian will not load the current plugin because `manifest.json` still
sets `isDesktopOnly: true`. Fix that through a separate mobile compatibility
pass rather than by only changing the private update URL.

## Backend Unavailable

Check the configured health endpoint:

```bash
curl -sS http://127.0.0.1:8765/healthz
```

From this project, also run:

```bash
npm run simulate:live
```

If live health fails but `npm run check` passes, the plugin build is fine and
the local gateway/runtime needs attention.

## Slow Local Requests

- Prefer `http://127.0.0.1:8765/...` over `localhost`.
- Keep retry count at `0` or `1` unless the local gateway is flaky.
- Lower active note character limit for very large notes.
- Use Selection mode for narrow questions.

The plugin retries only network errors, `408`, `429`, and `5xx`. Successful
requests do not incur retry delay.

## Markdown Looks Raw

The sidebar uses Obsidian `MarkdownRenderer`. Rebuild and reload the plugin if
answers appear as raw Markdown after a source change:

```bash
npm run build
```

## Math/LaTeX Not Rendering

If formulas show as raw source (e.g. `\hat h_i`, leaked `\sum`, missing
subscripts), the model emitted OpenAI-style `\(…\)` / `\[…\]` delimiters, which
Obsidian's MathJax ignores. `src/ui/markdown.ts` normalizes these to `$…$` /
`$$…$$` before rendering — exercised by `npm run test:markdown`. If math still
leaks: confirm the loaded plugin is at least `0.7.11`; check the delimiters are
not inside an inline-code span or fenced block (those are deliberately left
verbatim); and verify Obsidian's own math rendering works in a normal note.

## PDF Page Is Wrong in Context

The page shown for a PDF should match Obsidian's toolbar (no selection) or the
page the selected text sits on (with a selection). The live value is read from
the private pdf.js `PDFViewer.currentPageNumber` (`src/pdf.ts`); if a future
Obsidian release moves that path, a DOM most-visible-page fallback keeps it close.
If the page is still off, capture the Obsidian version — the private object path
may have changed and the fallback selector may need updating.

## Duplicate Messages

The view ignores duplicate submit attempts while a prompt is already in flight.
If duplicate answers still appear, check whether multiple Obsidian plugin
copies or multiple gateway processes are active.
