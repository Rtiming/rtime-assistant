// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import esbuild from "esbuild";

// Unit tests for the streaming SSE parser (consumeSseFrames) — the pure code path
// the gateway's early-status + delta frames feed. Bundles assistant-client.ts with
// an obsidian stub (mirrors simulate-plugin.mjs) so the parser runs without Obsidian.

const root = path.resolve(import.meta.dirname, "..");

function log(message) {
  console.log(`✓ ${message}`);
}

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-sse-"));
try {
  const stub = path.join(tmpDir, "obsidian-stub.mjs");
  await writeFile(stub, "export async function requestUrl() { throw new Error('requestUrl stub'); }\n");
  const outfile = path.join(tmpDir, "assistant-client.mjs");
  await esbuild.build({
    entryPoints: [path.join(root, "src/services/assistant-client.ts")],
    bundle: true,
    format: "esm",
    platform: "browser",
    alias: { obsidian: stub },
    outfile,
    logLevel: "silent",
  });
  const { consumeSseFrames } = await import(pathToFileURL(outfile).href);

  // Drive a sequence of network chunks through the parser exactly as the transport
  // does (append, consume, keep the remainder; flush with a trailing blank line).
  function drive(chunks) {
    const events = [];
    const state = { answerParts: [], finalResult: null, trace: {} };
    const handlers = {
      onStatus: (t) => events.push(["status", t]),
      onApprovalRequest: (t) => events.push(["approval", t]),
      onDelta: (t) => events.push(["delta", t]),
      onTrace: () => {},
      onDone: () => {},
    };
    let buffer = "";
    for (const chunk of chunks) {
      buffer += chunk;
      buffer = consumeSseFrames(buffer, handlers, state);
    }
    consumeSseFrames(`${buffer}\n\n`, handlers, state); // end-of-stream flush
    return { events, state };
  }

  const frame = (obj) => `data: ${JSON.stringify(obj)}\n\n`;

  {
    const { events, state } = drive([
      frame({ type: "status", text: "thinking" }),
      frame({ type: "delta", text: "Hel" }),
      frame({ type: "delta", text: "lo" }),
      frame({ type: "done", answer: "Hello", sources: [] }),
    ]);
    assert.deepEqual(events, [["status", "thinking"], ["delta", "Hel"], ["delta", "lo"]]);
    assert.equal(state.answerParts.join(""), "Hello");
    assert.equal(state.finalResult?.answer, "Hello");
    log("SSE parser dispatches status/delta in order and captures the done payload");
  }

  {
    const whole = frame({ type: "delta", text: "split-me" });
    const mid = Math.floor(whole.length / 2);
    const { events } = drive([whole.slice(0, mid), whole.slice(mid)]);
    assert.deepEqual(events, [["delta", "split-me"]]);
    log("SSE parser reassembles a frame split across chunk boundaries (no dup, no drop)");
  }

  {
    const crlf = `data: ${JSON.stringify({ type: "delta", text: "crlf" })}\r\n\r\n`;
    const { events } = drive([crlf]);
    assert.deepEqual(events, [["delta", "crlf"]]);
    log("SSE parser normalizes CRLF frame separators");
  }

  {
    const { events } = drive([frame({ type: "delta", text: "x" }), "data: [DONE]\n\n"]);
    assert.deepEqual(events, [["delta", "x"]]);
    log("SSE parser ignores the [DONE] sentinel rather than treating it as content");
  }

  {
    assert.throws(
      () => drive([frame({ type: "error", message: "boom" })]),
      /Assistant stream error: boom/,
    );
    log("SSE parser raises a classified error on an error frame");
  }
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
