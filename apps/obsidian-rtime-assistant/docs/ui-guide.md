# UI Guide

The plugin renders a right-sidebar assistant panel. The note stays visible while
the user asks questions.

## Header

- Bot icon and title identify the panel.
- Endpoint text shows the configured chat endpoint without the protocol.
- Status pill shows idle, working, ready, or backend error.

## Context Bar

- Selection: use editor selected text as the primary context.
- Current note: include the active note context.
- Vault: send vault-scoped mode hints to the backend.

The context summary shows the active file and position. For PDFs the page is the
page the selected text sits on when text is selected; with no selection it tracks
the live page Obsidian's own toolbar shows (the pdf.js `currentPageNumber`, with a
most-visible-page DOM fallback). Markdown files display the editor line and column.
The mode dropdown controls whether the request prefers current note, selection, or
vault-scoped hints.

The selection chip is deliberately independent of Obsidian's visible highlight.
If the user selects text and then clicks into the composer, the plugin keeps a
locked pending selection until the user captures a new selection, clears it, or
sends the request. The preview line shows the file/page or line/column plus the
first part of the locked text.

## Composer

The paperclip control accepts images, PDFs, Markdown/TXT, Office files, and
CSV/table files. File chips are sent as `context.attachments[]` metadata and
small extracted text/previews where available. Added files are temporary
next-message context: after the message is sent, the composer attachment list is
cleared. Durable brain filing is intentionally not mixed into the chat chip UI;
use a separate intake workflow when a file should become library material.

The advanced panel includes memory controls:

- `记住`: mark the current turn as a memory-candidate request.
- `不记`: disable memory generation for the current turn.
- `review`: ask for the review path without mutating long-term memory.

Assistant cards may show trace metadata such as first delta and total stream
duration when the gateway provides timing evidence.

- Task buttons: ask, summarize, explain, related, citation review.
- Target module: auto, brain, literature, project, runtime.
- Target folder: optional routing hint; the plugin does not read or write that
  folder.
- Send behavior is configurable:
  - Enter sends and Shift+Enter inserts newline.
  - Cmd/Ctrl+Enter sends and Enter inserts newline.

## Answer Rendering

Answers render through Obsidian's `MarkdownRenderer`, with two transforms applied
first (see `src/ui/markdown.ts`):

- Math delimiters: models often emit OpenAI-style `\(…\)` / `\[…\]`, which Obsidian
  does not typeset. These are rewritten to `$…$` / `$$…$$` so MathJax renders them;
  code spans and fenced blocks are left untouched, and existing `$`-math is kept.
- Trailing sources: the backend appends a `来源：` list and also returns it as
  structured sources. The inline list is stripped from the rendered answer (only a
  genuine label + citation block, never plain prose) and shown instead as a
  collapsed-by-default "来源" toggle. Copy/Insert still use the full original text.

The same transforms run during streaming and on the final render, so the answer
does not visibly change when the stream completes. Layout is intentionally compact
for the narrow side panel (tightened line height, list/table/heading spacing, and
math block margins).

## Actions

- Send: submits the prompt to the configured chat endpoint.
- Insert: inserts the last assistant answer into the active Markdown editor.
- Health: checks the configured health endpoint, using the short health cache.
- Settings: opens the Obsidian plugin settings tab.

## Settings

Settings include language, endpoints, context defaults, module/folder defaults,
selection/body inclusion, note character limit, request timeout, retry count,
retry delay, health cache, send shortcut, composer clearing, focus retention,
and auto-scroll.
