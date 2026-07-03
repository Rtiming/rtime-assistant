// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { App, MarkdownView, TFile } from "obsidian";
import type { Editor, EditorPosition, MarkdownFileInfo } from "obsidian";
import { readPdfPageFromSelection, readViewerPage, readViewFile } from "./pdf";
import type {
  ActiveFilePayload,
  AssistantAttachment,
  AssistantContext,
  AssistantRuntimeContext,
  MemoryEvents,
  PendingSelection,
  RtimeAssistantSettings,
} from "./types";

export interface EditorContextSnapshot {
  filePath: string;
  selectionText: string;
  cursor: EditorPosition | null;
  pdfPage: number | null;
  updatedAt: number;
}

export interface ContextLocationDisplay {
  label: string | null;
  title: string;
  hasFile: boolean;
  isPdf: boolean;
}

export interface CollectContextExtras {
  pendingSelection?: PendingSelection | null;
  attachments?: AssistantAttachment[];
  memory?: MemoryEvents;
  runtime?: AssistantRuntimeContext;
}

export async function collectAssistantContext(
  app: App,
  settings: RtimeAssistantSettings,
  snapshot: EditorContextSnapshot | null = null,
  extras: CollectContextExtras = {},
): Promise<AssistantContext> {
  const activeFile = resolveContextFile(app, snapshot);
  const markdownView = activeFile ? findMarkdownViewForFile(app, activeFile) : null;
  const isMarkdown = activeFile instanceof TFile && activeFile.extension.toLowerCase() === "md";
  const isPdf = activeFile instanceof TFile && activeFile.extension.toLowerCase() === "pdf";
  const selectionText = settings.includeSelection
    ? readSelectionText(markdownView?.editor, activeFile, snapshot, extras.pendingSelection)
    : "";

  let note: AssistantContext["note"] = null;
  if (
    isMarkdown &&
    settings.includeActiveNoteBody &&
    settings.contextMode !== "selection"
  ) {
    const source = await app.vault.cachedRead(activeFile);
    const truncated = truncateText(source, settings.maxNoteChars);
    note = {
      text: truncated.text,
      chars: source.length,
      truncated: truncated.truncated,
    };
  }

  const cache = activeFile instanceof TFile ? app.metadataCache.getFileCache(activeFile) : null;
  const result: AssistantContext = {
    vault: {
      name: app.vault.getName(),
    },
    active_file: activeFile instanceof TFile ? filePayload(activeFile) : null,
    selection: selectionText
      ? {
          text: selectionText,
          chars: selectionText.length,
        }
      : null,
    note,
    metadata: {
      headings:
        cache?.headings?.slice(0, 40).map((heading) => ({
          heading: heading.heading,
          level: heading.level,
          line: heading.position?.start.line ?? null,
        })) ?? [],
      tags:
        cache?.tags?.slice(0, 40).map((tag) => ({
          tag: tag.tag,
          line: tag.position?.start.line ?? null,
        })) ?? [],
      links:
        cache?.links?.slice(0, 40).map((link) => ({
          link: link.link,
          line: link.position?.start.line ?? null,
        })) ?? [],
    },
    pdf: isPdf ? { page: readCurrentPdfPage(app, activeFile, snapshot) } : undefined,
    requested_mode: settings.contextMode,
    local_time: new Date().toISOString(),
  };
  if (extras.pendingSelection) {
    result.pending_selection = extras.pendingSelection;
  }
  if (extras.attachments?.length) {
    result.attachments = extras.attachments;
  }
  if (extras.memory) {
    result.memory = extras.memory;
  }
  if (extras.runtime) {
    result.runtime = extras.runtime;
  }
  return result;
}

