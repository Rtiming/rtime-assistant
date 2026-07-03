# Workflows

Run all commands from `apps/obsidian-rtime-assistant`.

## Local Development

```bash
npm install
npm run dev
```

`npm run dev` watches `src/main.ts` and writes `main.js`.

## Deterministic Check

```bash
npm run check
```

This runs typecheck, production build, keyboard/composer/response simulations,
and a temporary smoke gateway request. It does not call a real model.

## Live Gateway Health

```bash
npm run simulate:live
```

This includes the deterministic simulation and then checks
`GET http://127.0.0.1:8765/healthz`. It does not send a live chat prompt.

## Package For Obsidian

```bash
npm run package:plugin
```

Generated install files are written to:

```text
dist/rtime-assistant/
```

The package includes:

```text
manifest.json
main.js
styles.css
release.json
README.txt
```

Copy that folder into:

```text
<vault>/.obsidian/plugins/rtime-assistant/
```

Then reload the plugin or restart Obsidian.

For private cross-device updates, host the same `dist/rtime-assistant/` folder
on Orange Pi or a private HTTPS server. In the plugin settings, set the private
update URL to either:

```text
http://127.0.0.1:8088/rtime-assistant/
https://example.internal/rtime-assistant/release.json
```

The settings update button downloads `release.json`, verifies the hashes for
the three Obsidian runtime files, backs up the current files, and writes the
new files into the active vault. Reload the plugin or restart Obsidian after
the update. The current manifest is desktop-only, so this workflow targets
desktop Obsidian until mobile compatibility is audited.

## Composer Contract Simulation

```bash
npm run simulate:composer -- --dry-run --template citation-review --module brain --folder papers/zotero
```

Use this when changing templates, target modules, or folder routing hints.
