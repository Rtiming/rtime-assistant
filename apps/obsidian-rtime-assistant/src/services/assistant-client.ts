// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { buildComposerRouteHint } from "../composer-contract";
import type {
  AssistantContext,
  AssistantModelCatalog,
  AssistantPrepareResult,
  AssistantRequestBody,
  AssistantResult,
  RtimeAssistantSettings,
  StreamTrace,
  TaskMode,
} from "../types";
import { normalizeLocalhostUrl, requestWithRetry } from "./http";
import { parseAssistantResult, parseJsonOrText } from "./response";
import { nodeHttpStream, supportsNodeTransport } from "./transport";

interface CachedHealth {
  endpoint: string;
  expiresAt: number;
  text: string;
}

/** Session fields threaded into the request body (all optional, v0.2-compatible). */
export interface AssistantSessionInfo {
  conversationId?: string;
  prepareId?: string;
}

export interface AssistantClient {
  prepareContext(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    session?: AssistantSessionInfo,
  ): Promise<AssistantPrepareResult>;
  postAssistantRequest(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    message: string,
    taskMode: TaskMode,
    session?: AssistantSessionInfo,
  ): Promise<AssistantResult>;
  postAssistantStreamRequest(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    message: string,
    taskMode: TaskMode,
    handlers: AssistantStreamHandlers,
    session?: AssistantSessionInfo,
  ): Promise<AssistantResult>;
  checkBackendHealth(settings: RtimeAssistantSettings): Promise<string>;
  clearHealthCache(): void;
  /** Cancel the in-flight streaming request, if any (frees the gateway slot). */
  cancelActive(): void;
}

export function isBusyError(error: unknown): boolean {
  return error instanceof Error && error.message.includes("HTTP 503");
}

export function isStreamServerError(error: unknown): boolean {
  return error instanceof Error && error.message.startsWith("Assistant stream error:");
}

export interface AssistantStreamHandlers {
  onStatus?(text: string): void;
  onApprovalRequest?(text: string): void;
  onDelta?(text: string): void;
  onDone?(result: AssistantResult): void;
  onTrace?(trace: StreamTrace): void;
}

export function createAssistantClient(): AssistantClient {
  return new DefaultAssistantClient();
}

class DefaultAssistantClient implements AssistantClient {
  private cachedHealth: CachedHealth | null = null;
  private activeStreamAbort: AbortController | null = null;

  cancelActive(): void {
    this.activeStreamAbort?.abort();
  }

  async prepareContext(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    session?: AssistantSessionInfo,
  ): Promise<AssistantPrepareResult> {
    const url = prepareEndpointFromChat(settings.chatEndpoint);
    if (url === settings.chatEndpoint) {
      throw new Error("chatEndpoint does not end with /api/obsidian/chat; cannot derive prepare endpoint");
    }
    const body = buildAssistantRequestBody(
      settings,
      context,
      "",
      settings.defaultTaskMode,
      false,
      session,
    );
    const response = await requestWithRetry({
      method: "POST",
      url,
      contentType: "application/json",
      headers: { Accept: "application/json" },
      body: JSON.stringify(body),
      timeoutMs: Math.min(settings.requestTimeoutMs, 10000),
      retryCount: Math.min(settings.requestRetryCount, 1),
      retryDelayMs: settings.requestRetryDelayMs,
    });
    const data = response.json ?? parseJsonOrText(response.text);
    if (response.status >= 400 || !isPrepareResult(data)) {
      throw new Error(`Prepare endpoint returned HTTP ${response.status}`);
    }
    return data;
  }

  async postAssistantRequest(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    message: string,
    taskMode: TaskMode,
    session?: AssistantSessionInfo,
  ): Promise<AssistantResult> {
    const body = buildAssistantRequestBody(settings, context, message, taskMode, false, session);
    const response = await requestWithRetry({
      method: "POST",
      url: resolveChatEndpoint(settings),
      contentType: "application/json",
      headers: {
        Accept: "application/json",
      },
      body: JSON.stringify(body),
      timeoutMs: settings.requestTimeoutMs,
      retryCount: settings.requestRetryCount,
      retryDelayMs: settings.requestRetryDelayMs,
    });

    if (response.status >= 400) {
      throw new Error(`Assistant endpoint returned HTTP ${response.status}: ${response.text}`);
    }

    const data = response.json ?? parseJsonOrText(response.text);
    return parseAssistantResult(data);
  }