export function captureCurrentContextSnapshot(
  app: App,
  previous: EditorContextSnapshot | null = null,
): EditorContextSnapshot | null {
  const markdownView = app.workspace.getActiveViewOfType(MarkdownView);
  if (markdownView?.file) {
    return snapshotFromEditor(markdownView.editor, markdownView.file, previous);
  }

  const activeFile = app.workspace.getActiveFile() ??
    readViewFile(app.workspace.activeLeaf?.view as unknown);
  if (activeFile instanceof TFile && activeFile.extension.toLowerCase() === "pdf") {
    const domSelection = readDomSelection();
    if (domSelection) {
      // The page a quote belongs to is the page the selection sits on — not the
      // viewer's "current" page (they differ when a page spans the viewport edge).
      return {
        filePath: activeFile.path,
        selectionText: domSelection,
        cursor: null,
        pdfPage: readPdfPageFromSelection() ?? readCurrentPdfPage(app, activeFile, previous),
        updatedAt: Date.now(),
      };
    }
    // No live selection: carry a prior selection (and its page) if it's the same
    // file, otherwise just track Obsidian's current page.
    const carriedSelection = previous?.filePath === activeFile.path ? previous.selectionText : "";
    return {
      filePath: activeFile.path,
      selectionText: carriedSelection,
      cursor: null,
      pdfPage: carriedSelection ? previous!.pdfPage : readCurrentPdfPage(app, activeFile, previous),
      updatedAt: Date.now(),
    };
  }

  return previous;
}

export function captureMarkdownEditorSnapshot(
  editor: Editor,
  info: MarkdownView | MarkdownFileInfo,
  previous: EditorContextSnapshot | null = null,
): EditorContextSnapshot | null {
  if (!(info.file instanceof TFile)) {
    return null;
  }
  return snapshotFromEditor(editor, info.file, previous);
}

export function pendingSelectionFromSnapshot(
  app: App,
  snapshot: EditorContextSnapshot | null,
): PendingSelection | null {
  if (!snapshot?.selectionText.trim()) {
    return null;
  }
  const file = app.vault.getAbstractFileByPath(snapshot.filePath);
  if (!(file instanceof TFile)) {
    return null;
  }
  return {
    text: snapshot.selectionText,
    chars: snapshot.selectionText.length,
    file_path: file.path,
    file_name: file.name,
    pdf_page: snapshot.pdfPage,
    line: snapshot.cursor ? snapshot.cursor.line + 1 : null,
    column: snapshot.cursor ? snapshot.cursor.ch + 1 : null,
    updated_at: new Date(snapshot.updatedAt).toISOString(),
  };
}

export function findContextMarkdownView(
  app: App,
  snapshot: EditorContextSnapshot | null = null,
): MarkdownView | null {
  const file = resolveContextFile(app, snapshot);
  return file ? findMarkdownViewForFile(app, file) : null;
}

export function describeContextLocation(
  app: App,
  snapshot: EditorContextSnapshot | null = null,
): ContextLocationDisplay {
  const file = resolveContextFile(app, snapshot);
  if (!(file instanceof TFile)) {
    return { label: null, title: "", hasFile: false, isPdf: false };
  }

  const extension = file.extension.toLowerCase();
  const isPdf = extension === "pdf";
  if (isPdf) {
    const page = readCurrentPdfPage(app, file, snapshot);
    return {
      label: page ? `${file.name} · p.${page}` : file.name,
      title: page ? `${file.path}#page=${page}` : file.path,
      hasFile: true,
      isPdf: true,
    };
  }

  const markdownView = findMarkdownViewForFile(app, file);
  const cursor = readEditorCursor(markdownView?.editor) ??
    (snapshot?.filePath === file.path ? snapshot.cursor : null);
  return {
    label: cursor ? `${file.name} · L${cursor.line + 1}:C${cursor.ch + 1}` : file.name,
    title: cursor ? `${file.path}:${cursor.line + 1}:${cursor.ch + 1}` : file.path,
    hasFile: true,
    isPdf: false,
  };
}

function filePayload(file: TFile): ActiveFilePayload {
  return {
    path: file.path,
    basename: file.basename,
    extension: file.extension,
    size: file.stat.size,
    ctime: file.stat.ctime,
    mtime: file.stat.mtime,
  };
}

function truncateText(text: string, maxChars: number): { text: string; truncated: boolean } {
  if (text.length <= maxChars) {
    return { text, truncated: false };
  }
  return { text: text.slice(0, maxChars), truncated: true };
}

