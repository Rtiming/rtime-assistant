// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
// Live e2e for the plugin's own client stack (node transport + SSE parsing)
// against the real gateway — no Obsidian required. Used by agents to verify
// the plugin network path: `npm run e2e:gateway`.
import esbuild from "esbuild";
import { mkdtemp, rm, writeFile } from "node:fs/promises";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
// Set RTIME_GATEWAY_URL to test a remote/private gateway.
const endpoint = (process.env.RTIME_GATEWAY_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");

const tmp = await mkdtemp(path.join(os.tmpdir(), "rtime-e2e-"));
try {
  const stub = path.join(tmp, "obsidian-stub.js");
  await writeFile(
    stub,
    "module.exports = { requestUrl: async () => { throw new Error('requestUrl stub: node transport should have been used'); } };\n",
  );
  const outfile = path.join(tmp, "client.cjs");
  await esbuild.build({
    entryPoints: [path.join(root, "src/services/assistant-client.ts")],
    bundle: true,
    format: "cjs",
    platform: "node",
    outfile,
    alias: { obsidian: stub },
    logLevel: "silent",
  });

  const requireCjs = createRequire(import.meta.url);
  const { createAssistantClient } = requireCjs(outfile);
  const client = createAssistantClient();

  const settings = {
    uiLanguage: "zh-CN",
    chatEndpoint: `${endpoint}/api/obsidian/chat`,
    healthEndpoint: `${endpoint}/healthz`,
    contextMode: "current-note",
    defaultTaskMode: "ask",
    targetModule: "auto",
    targetFolder: "",
    includeSelection: true,
    includeActiveNoteBody: true,
    maxNoteChars: 12000,
    requestTimeoutMs: 120000,
    requestRetryCount: 0,
    requestRetryDelayMs: 0,
    healthCacheMs: 0,
    streamEnabled: true,
    prepareEnabled: true,
    modelPrewarmEnabled: true,
    prepareDebounceMs: 700,
    modelProviderId: "",
    modelId: "",
    modelProtocol: "",
    modelCatalog: null,
    permissionMode: "dontAsk",
    approvalForwardingEnabled: true,
    lastConversationId: "",
  };
  const context = {
    vault: { name: "e2e" },
    active_file: null,
    selection: null,
    note: null,
    metadata: { headings: [], tags: [], links: [] },
    requested_mode: "current-note",
    local_time: new Date().toISOString(),
  };

  const health = await client.checkBackendHealth(settings);
  console.log(`✓ health: ${health}`);

  const prepare = await client.prepareContext(settings, context);
  console.log(
    `✓ prepare: ${prepare.prepare_id}, ${prepare.dur_ms ?? "?"}ms, ` +
      `${prepare.unlock_count ?? 0} unlocks`,
  );

  let deltas = 0;
  let statuses = 0;
  let firstDeltaMs = null;
  const t0 = Date.now();
  const result = await client.postAssistantStreamRequest(
    settings,
    context,
    "这是命令行e2e测试。请直接回复四个字：链路畅通。不要调用任何工具。",
    "ask",
    {
      onStatus: () => {
        statuses += 1;
      },
      onDelta: () => {
        if (firstDeltaMs === null) {
          firstDeltaMs = Date.now() - t0;
        }
        deltas += 1;
      },
    },
  );
  console.log(
    `✓ stream: ${Date.now() - t0}ms total, first delta ${firstDeltaMs}ms, ` +
      `${deltas} deltas, ${statuses} status events`,
  );
  console.log(`✓ answer: ${result.answer.slice(0, 80)} (sources: ${result.sources.length})`);
  if (!result.answer.trim()) {
    process.exitCode = 1;
  }
} finally {
  await rm(tmp, { recursive: true, force: true });
}
