# Release Checklist

Use this checklist when extracting or submitting `apps/obsidian-rtime-assistant`
as a standalone Obsidian plugin project.

## Source Boundary

- Keep `manifest.json`, `package.json`, `versions.json`, `tsconfig.json`,
  `esbuild.config.mjs`, `styles.css`, `src/`, `dev/`, `docs/`, `README.md`, and
  `AGENTS.md`.
- Do not commit `node_modules/`, `dist/`, local logs, Obsidian workspace state,
  provider keys, runtime state, or generated indexes.
- Keep backend/model/retrieval changes outside this plugin project.

## Validation

```bash
npm install
npm run check
npm run simulate:live
```

`simulate:live` requires the local gateway on `127.0.0.1:8765`. If the gateway
is not running, `npm run check` is still the deterministic offline gate.

## Two delivery paths — pick the right one

There are TWO ways to get a build onto a machine. Picking the wrong one is the
classic mistake (a manual vault copy looks like it worked but the user never
sees the change):

| Path | When | How |
| --- | --- | --- |
| **Channel release** (normal) | Any change a real user installs — they run the private updater and watch the 版本状态 panel | `npm run publish:release` (see below). **Must bump version.** |
| **Vault copy** (throwaway) | Only quick local smoke-testing in your OWN dev vault | copy `dist/rtime-assistant/` into `<vault>/.obsidian/plugins/rtime-assistant/` |

Do NOT hand-copy `main.js` into a user's vault as a "release": it does not bump
the version (the updater never offers it), the running plugin can overwrite
`data.json`, and the next `检查并安装` reverts your file. Use the channel.

## Channel release (the normal path)

1. **Bump the version** for any user-facing change — old clients discover updates
   by `manifest.version` only. Edit all three to the same new value:
   `manifest.json`, `versions.json` (add `"<new>": "<minAppVersion>"`), `package.json`.
2. **Validate**: `npm run check`.
3. **Publish in one command**:

   ```bash
   npm run publish:release
   ```

   This runs `package:plugin` (rebuilds `main.js` + regenerates `release.json`
   with a fresh `build_id`), `scp`s the four files to the static channel dir,
   and verifies every published file's SHA-256 against `release.json` (the
   updater rejects a mismatch). It warns if you forgot to bump the version.

   Targets are env-overridable (defaults shown):

   ```text
   RTIME_OBSIDIAN_RELEASE_SSH   <runtime-host>
   RTIME_OBSIDIAN_RELEASE_DIR   ~/.local/share/rtime-assistant/plugin-release/rtime-assistant
   RTIME_GATEWAY_URL            http://127.0.0.1:8765
   ```

   The gateway serves this dir at `/api/obsidian/plugin-release/` (see
   `GATEWAY_PLUGIN_RELEASE_DIR` in `apps/assistant-gateway/gateway.py`).

4. **On the client**, in Obsidian: `刷新版本信息` → `检查并安装` → `重载插件`.

The directory must keep these four files together (handled by the script):

```text
release.json
manifest.json
main.js
styles.css
```

The plugin downloads the manifest, verifies the SHA-256 and size of
`manifest.json`, `main.js`, and `styles.css`, backs up the previous files under
`.obsidian/plugins/rtime-assistant/updates/<timestamp>/`, and then writes the new
files into the active vault.

Use `刷新版本信息` to read only `release.json` and update the displayed remote
version/build status. After `检查并安装` succeeds, click `重载插件`. From `0.7.2`
onward `重载插件` cascades through three reload methods so it works across
platforms: Obsidian's built-in `app:reload` command, then a targeted plugin
disable/enable, then a hard `window.location.reload()` — earlier builds called
only `window.location.reload()`, which was observed to silently no-op in some
Obsidian/Electron states (the button "did nothing"). Because the reload button
is itself the code being replaced, upgrading FROM a client at `0.7.1` or older
still needs one manual `Cmd/Ctrl+R` (or quit + reopen Obsidian) the first time;
after the fixed build is live the button works on its own.

Version detection is intentionally two-layered:

- Bump the release `version` for user-visible feature/fix releases. This keeps
  older installed plugins, including versions before `0.6.1`, able to discover
  the update because they only compare `manifest.version`.
- Starting with `0.6.1`, the settings tab also compares `build_id`. If the
  semantic version is unchanged but the remote `build_id` differs from the last
  installed build, the status shows `同版本有新构建` / `available-build`.
- Do not rely on same-version `build_id` changes to distribute fixes to clients
  that may still be running `0.6.0` or older; publish a patch version instead.
- If the running client is older than `0.6.2`, installing the release may still
  require one manual Obsidian restart because the old reload button itself is
  the code being replaced.

The current plugin manifest still has `isDesktopOnly: true`. The updater path is
kept mobile-compatible where practical, but Android usage requires a separate
audit and a manifest change before Obsidian Mobile will load the plugin.

Minimal static-hosting example after copying the generated folder to a server:

```bash
python3 -m http.server 8088 --directory /srv/rtime-plugin-release
```

If the copied folder is `/srv/rtime-plugin-release/rtime-assistant/`, set the
plugin update URL to:

```text
http://<server>:8088/rtime-assistant/
```

For a public Shanghai server, put the same directory behind HTTPS and keep the
URL stable. Do not put provider keys, Obsidian workspace state, logs, or vault
content in the static release directory.