  async postAssistantStreamRequest(
    settings: RtimeAssistantSettings,
    context: AssistantContext,
    message: string,
    taskMode: TaskMode,
    handlers: AssistantStreamHandlers,
    session?: AssistantSessionInfo,
  ): Promise<AssistantResult> {
    const url = normalizeLocalhostUrl(resolveChatEndpoint(settings));
    const body = buildAssistantRequestBody(settings, context, message, taskMode, true, session);

    // Desktop: Node sockets bypass the system proxy (Chromium fetch does not).
    if (supportsNodeTransport(url)) {
      const state = createStreamState();
      const abort = new AbortController();
      this.activeStreamAbort = abort;
      let buffer = "";
      try {
        const response = await nodeHttpStream(
          {
            url,
            method: "POST",
            headers: {
              "Content-Type": "application/json",
              Accept: "text/event-stream",
            },
            body: JSON.stringify(body),
            timeoutMs: settings.requestTimeoutMs,
            signal: abort.signal,
          },
          (chunk) => {
            markTrace(state.trace, "first_chunk_received_ms");
            buffer += chunk;
            buffer = consumeSseFrames(buffer, handlers, state);
          },
        );
        if (response.status >= 400) {
          throw new Error(`Assistant endpoint returned HTTP ${response.status}: ${response.text}`);
        }
        consumeSseFrames(`${buffer}\n\n`, handlers, state);
        const result = state.finalResult ?? {
          answer: state.answerParts.join(""),
          sources: [],
        };
        result.trace = mergeTrace(result.trace, state.trace);
        handlers.onDone?.(result);
        return result;
      } finally {
        if (this.activeStreamAbort === abort) {
          this.activeStreamAbort = null;
        }
      }
    }

    if (typeof fetch !== "function") {
      throw new Error("Streaming fetch is not available in this Obsidian runtime");
    }
    const controller = new AbortController();
    this.activeStreamAbort = controller;
    const timeoutHandle = setTimeout(() => {
      controller.abort();
    }, settings.requestTimeoutMs);
    const state = createStreamState();

    try {
      const response = await fetch(normalizeLocalhostUrl(resolveChatEndpoint(settings)), {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          Accept: "text/event-stream",
        },
        body: JSON.stringify(body),
        signal: controller.signal,
      });
      if (!response.ok) {
        const text = await response.text();
        throw new Error(`Assistant endpoint returned HTTP ${response.status}: ${text}`);
      }
      if (!response.body) {
        throw new Error("Assistant endpoint did not return a streaming body");
      }

      const reader = response.body.getReader();
      const decoder = new TextDecoder();
      let buffer = "";
      while (true) {
        const { done, value } = await reader.read();
        if (done) {
          break;
        }
        markTrace(state.trace, "first_chunk_received_ms");
        buffer += decoder.decode(value, { stream: true });
        buffer = consumeSseFrames(buffer, handlers, state);
      }
      buffer += decoder.decode();
      consumeSseFrames(`${buffer}\n\n`, handlers, state);

      const result = state.finalResult ?? {
        answer: state.answerParts.join(""),
        sources: [],
      };
      result.trace = mergeTrace(result.trace, state.trace);
      handlers.onDone?.(result);
      return result;
    } finally {
      clearTimeout(timeoutHandle);
      if (this.activeStreamAbort === controller) {
        this.activeStreamAbort = null;
      }
    }
  }

  async checkBackendHealth(settings: RtimeAssistantSettings): Promise<string> {
    const now = Date.now();
    if (
      this.cachedHealth &&
      this.cachedHealth.endpoint === settings.healthEndpoint &&
      this.cachedHealth.expiresAt > now
    ) {
      return this.cachedHealth.text;
    }

    const response = await requestWithRetry({
      method: "GET",
      url: settings.healthEndpoint,
      timeoutMs: Math.min(settings.requestTimeoutMs, 15000),
      retryCount: Math.min(settings.requestRetryCount, 1),
      retryDelayMs: settings.requestRetryDelayMs,
    });
    if (response.status >= 400) {
      throw new Error(`Health check returned HTTP ${response.status}`);
    }

    const text = response.text || "ok";
    const cacheMs = Math.max(0, settings.healthCacheMs);
    this.cachedHealth = cacheMs > 0
      ? { endpoint: settings.healthEndpoint, expiresAt: now + cacheMs, text }
      : null;
    return text;
  }

  clearHealthCache(): void {
    this.cachedHealth = null;
  }
}

