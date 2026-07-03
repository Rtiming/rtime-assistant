// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
// Obsidian PDF/view introspection. Obsidian's PDF view is NOT public API; the
// object paths here mirror what the PDF++ plugin documents and what Obsidian's own
// toolbar reads. Every reach into a private shape is defensive (optional chaining,
// type guards) and DOM access is feature-detected so the module also runs under the
// Node test harness, which has no `window`/`HTMLElement`.

import { TFile } from "obsidian";

export function readViewFile(view: unknown): TFile | null {
  if (!isRecord(view) || !(view.file instanceof TFile)) {
    return null;
  }
  return view.file;
}

/**
 * The page Obsidian's own toolbar tracks: the pdf.js `PDFViewer`'s
 * `currentPageNumber`. The object path has TWO `pdfViewer` hops — the first is
 * Obsidian's `ObsidianViewer` wrapper, the second is the real pdf.js viewer:
 *   PDFView.viewer(PDFViewerComponent).child.pdfViewer(ObsidianViewer)
 *          .pdfViewer(pdf.js PDFViewer).currentPageNumber
 * Stopping at the wrapper misses the live value and falls back to the persisted
 * `getState().page`, which only settles after a page has fully scrolled past.
 * Order: live value → on-screen DOM → lagging state.
 */
export function readViewerPage(view: unknown): number | null {
  const livePaths = [
    ["viewer", "child", "pdfViewer", "pdfViewer", "currentPageNumber"],
    ["viewer", "pdfViewer", "pdfViewer", "currentPageNumber"],
    ["viewer", "child", "pdfViewer", "currentPageNumber"],
    ["viewer", "pdfViewer", "currentPageNumber"],
    ["pdfViewer", "currentPageNumber"],
  ];
  for (const path of livePaths) {
    const normalized = normalizePage(readNestedNumber(view, path));
    if (normalized !== null) {
      return normalized;
    }
  }

  const domPage = readPageFromDom(view);
  if (domPage !== null) {
    return domPage;
  }

  return normalizePage(readStatePage(view));
}

/** Page the current DOM text selection sits on, read from the pdf.js page div
 * (`.page[data-page-number]`) that contains the selection's anchor. Null when the
 * selection is empty or not inside a PDF page (e.g. text picked in the sidebar). */
export function readPdfPageFromSelection(): number | null {
  if (typeof window === "undefined") {
    return null;
  }
  try {
    const selection = window.getSelection();
    if (!selection || selection.isCollapsed || !selection.toString().trim()) {
      return null;
    }
    const node = selection.anchorNode ?? selection.focusNode;
    const element = node instanceof Element ? node : node?.parentElement ?? null;
    const pageEl = element?.closest("[data-page-number]");
    if (!(pageEl instanceof HTMLElement)) {
      return null;
    }
    return normalizePage(pageEl.dataset.pageNumber);
  } catch {
    return null;
  }
}

/** Most-visible rendered PDF page within the view's scroll container — a
 * version-independent fallback for when the private object path changes. */
function readPageFromDom(view: unknown): number | null {
  if (typeof HTMLElement === "undefined" || typeof getComputedStyle === "undefined") {
    return null;
  }
  if (!isRecord(view) || !(view.containerEl instanceof HTMLElement)) {
    return null;
  }
  const pages = Array.from(
    view.containerEl.querySelectorAll<HTMLElement>(".page[data-page-number]"),
  );
  if (!pages.length) {
    return null;
  }
  // Prefer the known pdf.js scroll container; fall back to the nearest scrollable
  // ancestor so a renamed container in a future Obsidian release still works.
  const viewportEl =
    pages[0].closest<HTMLElement>(".pdf-viewer-container") ?? findScrollParent(pages[0]);
  const viewport = viewportEl.getBoundingClientRect();
  let best: { page: number; visible: number } | null = null;
  for (const el of pages) {
    const page = normalizePage(el.dataset.pageNumber);
    if (page === null) {
      continue;
    }
    const rect = el.getBoundingClientRect();
    const visible = Math.min(rect.bottom, viewport.bottom) - Math.max(rect.top, viewport.top);
    if (visible <= 0) {
      continue;
    }
    if (!best || visible > best.visible) {
      best = { page, visible };
    }
  }
  return best?.page ?? null;
}

/** Nearest vertically-scrollable ancestor (the PDF viewport), falling back to the
 * element itself when none is found. */
function findScrollParent(element: HTMLElement): HTMLElement {
  let current: HTMLElement | null = element.parentElement;
  while (current) {
    const overflowY = getComputedStyle(current).overflowY;
    if ((overflowY === "auto" || overflowY === "scroll") && current.scrollHeight > current.clientHeight) {
      return current;
    }
    current = current.parentElement;
  }
  return element;
}

function readStatePage(view: unknown): unknown {
  if (!isRecord(view) || typeof view.getState !== "function") {
    return undefined;
  }
  try {
    const state = view.getState() as unknown;
    if (!isRecord(state)) {
      return undefined;
    }
    if ("page" in state) {
      return state.page;
    }
    if (isRecord(state.state) && "page" in state.state) {
      return state.state.page;
    }
  } catch {
    return undefined;
  }
  return undefined;
}

function readNestedNumber(value: unknown, path: string[]): unknown {
  let current = value;
  for (const key of path) {
    if (!isRecord(current)) {
      return undefined;
    }
    current = current[key];
  }
  return current;
}

function normalizePage(value: unknown): number | null {
  const page = typeof value === "string" ? Number.parseInt(value, 10) : value;
  return typeof page === "number" && Number.isFinite(page) && page > 0 ? Math.floor(page) : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}
