// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { pathToFileURL } from "node:url";
import esbuild from "esbuild";

const root = path.resolve(import.meta.dirname, "..");
const args = new Set(process.argv.slice(2));

function log(message) {
  console.log(`✓ ${message}`);
}

async function checkBundleArtifacts() {
  const manifest = JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8"));
  assert.equal(manifest.id, "rtime-assistant");
  assert.equal(manifest.isDesktopOnly, true);

  const main = await readFile(path.join(root, "main.js"), "utf8");
  const styles = await readFile(path.join(root, "styles.css"), "utf8");
  assert.match(main, /MarkdownRenderer/);
  assert.match(main, /submitBehavior/);
  assert.match(main, /requestRetryCount/);
  assert.match(main, /healthCacheMs/);
  assert.match(main, /prepareContext|prepareEndpointFromChat|api\/obsidian\/prepare/);
  assert.match(main, /updateActivityCard|activity\.kicker/);
  assert.match(main, /message\.maxTurnsError/);
  assert.match(main, /attachments\.nextTurn/);
  assert.match(main, /permissionMode|permission_mode/);
  assert.match(main, /approvalForwardingEnabled|approval_forwarding|approval_request/);
  assert.match(main, /modelPrewarmEnabled|prewarm_model/);
  assert.match(main, /pluginUpdateUrl|release\.json|downloadPluginRelease/);
  assert.match(main, /rtime-assistant-settings-update-card/);
  assert.match(main, /settings\.update\.reload\.button/);
  assert.doesNotMatch(main, /attachments\.toggleIntake|api\/obsidian\/intake/);
  assert.match(main, /memory_events|memoryEvents/);
  assert.match(styles, /rtime-assistant-actions/);
  assert.match(styles, /rtime-assistant-message-content/);
  assert.match(styles, /rtime-assistant-activity-card/);
  assert.match(styles, /rtime-assistant-spin/);
  assert.match(styles, /rtime-assistant-attachment-chip/);
  assert.match(styles, /rtime-assistant-settings-update-card/);
  assert.match(styles, /rtime-assistant-settings-update-badge/);
  log("built plugin artifacts contain markdown, composer, network, and manifest markers");
}

