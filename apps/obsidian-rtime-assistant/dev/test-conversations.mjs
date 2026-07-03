// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Node tests for src/conversations.ts (run-10 session persistence).
 * Bundled standalone — the module has no `obsidian` imports by design.
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

function memoryAdapter() {
  const files = new Map();
  return {
    files,
    async exists(p) {
      return files.has(p);
    },
    async read(p) {
      if (!files.has(p)) {
        throw new Error(`missing: ${p}`);
      }
      return files.get(p);
    },
    async write(p, data) {
      files.set(p, data);
    },
    async remove(p) {
      files.delete(p);
    },
    async rename(p, np) {
      if (!files.has(p)) {
        throw new Error(`missing: ${p}`);
      }
      files.set(np, files.get(p));
      files.delete(p);
    },
  };
}

const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-conversations-"));
const outfile = path.join(tmpDir, "conversations.mjs");
try {
  await esbuild.build({
    entryPoints: [path.join(root, "src/conversations.ts")],
    bundle: true,
    format: "esm",
    platform: "node",
    outfile,
    logLevel: "silent",
  });
  const mod = await import(pathToFileURL(outfile).href);
  const {
    ConversationStore,
    buildHistoryPayload,
    conversationTitle,
    generatedConversationTitle,
    parseConversationsFile,
    trimConversations,
    CONVERSATIONS_FILE,
    MAX_CONVERSATIONS,
    MAX_MESSAGES_PER_CONVERSATION,
  } = mod;

  // --- title ---
  const title = conversationTitle("  什么是德拜模型？\n请展开讲讲低温极限下的行为  ");
  assert.equal(title, "知识库问答");
  assert.ok(!title.includes("\n"));
  assert.equal(conversationTitle("请解释这页", "explain", "15自由电子论.pdf · p.2"), "当前页解释");
  const generated = generatedConversationTitle({
    question: "它的低温极限行为是什么？",
    answer: "德拜模型的低温热容满足T^3定律。",
  });
  assert.equal(generated, "德拜模型");
  assert.notEqual(generated, "它的低温极限行为是什么？");
  log("conversation title uses provisional and generated summaries instead of raw prompts");

  // --- history payload ---
  const mixed = [
    { role: "system", content: "ignored" },
    { role: "user", content: "q1" },
    { role: "assistant", content: "a1" },
    { role: "assistant", content: "  " },
    { role: "assistant", content: "error card", isError: true },
    { role: "user", content: "q2" },
    { role: "assistant", content: "a2" },
  ];
  assert.deepEqual(buildHistoryPayload(mixed), [
    { role: "user", content: "q1" },
    { role: "assistant", content: "a1" },
    { role: "user", content: "q2" },
    { role: "assistant", content: "a2" },
  ]);

  const many = [];
  for (let i = 1; i <= 10; i += 1) {
    many.push({ role: "user", content: `q${i}` });
    many.push({ role: "assistant", content: `a${i}` });
  }
  const lastSixRounds = buildHistoryPayload(many);
  assert.equal(lastSixRounds.length, 12);
  assert.deepEqual(lastSixRounds[0], { role: "user", content: "q5" });
  assert.deepEqual(lastSixRounds.at(-1), { role: "assistant", content: "a10" });

  const budgeted = buildHistoryPayload(
    [
      { role: "user", content: "A".repeat(50) },
      { role: "assistant", content: "B".repeat(50) },
      { role: "user", content: "C".repeat(50) },
    ],
    6,
    120,
  );
  // whole messages newest-first within budget: C(50)+B(50) fit, A would overflow → dropped
  assert.deepEqual(
    budgeted.map((m) => m.content[0] + m.content.length),
    ["B50", "C50"],
  );
  const hugeNewest = buildHistoryPayload([{ role: "user", content: "X".repeat(9000) }], 6, 100);
  assert.equal(hugeNewest.length, 1);
  assert.equal(hugeNewest[0].content.length, 100); // newest alone is tail-clipped, never dropped
  log("history payload filters, windows 6 rounds, and budgets newest-first");

  // --- trim caps ---
  const overflow = [];
  for (let i = 0; i < 60; i += 1) {
    overflow.push({
      id: `c${i}`,
      title: `t${i}`,
      created: i,
      updated: i,
      messages: Array.from({ length: 120 }, (_, j) => ({ role: "user", content: `m${j}` })),
    });
  }
  const trimmed = trimConversations(overflow);
  assert.equal(trimmed.length, MAX_CONVERSATIONS);
  assert.equal(trimmed[0].id, "c59"); // newest kept
  assert.equal(trimmed.at(-1).id, "c10"); // oldest 10 dropped
  for (const conversation of trimmed) {
    assert.equal(conversation.messages.length, MAX_MESSAGES_PER_CONVERSATION);
    assert.equal(conversation.messages[0].content, "m20"); // oldest messages dropped
  }
  log(`trim keeps newest ${MAX_CONVERSATIONS} conversations and last ${MAX_MESSAGES_PER_CONVERSATION} messages`);

  // --- parse robustness ---
  assert.deepEqual(parseConversationsFile("not json"), []);
  assert.deepEqual(parseConversationsFile('{"version":1}'), []);
  assert.deepEqual(parseConversationsFile('{"conversations":[{"id":1},null,"x"]}'), []);
  log("parser survives corrupt and malformed files");

  // --- store roundtrip ---
  const adapter = memoryAdapter();
  const store = new ConversationStore(adapter, "plugins/rtime-assistant");
  await store.load("");
  assert.ok(store.getActiveId(), "fresh store starts an active conversation");
  assert.equal(store.getActive().messages.length, 0);

  store.appendMessage({ role: "user", content: "什么是德拜模型？这是一个很长的标题测试问题" });
  store.appendMessage({ role: "assistant", content: "德拜模型是……" });
  assert.equal(store.getActive().title, "知识库问答");
  assert.equal(store.getActive().title_status, "provisional");
  store.setTitle(store.getActiveId(), generatedConversationTitle({
    question: "什么是德拜模型？这是一个很长的标题测试问题",
    answer: "德拜模型是晶格振动声子谱模型。",
  }));
  assert.equal(store.getActive().title, "德拜模型");
  assert.equal(store.getActive().title_status, "generated");
  const firstId = store.getActiveId();

  await store.flush();
  const filePath = `plugins/rtime-assistant/${CONVERSATIONS_FILE}`;
  assert.ok(adapter.files.has(filePath), "conversations.json written");
  assert.ok(!adapter.files.has(`${filePath}.tmp`), "tmp file renamed away (atomic write)");

  await new Promise((resolve) => setTimeout(resolve, 5)); // ms-resolution timestamps need real elapsed time
  const second = store.startNew();
  store.appendMessage({ role: "user", content: "第二个会话" });
  await store.flush();

  // restart: preferred id restored with messages intact
  const reloaded = new ConversationStore(memoryReuse(adapter), "plugins/rtime-assistant");
  await reloaded.load(firstId);
  assert.equal(reloaded.getActiveId(), firstId);
  assert.equal(reloaded.getActive().messages.length, 2);
  assert.equal(reloaded.list().length, 2);

  // restart with unknown preferred id: falls back to most recent
  const fallback = new ConversationStore(memoryReuse(adapter), "plugins/rtime-assistant");
  await fallback.load("does-not-exist");
  assert.equal(fallback.getActiveId(), second.id);

  // delete active → next most recent becomes active and file shrinks
  const next = reloaded.deleteActive();
  assert.notEqual(next.id, firstId);
  await reloaded.flush();
  const persisted = JSON.parse(adapter.files.get(filePath));
  assert.equal(persisted.conversations.length, 1);
  assert.equal(persisted.conversations[0].id, second.id);

  // crash recovery: only tmp present → load still finds the data
  const crashAdapter = memoryAdapter();
  crashAdapter.files.set(
    `plugins/rtime-assistant/${CONVERSATIONS_FILE}.tmp`,
    JSON.stringify({
      version: 1,
      conversations: [
        { id: "c-tmp", title: "t", created: 1, updated: 1, messages: [{ role: "user", content: "hi" }] },
      ],
    }),
  );
  const recovered = new ConversationStore(crashAdapter, "plugins/rtime-assistant");
  await recovered.load("c-tmp");
  assert.equal(recovered.getActiveId(), "c-tmp");
  log("store persists atomically, restores by id, deletes, and recovers from tmp");

  // empty conversations never hit disk
  const emptyStore = new ConversationStore(adapter, "plugins/rtime-assistant");
  await emptyStore.load("");
  emptyStore.startNew();
  emptyStore.appendMessage({ role: "user", content: "only one with content" });
  await emptyStore.flush();
  const onDisk = JSON.parse(adapter.files.get(filePath));
  assert.ok(onDisk.conversations.every((c) => c.messages.length > 0));
  log("empty conversations stay in memory only");
} finally {
  await rm(tmpDir, { recursive: true, force: true });
}

function memoryReuse(adapter) {
  return adapter; // same Map → simulates the same disk across restarts
}
