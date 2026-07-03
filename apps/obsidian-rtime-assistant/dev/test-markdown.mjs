// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Node tests for src/ui/markdown.ts (pure, Obsidian-free). Bundles the TS with
 * esbuild so the edge cases that drive the rendering fixes are pinned down.
 */
import assert from "node:assert/strict";
import { mkdtemp, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import esbuild from "esbuild";

const root = path.resolve(import.meta.dirname, "..");

function log(message) {
  console.log(`✓ ${message}`);
}

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-markdown-"));
try {
  const outfile = path.join(tmpDir, "markdown.mjs");
  await esbuild.build({
    entryPoints: [path.join(root, "src/ui/markdown.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile,
    logLevel: "silent",
  });
  const { normalizeMathDelimiters, stripTrailingSourcesBlock } = await import(pathToFileURL(outfile).href);

  // --- normalizeMathDelimiters ---
  assert.equal(normalizeMathDelimiters("第 \\(i\\) 个子系 \\(\\hat h_i\\)。"), "第 $i$ 个子系 $\\hat h_i$。");
  log("inline \\(…\\) becomes $…$");

  assert.equal(
    normalizeMathDelimiters("推导：\n\\[\\hat H=\\sum_{i=1}^{N} h_i\\]\n完成。"),
    "推导：\n$$\\hat H=\\sum_{i=1}^{N} h_i$$\n完成。",
  );
  log("display \\[…\\] becomes $$…$$ and keeps subscripts");

  const codeInput = "见 `\\(x\\)`，块：\n```\n\\[y\\]\n```\n但 \\(z\\) 要转。";
  const codeOut = normalizeMathDelimiters(codeInput);
  assert.ok(codeOut.includes("`\\(x\\)`"), "inline code left verbatim");
  assert.ok(codeOut.includes("\\[y\\]"), "fenced code left verbatim");
  assert.ok(codeOut.includes("但 $z$ 要转。"), "math outside code still converts");
  log("code spans and fences are not touched");

  assert.equal(normalizeMathDelimiters("已正确 $E=mc^2$ 和 $$F=ma$$。"), "已正确 $E=mc^2$ 和 $$F=ma$$。");
  assert.equal(normalizeMathDelimiters("纯文本，无公式。"), "纯文本，无公式。");
  log("existing $-math and plain text pass through unchanged");

  // --- stripTrailingSourcesBlock ---
  const withSources = "结论是等概率原理。\n\n来源：\n- knowledge/a.pdf#page=1\n- knowledge/b.pdf#page=24";
  assert.equal(stripTrailingSourcesBlock(withSources), "结论是等概率原理。");
  log("trailing 来源 block with citations is stripped");

  assert.equal(
    stripTrailingSourcesBlock("Answer.\n\nSources:\n- knowledge/a.pdf#page=3\n- https://example.com/x"),
    "Answer.",
  );
  log("English Sources block with a URL is stripped");

  // A real bug guard: a plain bullet list under a 来源-looking heading, with NO
  // citation target, must be preserved (it is content, not a source block).
  const prose = "参考来源：\n- 牛顿第二定律见上文\n- 注意单位换算";
  assert.equal(stripTrailingSourcesBlock(prose), prose);
  const proseExactHeader = "来源：\n- 这是正文要点一\n- 这是正文要点二";
  assert.equal(stripTrailingSourcesBlock(proseExactHeader), proseExactHeader);
  log("a bullet list with no citation target is NOT stripped");

  const midText = "来源：\n- a.pdf#page=1\n\n后面还有正文，不该删。";
  assert.equal(stripTrailingSourcesBlock(midText), midText);
  assert.equal(stripTrailingSourcesBlock("没有来源块的普通回答。"), "没有来源块的普通回答。");
  log("non-trailing blocks and plain answers are left intact");
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
