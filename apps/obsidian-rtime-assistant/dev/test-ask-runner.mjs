// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import assert from "node:assert/strict";
import { mkdtemp, writeFile, rm } from "node:fs/promises";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import esbuild from "esbuild";

// Unit tests for the DOM-free ask runner: busy-retry / cancel / stream-fallback
// orchestration extracted from view.ts so it's testable without an ItemView.

const root = path.resolve(import.meta.dirname, "..");

function log(message) {
  console.log(`✓ ${message}`);
}

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-runner-"));
try {
  const stub = path.join(tmpDir, "obsidian-stub.mjs");
  await writeFile(stub, "export async function requestUrl() { throw new Error('requestUrl stub'); }\n");
  const outfile = path.join(tmpDir, "ask-runner.mjs");
  await esbuild.build({
    entryPoints: [path.join(root, "src/services/ask-runner.ts")],
    bundle: true,
    format: "esm",
    platform: "browser",
    alias: { obsidian: stub },
    outfile,
    logLevel: "silent",
  });
  const { runAsk, isCancelledError } = await import(pathToFileURL(outfile).href);

  const ok = (answer) => ({ answer, sources: [] });

  function makeHost(overrides = {}) {
    const calls = { reset: 0, sleeps: [], statuses: [], activities: [], deltas: [], askCalls: 0, streamCalls: 0 };
    const host = {
      streamEnabled: true,
      lang: "en",
      taskMode: "ask",
      isStopRequested: () => false,
      ask: async () => {
        calls.askCalls += 1;
        return ok("ASK");
      },
      stream: async () => {
        calls.streamCalls += 1;
        return ok("STREAM");
      },
      setStatus: (t) => calls.statuses.push(t),
      setActivity: (t) => calls.activities.push(t),
      appendDelta: (t) => calls.deltas.push(t),
      setTrace: () => {},
      resetForRetry: () => {
        calls.reset += 1;
      },
      sleep: async (ms) => {
        calls.sleeps.push(ms);
      },
      ...overrides,
    };
    return { host, calls };
  }

  {
    const { host, calls } = makeHost();
    const r = await runAsk("p", {}, host);
    assert.equal(r.answer, "STREAM");
    assert.equal(calls.askCalls, 0);
    log("streaming success returns the streamed result without falling back");
  }

  {
    const { host, calls } = makeHost({ streamEnabled: false });
    const r = await runAsk("p", {}, host);
    assert.equal(r.answer, "ASK");
    assert.equal(calls.streamCalls, 0);
    log("non-streaming mode calls ask() directly");
  }

  {
    const { host, calls } = makeHost({ isStopRequested: () => true });
    await assert.rejects(() => runAsk("p", {}, host), /Request cancelled/);
    assert.equal(calls.streamCalls, 0);
    assert.equal(calls.askCalls, 0);
    log("a pending Stop short-circuits before any request");
  }

  {
    let n = 0;
    const { host, calls } = makeHost({
      stream: async () => {
        n += 1;
        if (n === 1) throw new Error("Assistant endpoint returned HTTP 503: busy");
        return ok("OK-AFTER-RETRY");
      },
    });
    const r = await runAsk("p", {}, host);
    assert.equal(r.answer, "OK-AFTER-RETRY");
    assert.equal(calls.reset, 1);
    assert.deepEqual(calls.sleeps, [2000]);
    log("HTTP 503 busy retries with backoff then succeeds");
  }

  {
    const { host, calls } = makeHost({ stream: async () => { throw new Error("HTTP 503"); } });
    await assert.rejects(() => runAsk("p", {}, host), /HTTP 503/);
    assert.equal(calls.sleeps.length, 5);
    log("HTTP 503 gives up after exhausting the backoff schedule");
  }

  {
    const { host, calls } = makeHost({ stream: async () => { throw new Error("Assistant stream error: bad frame"); } });
    await assert.rejects(() => runAsk("p", {}, host), /Assistant stream error/);
    assert.equal(calls.askCalls, 0);
    log("a stream-server error propagates and does NOT fall back to non-streaming");
  }

  {
    const { host, calls } = makeHost({ stream: async () => { throw new Error("Request cancelled"); } });
    await assert.rejects(() => runAsk("p", {}, host), /Request cancelled/);
    assert.equal(calls.askCalls, 0);
    log("a cancellation propagates and does NOT re-fire a fresh request");
  }

  {
    const { host, calls } = makeHost({ stream: async () => { throw new Error("socket boom"); } });
    const r = await runAsk("p", {}, host);
    assert.equal(r.answer, "ASK");
    assert.equal(calls.askCalls, 1);
    assert.equal(calls.reset, 1);
    log("a generic stream failure falls back to a non-streaming request");
  }

  {
    assert.equal(isCancelledError(new Error("Request cancelled")), true);
    const abort = new Error("aborted");
    abort.name = "AbortError";
    assert.equal(isCancelledError(abort), true);
    assert.equal(isCancelledError(new Error("HTTP 500")), false);
    assert.equal(isCancelledError("nope"), false);
    log("isCancelledError classifies cancel/abort but not generic errors");
  }
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}
