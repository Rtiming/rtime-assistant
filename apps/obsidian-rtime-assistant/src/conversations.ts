// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Conversation persistence for the sidebar (run-10 session system).
 *
 * Conversations live in `conversations.json` inside the plugin folder —
 * plugin state, not vault notes. Writes are debounced (≥2s) and atomic
 * (tmp file + rename) so an Obsidian crash never leaves a torn file; load
 * falls back to the tmp file if a crash happened between remove and rename.
 *
 * No `obsidian` imports here: the file adapter is injected, which keeps the
 * whole module testable from node (dev/test-conversations.mjs).
 */

import type { ChatMessage, Conversation, ConversationTitleStatus, HistoryItem, TaskMode } from "./types";

export const CONVERSATIONS_FILE = "conversations.json";
export const MAX_CONVERSATIONS = 50;
export const MAX_MESSAGES_PER_CONVERSATION = 100;
export const SAVE_DEBOUNCE_MS = 2000;
export const TITLE_MAX_CHARS = 20;
export const HISTORY_ROUNDS = 6;
export const HISTORY_MAX_CHARS = 4000;
export const GENERATED_TITLE_MAX_CHARS = 18;

/** Subset of Obsidian's DataAdapter the store needs (injectable for tests). */
export interface ConversationFileAdapter {
  exists(path: string): Promise<boolean>;
  read(path: string): Promise<string>;
  write(path: string, data: string): Promise<void>;
  remove(path: string): Promise<void>;
  rename(path: string, newPath: string): Promise<void>;
}

interface ConversationsFileShape {
  version: 1;
  conversations: Conversation[];
}

export function conversationTitle(
  firstQuestion: string,
  taskMode: TaskMode = "ask",
  locationLabel = "",
): string {
  const location = locationLabel.replace(/\s+/g, " ").trim();
  const question = firstQuestion.replace(/\s+/g, " ").trim();
  if (taskMode === "explain") {
    return location.toLowerCase().includes(".pdf") || location.includes("p.")
      ? "当前页解释"
      : "选区解释";
  }
  if (taskMode === "summarize") {
    return location ? "当前材料总结" : "笔记总结";
  }
  if (taskMode === "related") {
    return "相关材料检索";
  }
  if (taskMode === "citation-review") {
    return "引文审阅";
  }
  if (/选区|这段|这页|当前页|解释/.test(question)) {
    return location.toLowerCase().includes(".pdf") ? "当前页问答" : "选区问答";
  }
  return location ? "当前材料问答" : "知识库问答";
}

export function generatedConversationTitle(input: {
  question: string;
  answer?: string;
  locationLabel?: string;
  taskMode?: TaskMode;
}): string {
  const combined = `${input.locationLabel ?? ""} ${input.question} ${input.answer ?? ""}`;
  const candidates = [
    /什么是([\u4e00-\u9fa5A-Za-z0-9]+)/,
    /解释([\u4e00-\u9fa5A-Za-z0-9]+)/,
    /([\u4e00-\u9fa5A-Za-z0-9]+(?:模型|方程|色散|声子|光子|备案|证书|入库|课件|记忆|流式|选区))/,
  ];
  for (const pattern of candidates) {
    const match = combined.match(pattern);
    if (match?.[1]) {
      return sanitizeTitle(match[1]);
    }
  }
  const fallback = conversationTitle(input.question, input.taskMode ?? "ask", input.locationLabel ?? "");
  return sanitizeTitle(fallback);
}

