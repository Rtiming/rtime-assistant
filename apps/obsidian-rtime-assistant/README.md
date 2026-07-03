# Rtime Assistant for Obsidian

Obsidian side-panel adapter for the `rtime-assistant` runtime.

This plugin follows the Obsidian sample-plugin TypeScript/esbuild shape and
keeps the plugin as a thin UI layer. It collects active-note context, sends it
to a configured local rtime assistant HTTP endpoint, displays the response in a
right-sidebar view, and can insert the last answer into the active editor when
the user explicitly clicks the insert command.

## Current Scope

- Right sidebar `Rtime Assistant` view.
- Chinese-first sidebar UI with an English switch in Obsidian plugin settings.
- Compact icon-led controls for context scope, quick actions, task mode,
  status, message roles, and source cards.
- Markdown-rendered chat messages using Obsidian's native renderer.
- Configurable composer behavior: Enter-send or Cmd/Ctrl+Enter-send, composer
  clearing, focus retention, and auto-scroll.
- Configurable local-network behavior: request timeout, short transient retry,
  retry delay, and health-check cache.
- Sidebar settings button that opens the plugin settings tab.
- Current note/PDF path, PDF page when available, selected text, active note
  body, headings, tags, and link metadata in request payloads.
- Locked selection preview: once text is captured, clicking into the sidebar
  composer no longer clears the text that will be sent.
- Composer attachments for images, PDFs, Markdown/TXT, Office files, and CSV
  metadata. Added files are sent as temporary context with the next message and
  are cleared after submit; durable filing is handled by separate intake
  workflows, not the chat attachment chip.
  Images carry bounded base64 content for the gateway plus a preview; sent user
  messages render image thumbnails in the conversation history.
- Conversation titles use short provisional/generated summaries instead of
  copying the user's prompt text.
- Streaming responses carry local/gateway trace timing when the gateway returns
  it, so slow answers can be split into queue, first chunk, first delta, paint,
  and completion timing.
- Memory status controls expose remember/skip/review intent for the current
  turn; they only create safe request metadata or candidates and do not edit
  long-term memory directly.
- Configurable HTTP chat and health endpoints.
- Explicit insert-last-answer command.
- Read-only-by-default library/citation boundaries.

Out of scope for this first version:

- Starting or supervising long-running production model/backend services.
- Writing Obsidian notes automatically.
- Syncing Zotero or mutating DocPacks.
- Reading provider secrets.
- Streaming responses.

## Project Shape

This directory is intentionally self-contained enough to be extracted as a
standalone Obsidian plugin project. The main module split is:

```text
src/main.ts                Obsidian plugin lifecycle and commands
src/view.ts                sidebar UI and chat state
src/settings.ts            settings tab and defaults
src/context.ts             active-note context collection
src/composer-contract.ts   composer templates and route hints
src/services/              HTTP client, assistant client, response parsing
src/ui/                    reusable UI helpers and source formatting
dev/                       smoke gateway, simulations, packaging helper
docs/                      plugin-specific architecture and release notes
```

See `docs/overview.md`, `docs/architecture.md`, `docs/workflows.md`,
`docs/ui-guide.md`, `docs/troubleshooting.md`, and
`docs/release-checklist.md` before submitting the plugin as its own project.

## Backend Contract

Default chat endpoint:

```text
POST http://127.0.0.1:8765/api/obsidian/chat
```

Request body:

```json
{
  "schema_version": 1,
  "entry": "obsidian",
  "message": "Summarize this note",
  "context": {
    "vault": {"name": "brain"},
    "active_file": {"path": "notes/example.md"},
    "selection": {"text": "...", "chars": 120},
    "note": {"text": "...", "chars": 4000, "truncated": false},
    "metadata": {"headings": [], "tags": [], "links": []}
  },
  "options": {
    "context_mode": "current-note",
    "task_mode": "ask",
    "template_id": "ask",
    "target_module": "auto",
    "target_folder": "",
    "ui_language": "zh-CN",
    "include_selection": true,
    "include_active_note_body": true
  }
}
```

The sidebar composer currently includes built-in templates for ask, summarize,
explain, related-material lookup, and citation review. These options are exposed
through `src/composer-contract.ts` so the visible template strip can be replaced
later without changing the request contract. It also sends optional routing
hints through `target_module` and `target_folder`; the plugin does not read or
write those folders directly.

Response body can be any of these compatible shapes:

```json
{"answer": "...", "sources": [{"title": "note", "path": "notes/example.md"}]}
```

```json
{"text": "...", "citations": [{"path": "papers/example.md", "line": 42}]}
```

```json
{"choices": [{"message": {"content": "..."}}]}
```

## Development

```bash
cd apps/obsidian-rtime-assistant
npm install
npm run dev
```

For a local smoke gateway that lets the plugin talk to a real HTTP endpoint
without starting the full assistant runtime:

```bash
npm run smoke:gateway
```

It listens on the default plugin endpoints:

```text
GET  http://127.0.0.1:8765/healthz
POST http://127.0.0.1:8765/api/obsidian/chat
```

The smoke gateway only echoes received Obsidian context into a structured
answer and source cards. It does not run retrieval, models, or note writes.

For deterministic local simulation:

```bash
npm run simulate
```

The simulation checks the composer keyboard matrix, built plugin artifacts, and
the smoke gateway request/response contract on a temporary local port. It does
not invoke a real model. To also verify the live gateway health on
`127.0.0.1:8765`:

```bash
npm run simulate:live
```

For the full standalone project gate:

```bash
npm run check
```

To simulate only the composer route hints:

