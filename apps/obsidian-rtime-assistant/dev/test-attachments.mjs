// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
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

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-attachments-"));
try {
  const outfile = path.join(tmpDir, "attachments.mjs");
  await esbuild.build({
    entryPoints: [path.join(root, "src/attachments.ts")],
    bundle: true,
    format: "esm",
    platform: "browser",
    outfile,
    logLevel: "silent",
  });
  const {
    MAX_BINARY_CONTENT_BYTES,
    attachmentFromFile,
    attachmentImageUrl,
    attachmentLabel,
    classifyAttachment,
    displayAttachmentSnapshot,
  } = await import(pathToFileURL(outfile).href);

  assert.equal(classifyAttachment("slide.pdf", "application/pdf"), "pdf");
  assert.equal(classifyAttachment("note.md", "text/markdown"), "markdown");
  assert.equal(classifyAttachment("table.csv", "text/csv"), "csv");
  assert.equal(classifyAttachment("deck.pptx", ""), "office");
  assert.equal(classifyAttachment("scan.jpg", "image/jpeg"), "image");
  assert.equal(classifyAttachment("course-pack.zip", "application/zip"), "archive");
  log("attachment classifier covers PDF, Markdown, CSV, Office, archives, and images");

  const textFile = new File(["hello ".repeat(3000)], "note.md", { type: "text/markdown" });
  const attachment = await attachmentFromFile(textFile, "picker");
  assert.equal(attachment.kind, "markdown");
  assert.equal(attachment.intake_mode, "session");
  assert.equal(attachment.temporary, true);
  assert.equal(attachment.extracted_text.length, 12000);
  assert.ok(attachment.extracted_chars > attachment.extracted_text.length);
  assert.match(attachmentLabel(attachment), /下条消息发送/);
  log("text attachments extract bounded text and default to next-message context");

  const pdf = await attachmentFromFile(new File([Uint8Array.from([0x25, 0x50, 0x44, 0x46])], "slides.pdf", { type: "application/pdf" }), "picker");
  assert.equal(pdf.kind, "pdf");
  assert.equal(pdf.status, "ready");
  assert.equal(pdf.content_encoding, "base64");
  assert.equal(pdf.content_media_type, "application/pdf");
  assert.ok(pdf.content_base64.length > 0);
  const pdfDisplay = displayAttachmentSnapshot(pdf);
  assert.equal(pdfDisplay.content_base64, undefined);
  log("binary document attachments carry bounded bytes for next-message file extraction");

  const largePdf = await attachmentFromFile(
    {
      name: "large.pdf",
      type: "application/pdf",
      size: MAX_BINARY_CONTENT_BYTES + 1,
      arrayBuffer: async () => new ArrayBuffer(0),
    },
    "picker",
  );
  assert.equal(largePdf.status, "error");
  assert.match(largePdf.error, /inline attachment limit/);
  log("oversized binary attachments are rejected before request serialization");

  const zip = await attachmentFromFile(new File([Uint8Array.from([0x50, 0x4b, 0x03, 0x04])], "archive.zip", { type: "application/zip" }), "drop");
  assert.equal(zip.kind, "archive");
  assert.equal(zip.status, "ready");
  assert.equal(zip.content_encoding, "base64");
  assert.equal(zip.content_media_type, "application/zip");
  assert.ok(zip.content_base64.length > 0);
  log("zip archives carry bounded bytes for tool-model archive inspection");

  const unsupported = await attachmentFromFile(new File(["x"], "archive.rar", { type: "application/vnd.rar" }), "drop");
  assert.equal(unsupported.status, "error");
  log("unsupported attachments are kept as visible error chips, not silently sent");

  const tinyPngBytes = Uint8Array.from([
    0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a,
    0x00, 0x00, 0x00, 0x0d,
  ]);
  const image = await attachmentFromFile(new File([tinyPngBytes], "screen.png", { type: "image/png" }), "paste");
  assert.equal(image.kind, "image");
  assert.equal(image.status, "ready");
  assert.equal(image.content_encoding, "base64");
  assert.equal(image.content_media_type, "image/png");
  assert.ok(image.content_base64.length > 0);
  assert.match(attachmentImageUrl(image), /^data:image\/png;base64,/);
  const display = displayAttachmentSnapshot(image);
  assert.equal(display.content_base64, undefined);
  assert.match(display.preview_data_url, /^data:image\/png;base64,/);
  log("image attachments carry model-visible base64 and display snapshots keep a preview");
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