export function buildAssistantRequestBody(
  settings: RtimeAssistantSettings,
  context: AssistantContext,
  message: string,
  taskMode: TaskMode,
  stream = false,
  session?: AssistantSessionInfo,
): AssistantRequestBody {
  return {
    schema_version: 1,
    entry: "obsidian",
    message,
    ...(session?.conversationId ? { conversation_id: session.conversationId } : {}),
    ...(session?.prepareId ? { prepare_id: session.prepareId } : {}),
    context,
    options: {
      context_mode: settings.contextMode,
      task_mode: taskMode,
      ...buildComposerRouteHint(settings, taskMode),
      ui_language: settings.uiLanguage,
      include_selection: settings.includeSelection,
      include_active_note_body: settings.includeActiveNoteBody,
      trace_stream: stream,
      prewarm_model: settings.modelPrewarmEnabled,
      permission_mode: settings.permissionMode,
      approval_forwarding: settings.approvalForwardingEnabled,
      ...(settings.maxToolTurns > 0 ? { max_tool_turns: settings.maxToolTurns } : {}),
      ...(settings.modelThinkingEffort ? { thinking_effort: settings.modelThinkingEffort } : {}),
      ...(settings.modelEditMode ? { edit_mode: true } : {}),
      // Only send the local Mac workspace path for the claude-local provider (which
      // routes to the local gateway) — never leak it to the orangepi main gateway.
      ...(settings.modelProviderId === "claude-local" && settings.localClaudeWorkspace
        ? {
            local_workspace: settings.localClaudeWorkspace,
            local_workspace_read_only: settings.localClaudeWorkspaceReadOnly,
          }
        : {}),
      ...(settings.modelProviderId && settings.modelId && settings.modelProtocol
        ? {
            model_provider_id: settings.modelProviderId,
            model_id: settings.modelId,
            model_protocol: settings.modelProtocol,
          }
        : {}),
    },
    ...(stream ? { stream: true } : {}),
  };
}

interface StreamState {
  answerParts: string[];
  finalResult: AssistantResult | null;
  trace: StreamTrace;
}

function createStreamState(): StreamState {
  return {
    answerParts: [],
    finalResult: null,
    trace: { request_started_ms: Date.now() },
  };
}

export function markTrace(trace: StreamTrace, key: keyof StreamTrace): void {
  if (trace[key] === undefined) {
    (trace as Record<string, number>)[key] = Date.now();
  }
}

function mergeTrace(remote: StreamTrace | undefined, local: StreamTrace): StreamTrace {
  return {
    ...local,
    ...(remote ?? {}),
    gateway: remote?.gateway,
  };
}

/** Exported for unit testing: pure SSE frame splitter (CRLF-normalizing,
 * boundary-safe) that dispatches status/delta/done/error to the handlers. */
export function consumeSseFrames(
  buffer: string,
  handlers: AssistantStreamHandlers,
  state: StreamState,
): string {
  let rest = buffer.replace(/\r\n/g, "\n");
  let frameEnd = rest.indexOf("\n\n");
  while (frameEnd >= 0) {
    const frame = rest.slice(0, frameEnd);
    rest = rest.slice(frameEnd + 2);
    handleSseFrame(frame, handlers, state);
    frameEnd = rest.indexOf("\n\n");
  }
  return rest;
}