```bash
npm run simulate:composer -- --dry-run --template citation-review --module brain --folder papers/zotero
npm run simulate:composer -- --template related --module literature --folder knowledge/research
```

For a real local gateway that keeps the same HTTP contract and invokes a
configured assistant runner:

```bash
npm run gateway
```

Useful runner environment:

```text
RTIME_OBSIDIAN_RUNNER=remote-claude-kimi
RTIME_OBSIDIAN_REMOTE_NODE=orangepi
RTIME_OBSIDIAN_REMOTE_WORKDIR=<brain-root>
RTIME_OBSIDIAN_REMOTE_CLI=~/.local/bin/claude-kimi
RTIME_OBSIDIAN_RUNNER_TIMEOUT_MS=180000
```

The gateway also supports `RTIME_OBSIDIAN_RUNNER=claude`, `codex`, or `auto`.
It returns only the final user-facing answer to Obsidian and keeps model keys
outside plugin settings.

For manual installation into a development vault, build once and copy these
files into:

```text
<vault>/.obsidian/plugins/rtime-assistant/
```

Required files:

```text
manifest.json
main.js
styles.css
```

To prepare an installable folder:

```bash
npm run package:plugin
```

`npm run package:plugin` also writes `dist/rtime-assistant/release.json`. Host
the whole `dist/rtime-assistant/` folder on Orange Pi or a private server. The
assistant gateway can serve the same folder at
`http://<gateway-host>:8765/api/obsidian/plugin-release/`; set the plugin's private
update URL to that directory or directly to a `release.json` file. The settings
tab can then download `manifest.json`,
`main.js`, and `styles.css`, verify their SHA-256 hashes from `release.json`,
back up the current files, and install the update into the current vault's
plugin folder. Reload the plugin or restart Obsidian after installing.

The updater is intended for trusted private HTTP/HTTPS locations such as a
Tailscale Orange Pi URL or a Shanghai server static directory. It stores only
the URL and does not store credentials. The current manifest is still
`isDesktopOnly: true`, so Android Obsidian will not load this plugin until a
separate mobile compatibility pass removes that desktop-only boundary.

`npm run build` creates `main.js` beside `manifest.json` and `styles.css` for
Obsidian compatibility. `main.js` and `dist/rtime-assistant/` are generated
artifacts, not source files.

Use a disposable development vault first. Do not develop against the primary
`brain` vault until the backend and insert behavior have been verified.

## Network Tuning

The plugin defaults to `127.0.0.1` endpoints. If a user enters `localhost`, the
HTTP client normalizes it to `127.0.0.1` for plain HTTP requests to avoid local
IPv6/IPv4 resolution delays on some machines.

Settings expose request timeout, transient retry count, retry delay, and
health-check cache duration. Retries apply only to network errors, `408`, `429`,
and `5xx` responses. Normal successful requests do not wait for retry logic.
The sidebar also ignores duplicate submits while one prompt is already in
flight.

## v0.3（2026-06-12）

1. **传输层换Node直连**：`requestUrl`/`window.fetch`都走Chromium网络栈、遵循macOS系统代理（如127.0.0.1:7890），代理无Tailscale规则时打到orangepi的请求会被回502。v0.3起普通请求、健康检查、SSE流式全部优先走Node `http`直连（`src/services/transport.ts`），系统代理不再影响；无Node环境自动回退旧路径。
2. **界面紧凑化**：单行头部（标题+状态+图标按钮：清空/检查/设置）、上下文一行（当前文件+范围下拉）、模块/文件夹收进"高级"折叠区；消息卡片化，助手消息悬停出现**复制/插入**按钮；来源默认折叠成"来源 N"，展开后逐条**可点击跳转**（按basename经vault链接解析，PDF带`#page=`）。
3. **流式渲染提速**：流式期间只更新最后一张卡片的纯文本（80ms节流+光标动画），定稿后一次性Markdown渲染（公式/代码/表格正常），不再每token全量重渲历史。
4. **自检接口（agent测试通道）**：命令"运行后端自检"或向插件目录写`selftest-request.json`（`{"id":"..."}`，20秒内被监视器拾取），插件用真实链路跑健康检查/非流式问答/流式问答/Markdown渲染四项并写`selftest-report.json`。设置项"自检文件监视"默认开。命令行侧另有`npm run e2e:gateway`直接以插件client代码打真实网关。

## v0.4（2026-06-12，run-10会话系统）

1. **聊天记录持久化**：会话存插件目录`conversations.json`（上限50会话×100条消息，超限裁最旧；防抖≥2秒原子写：tmp+rename，崩溃可从tmp恢复）。重开侧栏/重启Obsidian自动恢复上次会话全部消息（Markdown渲染）。头部新增**会话下拉**（按最近更新排序、标题取首问前20字）+新对话/删除当前会话按钮；原"清空对话"语义改为**新对话**（旧会话留在历史里）。
2. **续聊**：发送时自动携带`conversation_id`+最近6轮对话（只带文本、按4000字符预算从新往旧截断、错误卡不入history）。"它/上面说的"等指代由网关"此前对话回顾"提示节解析。
3. **离开即停语义完善**：切换会话/新对话/删除会话/关侧栏都会取消在途请求（generation计数防错位）：被取消的回答保留已流出的部分文本，空占位则静默移除，不再产生误导性错误卡片。
4. **自检升至五项**：新增followup项——同一`conversation_id`两问，第二问只有靠history里的第一问才能答出，校验续聊链路端到端可用。
5. 配套：`rtime_chat.py --conversation/--history-file`（agent续聊回归）、`npm run test:conversations`（会话存储节点测试，已入`npm run check`）。
