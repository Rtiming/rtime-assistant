// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
export type ContextMode = "current-note" | "selection" | "vault";
export type TaskMode = "ask" | "summarize" | "explain" | "related" | "citation-review";
export type TargetModule = "auto" | "brain" | "literature" | "project" | "runtime";
export type SubmitBehavior = "enter-send" | "mod-enter-send";
export type Role = "user" | "assistant" | "system";
export type UiLanguage = "zh-CN" | "en";
export type ConversationTitleStatus = "provisional" | "generated" | "manual";
export type ModelProtocol = "claude-wrapper/agent-tools" | "anthropic-compatible" | "openai-chat";
export type PermissionMode = "dontAsk" | "default" | "acceptEdits" | "plan" | "bypassPermissions";
export type ThinkingEffort = "" | "low" | "medium" | "high" | "xhigh" | "max";
export type AttachmentKind = "image" | "pdf" | "markdown" | "text" | "office" | "spreadsheet" | "csv" | "archive" | "unknown";
export type AttachmentSource = "picker" | "paste" | "drop" | "vault" | "brain" | "external";
export type AttachmentIntakeMode = "session" | "inbox_candidate";

export interface RtimeAssistantSettings {
  uiLanguage: UiLanguage;
  chatEndpoint: string;
  localClaudeEndpoint: string;
  healthEndpoint: string;
  contextMode: ContextMode;
  defaultTaskMode: TaskMode;
  targetModule: TargetModule;
  targetFolder: string;
  includeSelection: boolean;
  includeActiveNoteBody: boolean;
  maxNoteChars: number;
  requestTimeoutMs: number;
  maxToolTurns: number;
  requestRetryCount: number;
  requestRetryDelayMs: number;
  healthCacheMs: number;
  streamEnabled: boolean;
  prepareEnabled: boolean;
  modelPrewarmEnabled: boolean;
  prepareDebounceMs: number;
  submitBehavior: SubmitBehavior;
  clearComposerAfterSubmit: boolean;
  focusComposerAfterSubmit: boolean;
  autoScrollResponses: boolean;
  selftestWatchEnabled: boolean;
  /** Non-secret model preference. Empty fields mean "use gateway default". */
  modelProviderId: string;
  modelId: string;
  modelProtocol: string;
  modelCatalog: AssistantModelCatalog | null;
  /** Non-secret Claude Code permission preference for tool-capable routes. */
  permissionMode: PermissionMode;
  /** Ask the gateway to forward runner approval/permission request events when available. */
  approvalForwardingEnabled: boolean;
  /** Claude effort/thinking depth for the local Claude Code route. "" = backend default. */
  modelThinkingEffort: ThinkingEffort;
  /** When true, the local Claude Code route may use Write/Edit/Bash (default: read-only). */
  modelEditMode: boolean;
  /** Absolute Mac folder the local Claude Code route works in (cwd + readable). "" = none. */
  localClaudeWorkspace: string;
  /** When true, the local workspace is read-only; otherwise the local route may write/run there. */
  localClaudeWorkspaceReadOnly: boolean;
  /** Private release manifest URL for updating this plugin inside the current vault. */
  pluginUpdateUrl: string;
  /** Last plugin release version installed through the private updater. */
  pluginLastInstalledVersion: string;
  /** Last plugin build id installed through the private updater. */
  pluginLastInstalledBuildId: string;
  /** Last time the private release manifest was checked. */
  pluginLastCheckedAt: string;
  /** Last available private release version reported by release.json. */
  pluginLastAvailableVersion: string;
  /** Last available private release build id reported by release.json. */
  pluginLastAvailableBuildId: string;
  /** Last available private release generated_at reported by release.json. */
  pluginLastAvailableGeneratedAt: string;
  /** Last private updater status or error summary. */
  pluginLastUpdateStatus: string;
  /** Conversation restored when the sidebar reopens (not shown in settings UI). */
  lastConversationId: string;
}

// KEEP IN SYNC: packages/rtime-models — rtime_models.CAPABILITY_KEYS. These are the
// model capability fields the gateway catalog projects from the registry; the keys
// here must match that list (drift-checked by scripts/check-entrypoint-drift.py).
export interface AssistantModelCapabilities {
  agent_tools?: boolean;
  code?: boolean;
  chat?: boolean;
  vision?: boolean;
  file_extract?: boolean;
  long_context?: number | null;
  thinking?: string;
}

export interface AssistantModelInfo {
  id: string;
  label?: string;
  protocol: ModelProtocol | string;
  capabilities?: AssistantModelCapabilities;
}

export interface AssistantModelProvider {
  id: string;
  label?: string;
  protocol: ModelProtocol | string;
  base_url_label?: string;
  models: AssistantModelInfo[];
}

export interface AssistantModelCatalog {
  schema_version: 1;
  generated_at?: string;
  last_refreshed?: string;
  providers: AssistantModelProvider[];
  errors?: Array<{ provider?: string; type?: string; message?: string }>;
}

export interface ChatMessage {
  role: Role;
  content: string;
  attachments?: AssistantAttachment[];
  sources?: AssistantSource[];
  trace?: StreamTrace;
  memoryEvents?: MemoryEvents;
  isError?: boolean;
}

