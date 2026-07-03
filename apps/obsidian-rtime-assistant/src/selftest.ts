// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Plugin selftest + file-trigger channel.
 *
 * Lets a command-line agent test the live plugin without touching the UI:
 * write `selftest-request.json` ({"id": "..."}) into the plugin folder, and
 * within ~20s the plugin runs health/ask/stream/markdown checks through its
 * real client stack and writes `selftest-report.json` next to it. Also
 * exposed as a manual command.
 */

import { Component, MarkdownRenderer, normalizePath } from "obsidian";
import type RtimeAssistantPlugin from "./main";
import { supportsNodeTransport } from "./services/transport";

export const SELFTEST_REQUEST_FILE = "selftest-request.json";
export const SELFTEST_REPORT_FILE = "selftest-report.json";

const SELFTEST_QUESTION = "这是插件自检。请直接回复四个字：自检通过。不要调用任何工具。";
const SELFTEST_FOLLOWUP =
  "我上一条消息让你直接回复的四个字是什么？只回答那四个字，不要调用任何工具。";

export interface SelftestItem {
  ok: boolean;
  ms?: number;
  detail?: string;
}

export interface SelftestReport {
  id: string;
  ts: string;
  pluginVersion: string;
  endpoint: string;
  transport: "node-http" | "chromium";
  ok: boolean;
  health: SelftestItem;
  ask: SelftestItem & { chars?: number; sources?: number };
  stream: SelftestItem & { firstDeltaMs?: number; deltas?: number; statuses?: number };
  markdown: SelftestItem;
  /** 第5项（run-10）：同一conversation_id下第二问指代第一问，检验续聊解析。 */
  followup: SelftestItem & { conversationId?: string };
}

let selftestRunning = false;

export function isSelftestRunning(): boolean {
  return selftestRunning;
}

export async function runSelftest(plugin: RtimeAssistantPlugin, id: string): Promise<SelftestReport> {
  const settings = plugin.settings;
  const report: SelftestReport = {
    id,
    ts: new Date().toISOString(),
    pluginVersion: plugin.manifest.version,
    endpoint: settings.chatEndpoint,
    transport: supportsNodeTransport(settings.chatEndpoint) ? "node-http" : "chromium",
    ok: false,
    health: { ok: false },
    ask: { ok: false },
    stream: { ok: false },
    markdown: { ok: false },
    followup: { ok: false },
  };
  const conversationId = `selftest-${Date.now().toString(36)}`;

  let start = Date.now();
  try {
    const text = await plugin.checkHealth();
    report.health = {
      ok: text.trim().length > 0,
      ms: Date.now() - start,
      detail: text.slice(0, 80),
    };
  } catch (error) {
    report.health = { ok: false, ms: Date.now() - start, detail: errorText(error) };
  }

  start = Date.now();
  let askAnswer = "";
  try {
    const result = await plugin.askAssistant(SELFTEST_QUESTION, "ask", { conversationId });
    askAnswer = result.answer;
    report.ask = {
      ok: result.answer.trim().length > 0,
      ms: Date.now() - start,
      chars: result.answer.length,
      sources: result.sources.length,
    };
  } catch (error) {
    report.ask = { ok: false, ms: Date.now() - start, detail: errorText(error) };
  }

  start = Date.now();
  let firstDeltaMs: number | undefined;
  let deltas = 0;
  let statuses = 0;
  try {
    const result = await plugin.streamAssistant(SELFTEST_QUESTION, "ask", {
      onStatus: () => {
        statuses += 1;
      },
      onDelta: () => {
        if (firstDeltaMs === undefined) {
          firstDeltaMs = Date.now() - start;
        }
        deltas += 1;
      },
    });
    report.stream = {
      ok: result.answer.trim().length > 0,
      ms: Date.now() - start,
      firstDeltaMs,
      deltas,
      statuses,
    };
  } catch (error) {
    report.stream = {
      ok: false,
      ms: Date.now() - start,
      firstDeltaMs,
      deltas,
      statuses,
      detail: errorText(error),
    };
  }

  const host = document.createElement("div");
  const component = new Component();
  component.load();
  try {
    await MarkdownRenderer.render(plugin.app, "**rtime** 自检 $E=mc^2$", host, "", component);
    report.markdown = { ok: host.querySelector("strong") !== null };
  } catch (error) {
    report.markdown = { ok: false, detail: errorText(error) };
  } finally {
    component.unload();
  }

  // 续聊：第二问只有靠history里的第一问才答得出"自检通过"。
  start = Date.now();
  if (report.ask.ok && askAnswer.trim()) {
    try {
      const result = await plugin.askAssistant(SELFTEST_FOLLOWUP, "ask", {
        conversationId,
        history: [
          { role: "user", content: SELFTEST_QUESTION },
          { role: "assistant", content: askAnswer },
        ],
      });
      report.followup = {
        ok: result.answer.includes("自检通过"),
        ms: Date.now() - start,
        conversationId,
        detail: result.answer.slice(0, 80),
      };
    } catch (error) {
      report.followup = {
        ok: false,
        ms: Date.now() - start,
        conversationId,
        detail: errorText(error),
      };
    }
  } else {
    report.followup = { ok: false, conversationId, detail: "skipped: ask item failed" };
  }

  report.ok =
    report.health.ok && report.ask.ok && report.stream.ok && report.markdown.ok && report.followup.ok;
  return report;
}

export async function writeSelftestReport(
  plugin: RtimeAssistantPlugin,
  report: SelftestReport,
): Promise<void> {
  const path = normalizePath(`${pluginDir(plugin)}/${SELFTEST_REPORT_FILE}`);
  await plugin.app.vault.adapter.write(path, JSON.stringify(report, null, 2));
}

/** Poll the request file once; returns true if a selftest ran. */
export async function pollSelftestRequest(plugin: RtimeAssistantPlugin): Promise<boolean> {
  if (selftestRunning) {
    return false;
  }
  // User requests always win the single gateway slot; the request file stays
  // on disk and the next 20s tick retries.
  if (plugin.isRequestInFlight()) {
    return false;
  }
  const adapter = plugin.app.vault.adapter;
  const requestPath = normalizePath(`${pluginDir(plugin)}/${SELFTEST_REQUEST_FILE}`);
  try {
    if (!(await adapter.exists(requestPath))) {
      return false;
    }
  } catch {
    return false;
  }

  selftestRunning = true;
  try {
    let id = `file-${Date.now()}`;
    try {
      const parsed = JSON.parse(await adapter.read(requestPath)) as { id?: unknown };
      if (typeof parsed?.id === "string" && parsed.id.trim()) {
        id = parsed.id.trim();
      }
    } catch {
      // malformed request file still triggers a selftest with a generated id
    }
    try {
      await adapter.remove(requestPath);
    } catch {
      // leaving the file would retrigger; report id makes reruns identifiable
    }
    const report = await runSelftest(plugin, id);
    await writeSelftestReport(plugin, report);
    return true;
  } finally {
    selftestRunning = false;
  }
}

export async function runSelftestManually(plugin: RtimeAssistantPlugin): Promise<SelftestReport | null> {
  if (selftestRunning) {
    return null;
  }
  selftestRunning = true;
  try {
    const report = await runSelftest(plugin, `manual-${Date.now()}`);
    await writeSelftestReport(plugin, report);
    return report;
  } finally {
    selftestRunning = false;
  }
}

function pluginDir(plugin: RtimeAssistantPlugin): string {
  return `${plugin.app.vault.configDir}/plugins/${plugin.manifest.id}`;
}

function errorText(error: unknown): string {
  return (error instanceof Error ? error.message : String(error)).slice(0, 200);
}