async function checkKeyboardMatrix() {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-keyboard-"));
  const outfile = path.join(tmpDir, "keyboard.mjs");
  try {
    await esbuild.build({
      entryPoints: [path.join(root, "src/keyboard.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      outfile,
      logLevel: "silent",
    });
    const { shouldSubmitFromComposerEvent } = await import(pathToFileURL(outfile).href);
    const enterSend = { submitBehavior: "enter-send" };
    const modEnter = { submitBehavior: "mod-enter-send" };

    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter" }), true);
    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter", shiftKey: true }), false);
    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter", metaKey: true }), true);
    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter", ctrlKey: true }), true);
    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter", altKey: true }), false);
    assert.equal(shouldSubmitFromComposerEvent(enterSend, { key: "Enter", isComposing: true }), false);

    assert.equal(shouldSubmitFromComposerEvent(modEnter, { key: "Enter" }), false);
    assert.equal(shouldSubmitFromComposerEvent(modEnter, { key: "Enter", shiftKey: true }), false);
    assert.equal(shouldSubmitFromComposerEvent(modEnter, { key: "Enter", metaKey: true }), true);
    assert.equal(shouldSubmitFromComposerEvent(modEnter, { key: "Enter", ctrlKey: true }), true);
    assert.equal(shouldSubmitFromComposerEvent(modEnter, { key: "a" }), false);
    log("composer keyboard matrix matches configured behavior");
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function checkComposerContract() {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-composer-"));
  const outfile = path.join(tmpDir, "composer-contract.mjs");
  try {
    await esbuild.build({
      entryPoints: [path.join(root, "src/composer-contract.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      outfile,
      logLevel: "silent",
    });
    const {
      buildComposerRouteHint,
      folderSuggestionsFromPaths,
      getComposerTemplates,
      getPromptKeyForTemplate,
      getTargetModuleOptions,
    } = await import(pathToFileURL(outfile).href);

    assert.deepEqual(getComposerTemplates().map((item) => item.taskMode), [
      "ask",
      "summarize",
      "explain",
      "related",
      "citation-review",
    ]);
    assert.deepEqual(getTargetModuleOptions().map((item) => item.id), [
      "auto",
      "brain",
      "literature",
      "project",
      "runtime",
    ]);
    assert.equal(getPromptKeyForTemplate("related"), "prompt.related");
    assert.deepEqual(
      buildComposerRouteHint({ targetModule: "literature", targetFolder: " papers/zotero " }, "citation-review"),
      {
        template_id: "citation-review",
        target_module: "literature",
        target_folder: "papers/zotero",
      },
    );
    assert.deepEqual(folderSuggestionsFromPaths("papers/zotero/note.md", [
      "Inbox/a.md",
      "papers/zotero/note.md",
      "projects/rtime/index.md",
    ]), ["papers/zotero", "Inbox", "papers", "projects", "projects/rtime"]);
    log("composer contract exposes templates, target modules, route hints, and folder suggestions");
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function checkResponseParser() {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-response-"));
  const outfile = path.join(tmpDir, "response.mjs");
  try {
    await esbuild.build({
      entryPoints: [path.join(root, "src/services/response.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      outfile,
      logLevel: "silent",
    });
    const { parseAssistantResult, parseJsonOrText } = await import(pathToFileURL(outfile).href);

    assert.deepEqual(parseAssistantResult({
      choices: [{ message: { content: "hello" } }],
      citations: [{ file: "a.md", line: 2 }],
      memory_events: { referenced_count: 1, candidate_count: 2, auto_merged_count: 0, review_count: 2 },
      trace: { first_stdout_event: 1 },
    }), {
      answer: "hello",
      sources: [{ path: "a.md", line: 2 }],
      memoryEvents: { referenced_count: 1, candidate_count: 2, auto_merged_count: 0, review_count: 2, disabled: false, summary: undefined },
      trace: { gateway: { first_stdout_event: 1 } },
    });
    assert.equal(parseJsonOrText("{\"answer\":\"ok\"}").answer, "ok");
    assert.equal(parseJsonOrText("plain text"), "plain text");
    log("assistant response parser handles answer, choices, sources, and plain text");
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function checkAssistantClientHelpers() {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-client-"));
  const stub = path.join(tmpDir, "obsidian-stub.mjs");
  const outfile = path.join(tmpDir, "assistant-client.mjs");
  try {
    await writeFile(
      stub,
      "export async function requestUrl() { throw new Error('requestUrl stub'); }\n",
    );
    await esbuild.build({
      entryPoints: [path.join(root, "src/services/assistant-client.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      outfile,
      alias: { obsidian: stub },
      logLevel: "silent",
    });
    const { buildAssistantRequestBody, isStreamServerError, prepareEndpointFromChat } = await import(pathToFileURL(outfile).href);
    assert.equal(
      prepareEndpointFromChat("http://127.0.0.1:8765/api/obsidian/chat"),
      "http://127.0.0.1:8765/api/obsidian/prepare",
    );
    assert.equal(isStreamServerError(new Error("Assistant stream error: incomplete")), true);
    assert.equal(isStreamServerError(new Error("Assistant stream error: error_max_turns")), true);
    assert.equal(isStreamServerError(new Error("HTTP 503")), false);
    assert.equal(isStreamServerError(new Error("fetch failed")), false);
    const body = buildAssistantRequestBody(
      {
        uiLanguage: "zh-CN",
        chatEndpoint: "http://127.0.0.1:8765/api/obsidian/chat",
        healthEndpoint: "http://127.0.0.1:8765/healthz",
        contextMode: "current-note",
        defaultTaskMode: "ask",
        targetModule: "auto",
        targetFolder: "",
        includeSelection: true,
        includeActiveNoteBody: true,
        maxNoteChars: 12000,
        requestTimeoutMs: 180000,
        requestRetryCount: 0,
        requestRetryDelayMs: 0,
        healthCacheMs: 0,
        streamEnabled: true,
        prepareEnabled: true,
        modelPrewarmEnabled: true,
        prepareDebounceMs: 700,
        submitBehavior: "enter-send",
        clearComposerAfterSubmit: true,
        focusComposerAfterSubmit: true,
        autoScrollResponses: true,
        selftestWatchEnabled: true,
        modelProviderId: "",
        modelId: "",
        modelProtocol: "",
        modelCatalog: null,
        permissionMode: "default",
        approvalForwardingEnabled: true,
        pluginUpdateUrl: "",
        pluginLastInstalledVersion: "",
        pluginLastInstalledBuildId: "",
        pluginLastCheckedAt: "",
        pluginLastAvailableVersion: "",
        pluginLastAvailableBuildId: "",
        pluginLastAvailableGeneratedAt: "",
        pluginLastUpdateStatus: "",
        lastConversationId: "",
      },
      {
        vault: { name: "brain-notes" },
        active_file: null,
        selection: null,
        note: null,
        metadata: { headings: [], tags: [], links: [] },
        requested_mode: "current-note",
        local_time: "2026-06-15T00:00:00.000Z",
      },
      "hello",
      "ask",
      true,
    );
    assert.equal(body.options.permission_mode, "default");
    assert.equal(body.options.approval_forwarding, true);
    assert.equal(body.options.prewarm_model, true);
    log("assistant client derives endpoints, parses stream errors, and serializes prewarm/permission options");
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function checkPluginUpdaterHelpers() {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-updater-"));
  const stub = path.join(tmpDir, "obsidian-stub.mjs");
  const outfile = path.join(tmpDir, "plugin-updater.mjs");
  try {
    await writeFile(
      stub,
      "export async function requestUrl() { throw new Error('requestUrl stub'); }\n",
    );
    await esbuild.build({
      entryPoints: [path.join(root, "src/services/plugin-updater.ts")],
      bundle: true,
      format: "esm",
      platform: "node",
      outfile,
      alias: { obsidian: stub },
      logLevel: "silent",
    });
    const {
      compareVersions,
      parsePluginReleaseManifest,
      pluginReleaseStatus,
      releaseManifestUrl,
      resolveReleaseAssetUrl,
    } = await import(pathToFileURL(outfile).href);
    const sha = "a".repeat(64);
    const release = parsePluginReleaseManifest({
      schema_version: 1,
      id: "rtime-assistant",
      version: "0.6.1",
      build_id: "0.6.1+abc",
      files: {
        "manifest.json": { path: "manifest.json", sha256: sha, size: 10 },
        "main.js": { path: "main.js", sha256: sha, size: 20 },
        "styles.css": { path: "styles.css", sha256: sha, size: 30 },
      },
    });
    assert.equal(release.id, "rtime-assistant");
    assert.equal(release.files["main.js"].sha256, sha);
    assert.equal(
      releaseManifestUrl("https://example.test/releases/rtime-assistant/"),
      "https://example.test/releases/rtime-assistant/release.json",
    );
    assert.equal(
      releaseManifestUrl("https://example.test/releases/rtime-assistant/release.json"),
      "https://example.test/releases/rtime-assistant/release.json",
    );
    assert.equal(
      resolveReleaseAssetUrl("https://example.test/releases/rtime-assistant/release.json", "main.js"),
      "https://example.test/releases/rtime-assistant/main.js",
    );
    assert.equal(compareVersions("0.7.0", "0.6.9"), 1);
    assert.equal(compareVersions("0.6.0", "0.6.0"), 0);
    assert.equal(pluginReleaseStatus({ version: "0.6.1", build_id: "0.6.1+abc" }, "0.6.0"), "available-newer");
    assert.equal(
      pluginReleaseStatus({ version: "0.6.1", build_id: "0.6.1+def" }, "0.6.1", "0.6.1", "0.6.1+abc"),
      "available-build",
    );
    assert.equal(
      pluginReleaseStatus({ version: "0.6.1", build_id: "0.6.1+abc" }, "0.6.0", "0.6.1", "0.6.1+abc"),
      "installed",
    );
    assert.throws(() => parsePluginReleaseManifest({
      schema_version: 1,
      id: "rtime-assistant",
      version: "0.6.1",
      files: { "main.js": { path: "../main.js", sha256: sha } },
    }), /missing manifest\.json|unsafe release path/);
    log("plugin updater validates release manifests, asset URLs, and version ordering");
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function getFreePort() {
  return new Promise((resolve, reject) => {
    const server = net.createServer();
    server.listen(0, "127.0.0.1", () => {
      const address = server.address();
      server.close(() => {
        if (address && typeof address === "object") {
          resolve(address.port);
        } else {
          reject(new Error("could not allocate local port"));
        }
      });
    });
    server.on("error", reject);
  });
}

async function waitForHealth(url, timeoutMs = 8000) {
  const started = Date.now();
  let lastError = null;
  while (Date.now() - started < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.ok) {
        return await response.json();
      }
      lastError = new Error(`HTTP ${response.status}`);
    } catch (error) {
      lastError = error;
    }
    await new Promise((resolve) => setTimeout(resolve, 200));
  }
  throw lastError ?? new Error(`timed out waiting for ${url}`);
}

function samplePayload() {
  return {
    schema_version: 1,
    entry: "obsidian",
    message: "请用一句话确认模拟链路。",
    context: {
      vault: { name: "brain-notes" },
      active_file: {
        path: "书籍/README.md",
        basename: "README",
        extension: "md",
        size: 653,
        ctime: 0,
        mtime: 0,
      },
      selection: { text: "选中文本", chars: 4 },
      note: {
        text: "# README\n\n这是 Obsidian 插件模拟测试笔记。",
        chars: 34,
        truncated: false,
      },
      metadata: {
        headings: [{ heading: "README", level: 1, line: 0 }],
        tags: [{ tag: "#books", line: 2 }],
        links: [{ link: "Some Note", line: 3 }],
      },
      requested_mode: "current-note",
      local_time: "2026-06-10T00:00:00.000Z",
    },
    options: {
      context_mode: "current-note",
      task_mode: "ask",
      template_id: "ask",
      target_module: "auto",
      target_folder: "书籍",
      ui_language: "zh-CN",
      include_selection: true,
      include_active_note_body: true,
      permission_mode: "dontAsk",
      approval_forwarding: true,
    },
  };
}

async function checkSmokeGatewayContract() {
  const port = await getFreePort();
  const child = spawn(process.execPath, ["dev/smoke-gateway.mjs"], {
    cwd: root,
    env: {
      ...process.env,
      RTIME_OBSIDIAN_SMOKE_HOST: "127.0.0.1",
      RTIME_OBSIDIAN_SMOKE_PORT: String(port),
    },
    stdio: ["ignore", "pipe", "pipe"],
  });

  let stderr = "";
  child.stderr.on("data", (chunk) => {
    stderr += chunk.toString("utf8");
  });

  try {
    const health = await waitForHealth(`http://127.0.0.1:${port}/healthz`);
    assert.equal(health.ok, true);
    assert.equal(health.service, "rtime-obsidian-smoke-gateway");

    const response = await fetch(`http://127.0.0.1:${port}/api/obsidian/chat`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(samplePayload()),
    });
    assert.equal(response.status, 200);
    const data = await response.json();
    assert.equal(typeof data.answer, "string");
    assert.match(data.answer, /Smoke gateway|Smoke gateway 已连通/);
    assert.ok(Array.isArray(data.sources));
    assert.ok(data.sources.length >= 2);
    assert.equal(data.sources[0].path, "书籍/README.md");
    const prepareResponse = await fetch(`http://127.0.0.1:${port}/api/obsidian/prepare`, {
      method: "POST",
      headers: { "content-type": "application/json" },
      body: JSON.stringify(samplePayload()),
    });
    assert.equal(prepareResponse.status, 200);
    const prepare = await prepareResponse.json();
    assert.equal(prepare.ok, true);
    assert.match(prepare.prepare_id, /^prep-smoke-/);
    assert.equal(prepare.unlock_count, 1);
    log("smoke gateway accepts Obsidian chat and prepare payloads");
  } finally {
    child.kill("SIGTERM");
    await new Promise((resolve) => child.once("close", resolve));
  }

  if (stderr.trim()) {
    throw new Error(`smoke gateway wrote stderr: ${stderr.trim()}`);
  }
}

async function checkLiveGatewayHealth() {
  if (!args.has("--live")) {
    return;
  }
  const health = await waitForHealth("http://127.0.0.1:8765/healthz", 3000);
  assert.equal(health.ok, true);
  assert.equal(health.service, "rtime-obsidian-local-gateway");
  assert.ok(["remote-claude-kimi", "claude", "codex", "auto"].includes(health.runner));
  log(`live gateway health is ok with runner=${health.runner}`);
}

await checkKeyboardMatrix();
await checkComposerContract();
await checkResponseParser();
await checkAssistantClientHelpers();
await checkPluginUpdaterHelpers();
await checkBundleArtifacts();
await checkSmokeGatewayContract();
await checkLiveGatewayHealth();