/** One persisted sidebar conversation (stored in the plugin dir, not the vault). */
export interface Conversation {
  id: string;
  title: string;
  title_status?: ConversationTitleStatus;
  created: number;
  updated: number;
  messages: ChatMessage[];
}

/** Prior-turn excerpt sent to the gateway for follow-up reference resolution. */
export interface HistoryItem {
  role: "user" | "assistant";
  content: string;
}

export interface AssistantSource {
  title?: string;
  path?: string;
  line?: number;
  page?: number;
  url?: string;
  kind?: string;
  snippet?: string;
}

export interface AssistantAttachment {
  id: string;
  name: string;
  kind: AttachmentKind;
  mime: string;
  size: number;
  source: AttachmentSource;
  intake_mode: AttachmentIntakeMode;
  temporary: boolean;
  path?: string;
  extracted_text?: string;
  extracted_chars?: number;
  content_base64?: string;
  content_encoding?: "base64";
  content_media_type?: string;
  preview_data_url?: string;
  status: "ready" | "error";
  error?: string;
  /** set after a successful upload to the gateway inbox endpoint */
  intake_status?: "inbox" | "confirm_pending";
}

export interface PendingSelection {
  text: string;
  chars: number;
  file_path: string;
  file_name: string;
  pdf_page: number | null;
  line: number | null;
  column: number | null;
  updated_at: string;
}

export interface AssistantRuntimeContext {
  last_error?: {
    code?: string;
    message: string;
  };
}

export interface MemoryEvents {
  referenced_count: number;
  candidate_count: number;
  auto_merged_count: number;
  review_count: number;
  disabled?: boolean;
  commands?: Array<"remember" | "do_not_remember" | "forget" | "open_review">;
  summary?: string;
}

export interface StreamTrace {
  request_started_ms?: number;
  first_chunk_received_ms?: number;
  first_delta_parsed_ms?: number;
  first_dom_painted_ms?: number;
  done_received_ms?: number;
  gateway?: Record<string, number | string | null>;
}

export interface ActiveFilePayload {
  path: string;
  basename: string;
  extension: string;
  size: number;
  ctime: number;
  mtime: number;
}

export interface AssistantContext {
  vault: {
    name: string;
  };
  active_file: ActiveFilePayload | null;
  selection: {
    text: string;
    chars: number;
  } | null;
  note: {
    text: string;
    chars: number;
    truncated: boolean;
  } | null;
  pdf?: {
    page: number | null;
  };
  attachments?: AssistantAttachment[];
  pending_selection?: PendingSelection | null;
  memory?: MemoryEvents;
  runtime?: AssistantRuntimeContext;
  metadata: {
    headings: Array<{ heading: string; level: number; line: number | null }>;
    tags: Array<{ tag: string; line: number | null }>;
    links: Array<{ link: string; line: number | null }>;
  };
  requested_mode: ContextMode;
  local_time: string;
  /** Recent turns of the active conversation; the gateway folds them into the prompt. */
  history?: HistoryItem[];
}

export interface AssistantRequestBody {
  schema_version: 1;
  entry: "obsidian";
  message: string;
  /** Optional session marker; the gateway logs it and M9 groups materials by it. */
  conversation_id?: string;
  /** Optional short-lived gateway prepare cache id for the active context. */
  prepare_id?: string;
  context: AssistantContext;
  options: {
    context_mode: ContextMode;
    task_mode: TaskMode;
    template_id: TaskMode;
    target_module: TargetModule;
    target_folder: string;
    ui_language: UiLanguage;
    include_selection: boolean;
    include_active_note_body: boolean;
    trace_stream?: boolean;
    prewarm_model?: boolean;
    model_provider_id?: string;
    model_id?: string;
    model_protocol?: string;
    permission_mode?: PermissionMode;
    approval_forwarding?: boolean;
    /** Thinking depth for the local Claude Code route: low|medium|high|xhigh|max. */
    thinking_effort?: string;
    /** Allow the local Claude Code route to use write/edit/bash tools. */
    edit_mode?: boolean;
    /** Absolute Mac folder the local Claude Code route should work in (cwd + readable). */
    local_workspace?: string;
    /** When true, the local workspace is read-only; otherwise writable (normal Claude). */
    local_workspace_read_only?: boolean;
  };
  stream?: boolean;
}

export interface AssistantPrepareResult {
  ok: boolean;
  schema_version?: number;
  prepare_id: string;
  cache_ttl_seconds?: number;
  dur_ms?: number;
  unlock_count?: number;
  related_count?: number;
  memory_referenced_count?: number;
  model_catalog_cached?: boolean;
  model_provider_count?: number;
  prewarm_status?: string;
  prewarm_reason?: string;
  prewarm_model_provider_id?: string;
  prewarm_model_id?: string;
  prewarm_model_protocol?: string;
  unlocks?: Array<{ label?: string; path?: string }>;
}

export interface AssistantResult {
  answer: string;
  sources: AssistantSource[];
  trace?: StreamTrace;
  memoryEvents?: MemoryEvents;
}