function sanitizeTitle(value: string): string {
  const compact = value
    .replace(/[`*_#[\](){}<>|]/g, "")
    .replace(/\s+/g, "")
    .replace(/[。！？?!，,：:；;]+$/g, "");
  return (compact || "新对话").slice(0, GENERATED_TITLE_MAX_CHARS);
}

/** Newest-first by updated; ties keep insertion order. */
export function sortConversations(conversations: Conversation[]): Conversation[] {
  return [...conversations].sort((a, b) => b.updated - a.updated);
}

/** Enforce the storage caps: newest 50 conversations, last 100 messages each. */
export function trimConversations(conversations: Conversation[]): Conversation[] {
  return sortConversations(conversations)
    .slice(0, MAX_CONVERSATIONS)
    .map((conversation) =>
      conversation.messages.length <= MAX_MESSAGES_PER_CONVERSATION
        ? conversation
        : { ...conversation, messages: conversation.messages.slice(-MAX_MESSAGES_PER_CONVERSATION) },
    );
}

/**
 * Build the context.history payload from conversation messages: last N rounds,
 * text only (sources dropped), error cards and blanks skipped, whole messages
 * kept newest-first within the char budget (only the newest message may be
 * tail-clipped when it alone exceeds the budget).
 */
export function buildHistoryPayload(
  messages: ChatMessage[],
  rounds: number = HISTORY_ROUNDS,
  maxChars: number = HISTORY_MAX_CHARS,
): HistoryItem[] {
  const usable = messages.filter(
    (message) =>
      (message.role === "user" || message.role === "assistant") &&
      !message.isError &&
      message.content.trim().length > 0,
  );
  const recent = usable.slice(-rounds * 2);
  const kept: HistoryItem[] = [];
  let remaining = maxChars;
  for (let i = recent.length - 1; i >= 0; i -= 1) {
    const message = recent[i];
    let content = message.content;
    if (content.length > remaining) {
      if (kept.length > 0) {
        break;
      }
      content = content.slice(-remaining);
    }
    kept.unshift({ role: message.role as "user" | "assistant", content });
    remaining -= content.length;
    if (remaining <= 0) {
      break;
    }
  }
  return kept;
}

export function parseConversationsFile(raw: string): Conversation[] {
  let data: unknown;
  try {
    data = JSON.parse(raw);
  } catch {
    return [];
  }
  const shape = data as Partial<ConversationsFileShape> | null;
  if (!shape || !Array.isArray(shape.conversations)) {
    return [];
  }
  const result: Conversation[] = [];
  for (const item of shape.conversations) {
    if (!item || typeof item !== "object") {
      continue;
    }
    const conversation = item as Partial<Conversation>;
    if (typeof conversation.id !== "string" || !Array.isArray(conversation.messages)) {
      continue;
    }
    result.push({
      id: conversation.id,
      title: typeof conversation.title === "string" ? conversation.title : "",
      title_status: isTitleStatus(conversation.title_status) ? conversation.title_status : undefined,
      created: typeof conversation.created === "number" ? conversation.created : 0,
      updated: typeof conversation.updated === "number" ? conversation.updated : 0,
      messages: conversation.messages.filter(
        (message): message is ChatMessage =>
          !!message &&
          typeof message === "object" &&
          typeof (message as ChatMessage).content === "string" &&
          typeof (message as ChatMessage).role === "string",
      ),
    });
  }
  return result;
}

function isTitleStatus(value: unknown): value is ConversationTitleStatus {
  return value === "provisional" || value === "generated" || value === "manual";
}

export class ConversationStore {
  private conversations: Conversation[] = [];
  private activeId: string | null = null;
  private saveTimer: ReturnType<typeof setTimeout> | null = null;
  private dirty = false;
  private saving: Promise<void> = Promise.resolve();

  constructor(
    private readonly adapter: ConversationFileAdapter,
    private readonly pluginDir: string,
  ) {}

  private get filePath(): string {
    return `${this.pluginDir}/${CONVERSATIONS_FILE}`;
  }

  private get tmpPath(): string {
    return `${this.filePath}.tmp`;
  }

  /** Load from disk and pick the active conversation (saved id → most recent → fresh). */
  async load(preferredId: string): Promise<void> {
    let raw: string | null = null;
    try {
      if (await this.adapter.exists(this.filePath)) {
        raw = await this.adapter.read(this.filePath);
      } else if (await this.adapter.exists(this.tmpPath)) {
        raw = await this.adapter.read(this.tmpPath); // crash between remove and rename
      }
    } catch {
      raw = null;
    }
    this.conversations = trimConversations(raw ? parseConversationsFile(raw) : []);
    const preferred = this.conversations.find((c) => c.id === preferredId);
    const fallback = sortConversations(this.conversations)[0];
    this.activeId = (preferred ?? fallback)?.id ?? null;
    if (this.activeId === null) {
      this.startNew();
    }
  }

  /** Conversations newest-first, for the session dropdown. */
  list(): Conversation[] {
    return sortConversations(this.conversations);
  }

  getActive(): Conversation | null {
    return this.conversations.find((c) => c.id === this.activeId) ?? null;
  }

  getActiveId(): string | null {
    return this.activeId;
  }

  setActive(id: string): Conversation | null {
    const found = this.conversations.find((c) => c.id === id);
    if (found) {
      this.activeId = found.id;
    }
    return found ?? null;
  }

  /** Create and activate an empty conversation (persisted once it has messages). */
  startNew(): Conversation {
    const now = Date.now();
    const conversation: Conversation = {
      id: `conv-${now.toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
      title: "",
      title_status: "provisional",
      created: now,
      updated: now,
      messages: [],
    };
    this.conversations.push(conversation);
    this.activeId = conversation.id;
    return conversation;
  }

  /** Append to the active conversation; first user message becomes the title. */
  appendMessage(message: ChatMessage): Conversation {
    const conversation = this.getActive() ?? this.startNew();
    conversation.messages.push(message);
    if (!conversation.title && message.role === "user" && message.content.trim()) {
      conversation.title = conversationTitle(message.content);
      conversation.title_status = "provisional";
    }
    if (conversation.messages.length > MAX_MESSAGES_PER_CONVERSATION) {
      conversation.messages = conversation.messages.slice(-MAX_MESSAGES_PER_CONVERSATION);
    }
    conversation.updated = Date.now();
    this.scheduleSave();
    return conversation;
  }

  setTitle(id: string, title: string, status: ConversationTitleStatus = "generated"): Conversation | null {
    const conversation = this.conversations.find((c) => c.id === id);
    if (!conversation) {
      return null;
    }
    conversation.title = title;
    conversation.title_status = status;
    conversation.updated = Date.now();
    this.scheduleSave();
    return conversation;
  }

  /** Bump updated + persist; call after streaming mutates a message in place. */
  touch(): void {
    const conversation = this.getActive();
    if (conversation) {
      conversation.updated = Date.now();
    }
    this.scheduleSave();
  }

  /** Delete the active conversation and activate the next most recent (or a fresh one). */
  deleteActive(): Conversation {
    this.conversations = this.conversations.filter((c) => c.id !== this.activeId);
    this.activeId = sortConversations(this.conversations)[0]?.id ?? null;
    this.scheduleSave();
    return this.getActive() ?? this.startNew();
  }

  private scheduleSave(): void {
    this.dirty = true;
    if (this.saveTimer !== null) {
      return;
    }
    this.saveTimer = setTimeout(() => {
      this.saveTimer = null;
      void this.flush();
    }, SAVE_DEBOUNCE_MS);
  }

  /** Write now (atomic tmp+rename). Used by the debounce timer and plugin unload. */
  async flush(): Promise<void> {
    if (!this.dirty) {
      return;
    }
    this.dirty = false;
    if (this.saveTimer !== null) {
      clearTimeout(this.saveTimer);
      this.saveTimer = null;
    }
    // Serialize writers so a slow rename never races a newer write.
    this.saving = this.saving.then(() => this.writeFile()).catch(() => undefined);
    await this.saving;
  }

  private async writeFile(): Promise<void> {
    const persisted = trimConversations(
      this.conversations.filter((c) => c.messages.length > 0),
    );
    const payload: ConversationsFileShape = { version: 1, conversations: persisted };
    const json = JSON.stringify(payload, null, 2);
    try {
      await this.adapter.write(this.tmpPath, json);
      if (await this.adapter.exists(this.filePath)) {
        await this.adapter.remove(this.filePath);
      }
      await this.adapter.rename(this.tmpPath, this.filePath);
    } catch {
      // Last resort: direct write beats losing the session on quit.
      try {
        await this.adapter.write(this.filePath, json);
      } catch {
        // disk unavailable; in-memory state remains usable
      }
    }
  }
}