function handleSseFrame(
  frame: string,
  handlers: AssistantStreamHandlers,
  state: StreamState,
): void {
  const payload = frame
    .split("\n")
    .filter((line) => line.startsWith("data:"))
    .map((line) => line.slice(5).trimStart())
    .join("\n")
    .trim();
  if (!payload || payload === "[DONE]") {
    return;
  }
  const data = parseJsonOrText(payload);
  if (!isRecord(data)) {
    return;
  }

  const type = asString(data.type) ?? "";
  if (type === "status") {
    const text = asString(data.text) ?? asString(data.message);
    if (text) {
      handlers.onStatus?.(text);
    }
    return;
  }
  if (type === "approval_request") {
    const text = asString(data.text) ?? asString(data.message) ?? "Approval requested";
    handlers.onApprovalRequest?.(text);
    if (!handlers.onApprovalRequest) {
      handlers.onStatus?.(text);
    }
    return;
  }
  if (type === "delta") {
    const text = asString(data.text) ?? asString(data.delta) ?? asString(data.content);
    if (text) {
      markTrace(state.trace, "first_delta_parsed_ms");
      state.answerParts.push(text);
      handlers.onDelta?.(text);
    }
    return;
  }
  if (type === "done") {
    state.finalResult = parseAssistantResult(data);
    markTrace(state.trace, "done_received_ms");
    state.finalResult.trace = mergeTrace(state.finalResult.trace, state.trace);
    handlers.onTrace?.(state.finalResult.trace);
    return;
  }
  if (type === "error") {
    throw new Error(`Assistant stream error: ${asString(data.message) ?? asString(data.text) ?? "Assistant stream failed"}`);
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

export interface AttachmentIntakeResult {
  needsConfirm: boolean;
  notify: string;
  inboxPath: string;
  decision: string;
}

export function intakeEndpointFromChat(chatEndpoint: string): string {
  return chatEndpoint.replace(/\/api\/obsidian\/chat\/?$/, "/api/obsidian/intake");
}

export function prepareEndpointFromChat(chatEndpoint: string): string {
  return chatEndpoint.replace(/\/api\/obsidian\/chat\/?$/, "/api/obsidian/prepare");
}

export function modelsEndpointFromChat(chatEndpoint: string, refresh = false): string {
  return chatEndpoint.replace(
    /\/api\/obsidian\/chat\/?$/,
    refresh ? "/api/obsidian/models/refresh" : "/api/obsidian/models",
  );
}

/** The "claude-local" provider runs on the Mac-local gateway, not the (orangepi)
 * main gateway. Route only that provider's chat there; every other provider keeps
 * using chatEndpoint, so the main gateway's feature set (other providers, prepare,
 * memory, context) is untouched and free of regression. */
export function resolveChatEndpoint(settings: RtimeAssistantSettings): string {
  const local = settings.localClaudeEndpoint?.trim();
  if (settings.modelProviderId === "claude-local" && local) {
    return local;
  }
  return settings.chatEndpoint;
}

/** Fetch + validate one gateway's model catalog from its chat endpoint. */
async function fetchCatalogAt(
  settings: RtimeAssistantSettings,
  baseChatEndpoint: string,
  refresh: boolean,
): Promise<AssistantModelCatalog> {
  const url = modelsEndpointFromChat(baseChatEndpoint, refresh);
  if (url === baseChatEndpoint) {
    throw new Error("endpoint does not end with /api/obsidian/chat; cannot derive models endpoint");
  }
  const response = await requestWithRetry({
    method: refresh ? "POST" : "GET",
    url: normalizeLocalhostUrl(url),
    headers: { Accept: "application/json" },
    timeoutMs: Math.min(settings.requestTimeoutMs, 30000),
    retryCount: Math.min(settings.requestRetryCount, 1),
    retryDelayMs: settings.requestRetryDelayMs,
  });
  const data = response.json ?? parseJsonOrText(response.text);
  if (response.status >= 400 || !isModelCatalog(data)) {
    throw new Error(`Models endpoint returned HTTP ${response.status}`);
  }
  return data;
}

/** Fetch the model catalog, merging the Mac-local gateway's claude-local provider
 * into the main (orangepi) catalog. Both gateways are fetched independently and
 * tolerantly: claude-local stays available even if the main gateway blips, and the
 * main catalog is unaffected when the local gateway is down. Only when BOTH fail is
 * an error surfaced. local-only providers are prepended so claude-local is easy to
 * find; shared ids (e.g. kimi-code-wrapper) keep the main gateway's definition,
 * matching where their requests are actually routed. */
export async function fetchModelCatalog(
  settings: RtimeAssistantSettings,
  refresh = false,
): Promise<AssistantModelCatalog> {
  const local = settings.localClaudeEndpoint?.trim();
  const localIsSeparate =
    !!local && normalizeLocalhostUrl(local) !== normalizeLocalhostUrl(settings.chatEndpoint);
  const [mainResult, localResult] = await Promise.allSettled([
    fetchCatalogAt(settings, settings.chatEndpoint, refresh),
    localIsSeparate ? fetchCatalogAt(settings, local as string, refresh) : Promise.reject(new Error("skip")),
  ]);
  const main = mainResult.status === "fulfilled" ? mainResult.value : null;
  const localCatalog = localResult.status === "fulfilled" ? localResult.value : null;

  if (main && localCatalog) {
    const seen = new Set(main.providers.map((provider) => provider.id));
    const localOnly = localCatalog.providers.filter((provider) => !seen.has(provider.id));
    return localOnly.length ? { ...main, providers: [...localOnly, ...main.providers] } : main;
  }
  if (main) return main;
  if (localCatalog) return localCatalog;
  // Both failed: surface the primary (main) endpoint's error.
  if (mainResult.status === "rejected") {
    throw mainResult.reason instanceof Error ? mainResult.reason : new Error(String(mainResult.reason));
  }
  throw new Error("Models endpoint unavailable");
}

function isModelCatalog(value: unknown): value is AssistantModelCatalog {
  return isRecord(value) && value.schema_version === 1 && Array.isArray(value.providers);
}

function isPrepareResult(value: unknown): value is AssistantPrepareResult {
  return isRecord(value) && value.ok === true && typeof value.prepare_id === "string";
}

/** Upload one attachment's bytes to the gateway inbox (entry-adapter contract:
 * the gateway writes brain/_inbox + an intake ticket, never final folders). */
export async function postAttachmentIntake(
  settings: RtimeAssistantSettings,
  payload: { name: string; content_base64: string; privacy_hint?: string; target_hint?: string },
): Promise<AttachmentIntakeResult> {
  const url = intakeEndpointFromChat(settings.chatEndpoint);
  if (url === settings.chatEndpoint) {
    throw new Error("chatEndpoint does not end with /api/obsidian/chat; cannot derive intake endpoint");
  }
  const response = await requestWithRetry({
    method: "POST",
    url,
    contentType: "application/json",
    headers: { Accept: "application/json" },
    body: JSON.stringify({ schema_version: 1, source: "obsidian", ...payload }),
    timeoutMs: settings.requestTimeoutMs,
    retryCount: 0, // uploads are not idempotent-cheap; surface errors instead of re-posting
    retryDelayMs: settings.requestRetryDelayMs,
  });
  const data = response.json ?? parseJsonOrText(response.text);
  const record = isRecord(data) ? data : {};
  if (response.status >= 400 || record.ok !== true) {
    throw new Error(asString(record.error) ?? `Intake endpoint returned HTTP ${response.status}`);
  }
  const ticket = isRecord(record.ticket) ? record.ticket : {};
  return {
    needsConfirm: record.needs_confirm === true,
    notify: asString(record.notify) ?? "",
    inboxPath: asString(ticket.inbox_path) ?? "",
    decision: asString(ticket.decision) ?? "",
  };
}
