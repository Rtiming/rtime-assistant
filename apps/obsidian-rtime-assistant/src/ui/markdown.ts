// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
// Pure markdown transforms applied to assistant answers before Obsidian renders
// them. No Obsidian imports — kept dependency-free so dev/test-markdown.mjs can
// exercise the edge cases directly.

/**
 * Obsidian's MathJax only typesets `$…$` / `$$…$$`. Models (notably the Kimi
 * wrapper) keep emitting OpenAI-style `\(…\)` / `\[…\]`, which Obsidian renders as
 * raw text — and markdown then eats the `_` subscripts and `\` commands. Rewrite
 * those delimiters to the `$` forms so the math actually renders. Fenced and inline
 * code is left verbatim so a literal `\(` inside code survives.
 */
export function normalizeMathDelimiters(content: string): string {
  if (!content.includes("\\(") && !content.includes("\\[")) {
    return content;
  }
  // Split keeps the code delimiters (capturing group) at odd indices; only the
  // even, non-code segments get the math rewrite.
  const segments = content.split(/(```[\s\S]*?```|~~~[\s\S]*?~~~|`[^`\n]*`)/g);
  for (let i = 0; i < segments.length; i += 2) {
    segments[i] = segments[i]
      .replace(/\\\[([\s\S]*?)\\\]/g, (_m, body: string) => `$$${body}$$`)
      .replace(/\\\(([\s\S]*?)\\\)/g, (_m, body: string) => `$${body}$`);
  }
  return segments.join("");
}

/** A standalone "来源：" / "Sources:" header line that opens the trailing block. */
const SOURCE_BLOCK_HEADER = /^\s*(?:来源|sources?)\s*[:：]?\s*$/i;

/** Unmistakably a citation target — a page anchor, a URL, or a known file type.
 * Used both to recognise source-list lines and to gate the strip (so a plain
 * bullet list under a "来源"-looking heading is not mistaken for a source block). */
function looksLikeCitation(text: string): boolean {
  return (
    /#page=\d+/.test(text) ||
    /https?:\/\//i.test(text) ||
    /\.(?:pdf|md|markdown|png|jpe?g|gif|webp|csv|tsv|json|docx?|pptx?|xlsx?|txt)\b/i.test(text)
  );
}

/** A line that belongs to a trailing source list: a bullet, a citation target, or
 * a backtick-wrapped path. */
function isSourceListLine(trimmed: string): boolean {
  return (
    /^[-*•]/.test(trimmed) ||
    looksLikeCitation(trimmed) ||
    (/^`.*`$/.test(trimmed) && /\.[a-z0-9]+/i.test(trimmed))
  );
}

/**
 * Remove the redundant trailing "来源：" markdown block from displayed content.
 * The backend appends this block AND parses it into structured sources, which the
 * UI shows as a collapsed toggle — rendering both duplicates it and wastes space.
 * Only strips a genuine trailing label + source-list run that contains at least one
 * real citation, so prose (even a plain bullet list under a "来源" heading) is never
 * touched; `message.content` (used by copy/insert) is left intact upstream.
 */
export function stripTrailingSourcesBlock(content: string): string {
  const lines = content.split("\n");
  let i = lines.length - 1;
  while (i >= 0 && lines[i].trim() === "") i -= 1;
  const lastNonBlank = i;
  let sawCitation = false;
  while (i >= 0) {
    const trimmed = lines[i].trim();
    if (trimmed === "") {
      i -= 1;
      continue;
    }
    if (isSourceListLine(trimmed)) {
      if (looksLikeCitation(trimmed)) {
        sawCitation = true;
      }
      i -= 1;
      continue;
    }
    break;
  }
  if (i < 0 || i >= lastNonBlank || !sawCitation || !SOURCE_BLOCK_HEADER.test(lines[i])) {
    return content;
  }
  let cut = i;
  while (cut > 0 && lines[cut - 1].trim() === "") cut -= 1;
  return lines.slice(0, cut).join("\n").replace(/\s+$/, "");
}
