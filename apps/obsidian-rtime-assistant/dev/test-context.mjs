// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Node tests for src/context.ts. The real Obsidian app is not available here,
 * so this uses a small runtime stub for the Obsidian classes used by instanceof.
 */
import assert from "node:assert/strict";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import esbuild from "esbuild";

const root = path.resolve(import.meta.dirname, "..");

function log(message) {
  console.log(`✓ ${message}`);
}

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-context-"));
try {
  const stub = path.join(tmpDir, "obsidian-stub.mjs");
  await writeFile(
    stub,
    `
export class App {}
export class TFile {
  constructor(path) {
    this.path = path;
    this.name = path.split("/").pop();
    const dot = this.name.lastIndexOf(".");
    this.basename = dot >= 0 ? this.name.slice(0, dot) : this.name;
    this.extension = dot >= 0 ? this.name.slice(dot + 1) : "";
    this.stat = { size: 1, ctime: 0, mtime: 0 };
  }
}
export class MarkdownView {
  constructor(file, editor) {
    this.file = file;
    this.editor = editor;
  }
}
`,
  );

  const wrapper = path.join(tmpDir, "context-wrapper.mjs");
  await writeFile(
    wrapper,
    `
export * from ${JSON.stringify(path.join(root, "src/context.ts"))};
export { MarkdownView, TFile } from "obsidian";
`,
  );

  const outfile = path.join(tmpDir, "context.mjs");
  await esbuild.build({
    entryPoints: [wrapper],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile,
    alias: { obsidian: stub },
    logLevel: "silent",
  });

  const {
    MarkdownView,
    TFile,
    captureMarkdownEditorSnapshot,
    collectAssistantContext,
    describeContextLocation,
    pendingSelectionFromSnapshot,
  } = await import(pathToFileURL(outfile).href);

  function editor(selection = "", cursor = { line: 0, ch: 0 }) {
    return {
      getSelection: () => selection,
      getCursor: () => cursor,
    };
  }

  function appFixture({
    activeFile = null,
    activeView = null,
    leaves = [],
    bodies = new Map(),
    extraFiles = [],
  }) {
    const files = new Map();
    for (const file of [
      activeFile,
      ...extraFiles,
      ...leaves.map((view) => view.file),
    ].filter(Boolean)) {
      files.set(file.path, file);
    }
    return {
      workspace: {
        activeLeaf: activeView ? { view: activeView } : null,
        getActiveFile: () => activeFile,
        getActiveViewOfType: (type) => (activeView instanceof type ? activeView : null),
        iterateAllLeaves: (callback) => {
          for (const view of leaves) {
            callback({ view });
          }
        },
      },
      vault: {
        getName: () => "test-vault",
        getAbstractFileByPath: (filePath) => files.get(filePath) ?? null,
        cachedRead: async (file) => bodies.get(file.path) ?? "",
      },
      metadataCache: {
        getFileCache: () => ({ headings: [], tags: [], links: [] }),
      },
    };
  }

  const settings = {
    includeSelection: true,
    includeActiveNoteBody: true,
    contextMode: "current-note",
    maxNoteChars: 12000,
  };

  const markdown = new TFile("notes/current.md");
  const markdownView = new MarkdownView(markdown, editor("live selected text", { line: 3, ch: 5 }));
  let app = appFixture({
    activeFile: markdown,
    leaves: [markdownView],
    bodies: new Map([[markdown.path, "# Current"]]),
  });
  let context = await collectAssistantContext(app, settings);
  assert.equal(context.active_file.path, "notes/current.md");
  assert.equal(context.selection.text, "live selected text");
  assert.equal(context.note.text, "# Current");
  log("context collector reads selection from a matching Markdown leaf when the sidebar has focus");

  const snapshot = captureMarkdownEditorSnapshot(editor("snapshotted selection", { line: 4, ch: 2 }), markdownView);
  app = appFixture({ activeFile: null, extraFiles: [markdown] });
  context = await collectAssistantContext(app, settings, snapshot);
  assert.equal(context.active_file.path, "notes/current.md");
  assert.equal(context.selection.text, "snapshotted selection");
  log("context collector falls back to the last Markdown editor snapshot");

  const preserved = captureMarkdownEditorSnapshot(editor("", { line: 4, ch: 2 }), markdownView, snapshot);
  assert.equal(preserved.selectionText, "snapshotted selection");
  app = appFixture({ activeFile: markdown, leaves: [markdownView], bodies: new Map([[markdown.path, "# Current"]]) });
  const pending = pendingSelectionFromSnapshot(app, preserved);
  context = await collectAssistantContext(app, settings, preserved, { pendingSelection: pending });
  assert.equal(context.pending_selection.text, "snapshotted selection");
  assert.equal(context.selection.text, "live selected text");
  log("empty editor selection does not clear the locked pending selection");

  const pdf = new TFile("papers/example.pdf");
  const pdfView = {
    file: pdf,
    getState: () => ({ page: 12 }),
  };
  app = appFixture({ activeFile: null, activeView: pdfView });
  context = await collectAssistantContext(app, settings);
  assert.equal(context.pdf.page, 12);
  const pdfLocation = describeContextLocation(app);
  assert.equal(pdfLocation.label, "example.pdf · p.12");
  assert.equal(pdfLocation.title, "papers/example.pdf#page=12");
  log("context summary shows the matching PDF file page");

  // The live pdf.js currentPageNumber (what Obsidian's toolbar shows) must win
  // over the lagging persisted getState().page. The live value sits two pdfViewer
  // hops deep: viewer.child.pdfViewer(ObsidianViewer).pdfViewer(PDFViewer).
  const livePdfView = {
    file: pdf,
    viewer: { child: { pdfViewer: { pdfViewer: { currentPageNumber: 5 } } } },
    getState: () => ({ page: 12 }),
  };
  app = appFixture({ activeFile: null, activeView: livePdfView });
  context = await collectAssistantContext(app, settings);
  assert.equal(context.pdf.page, 5);
  assert.equal(describeContextLocation(app).label, "example.pdf · p.5");
  log("PDF page prefers the live currentPageNumber over the lagging persisted state");

  app = appFixture({ activeFile: markdown, leaves: [markdownView] });
  const markdownLocation = describeContextLocation(app);
  assert.equal(markdownLocation.label, "current.md · L4:C6");
  assert.equal(markdownLocation.title, "notes/current.md:4:6");
  log("context summary shows Markdown cursor line and column");
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