function resolveContextFile(
  app: App,
  snapshot: EditorContextSnapshot | null,
): TFile | null {
  const activeFile = app.workspace.getActiveFile();
  if (activeFile instanceof TFile) {
    return activeFile;
  }

  const markdownView = app.workspace.getActiveViewOfType(MarkdownView);
  if (markdownView?.file instanceof TFile) {
    return markdownView.file;
  }

  const activeLeafFile = readViewFile(app.workspace.activeLeaf?.view as unknown);
  if (activeLeafFile instanceof TFile) {
    return activeLeafFile;
  }

  if (snapshot?.filePath) {
    const file = app.vault.getAbstractFileByPath(snapshot.filePath);
    if (file instanceof TFile) {
      return file;
    }
  }
  return null;
}

function findMarkdownViewForFile(app: App, file: TFile): MarkdownView | null {
  const activeView = app.workspace.getActiveViewOfType(MarkdownView);
  if (activeView?.file?.path === file.path) {
    return activeView;
  }

  let match: MarkdownView | null = null;
  app.workspace.iterateAllLeaves((leaf) => {
    if (match) {
      return;
    }
    const view = leaf.view;
    if (view instanceof MarkdownView && view.file?.path === file.path) {
      match = view;
    }
  });
  return match;
}

function snapshotFromEditor(
  editor: Editor,
  file: TFile,
  previous: EditorContextSnapshot | null = null,
): EditorContextSnapshot {
  // editor.getSelection() is empty in Reading view (and some Live Preview states)
  // even when text is visibly selected; fall back to the DOM selection. Safe here
  // because snapshotFromEditor only runs when the markdown view is the active leaf,
  // so window.getSelection() is that editor's selection, not the sidebar's.
  const liveSelection = readEditorSelection(editor) || readDomSelection();
  return {
    filePath: file.path,
    selectionText: liveSelection || (previous?.filePath === file.path ? previous.selectionText : ""),
    cursor: readEditorCursor(editor),
    pdfPage: null,
    updatedAt: Date.now(),
  };
}

function readSelectionText(
  editor: Editor | undefined,
  file: TFile | null,
  snapshot: EditorContextSnapshot | null,
  pendingSelection: PendingSelection | null | undefined,
): string {
  const liveSelection = readEditorSelection(editor);
  if (liveSelection) {
    return liveSelection;
  }
  if (file instanceof TFile && pendingSelection?.file_path === file.path) {
    return pendingSelection.text;
  }
  if (file instanceof TFile && snapshot?.filePath === file.path) {
    return snapshot.selectionText;
  }
  return "";
}

function readDomSelection(): string {
  try {
    return window.getSelection()?.toString().trim() ?? "";
  } catch {
    return "";
  }
}

function readEditorSelection(editor: Editor | undefined): string {
  if (!editor) {
    return "";
  }
  try {
    return editor.getSelection();
  } catch {
    return "";
  }
}

function readEditorCursor(editor: Editor | undefined): EditorPosition | null {
  if (!editor) {
    return null;
  }
  try {
    return editor.getCursor();
  } catch {
    return null;
  }
}

function readCurrentPdfPage(
  app: App,
  file: TFile | null,
  snapshot: EditorContextSnapshot | null,
): number | null {
  if (!(file instanceof TFile)) {
    return null;
  }
  const activeView = app.workspace.activeLeaf?.view as unknown;
  const activeFile = readViewFile(activeView);
  if (activeFile?.path === file.path) {
    const page = readViewerPage(activeView);
    if (page !== null) {
      return page;
    }
  }

  let page: number | null = null;
  app.workspace.iterateAllLeaves((leaf) => {
    if (page !== null) {
      return;
    }
    const view = leaf.view as unknown;
    const viewFile = readViewFile(view);
    if (viewFile?.path === file.path) {
      page = readViewerPage(view);
    }
  });

  if (page !== null) {
    return page;
  }
  return snapshot?.filePath === file.path ? snapshot.pdfPage : null;
}
