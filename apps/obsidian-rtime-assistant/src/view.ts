// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { Component, ItemView, MarkdownRenderer, Notice, setIcon, WorkspaceLeaf } from "obsidian";
import { attachmentFromFile, attachmentImageUrl, attachmentLabel, displayAttachmentSnapshot } from "./attachments";
import {
  folderSuggestionsFromPaths,
  getComposerTemplates,
  getPromptKeyForTemplate,
  getTargetModuleOptions,
} from "./composer-contract";
import { VIEW_TYPE_RTIME_ASSISTANT } from "./constants";
import { buildHistoryPayload, generatedConversationTitle } from "./conversations";
import { contextModeLabel, roleLabel, tr, type TranslationKey } from "./i18n";
import { shouldSubmitFromComposerEvent } from "./keyboard";
import type RtimeAssistantPlugin from "./main";
import type { AskOptions } from "./main";
import { isCancelledError, runAsk, type AskRunnerHost } from "./services/ask-runner";
import { catalogForDisplay, findProvider } from "./settings";
import type { AssistantAttachment, AssistantRuntimeContext, AssistantSource, ChatMessage, ContextMode, MemoryEvents, TargetModule, TaskMode, ThinkingEffort, UiLanguage } from "./types";
import { createActionButton } from "./ui/elements";
import { formatSource, iconForSource } from "./ui/format";
import { normalizeMathDelimiters, stripTrailingSourcesBlock } from "./ui/markdown";

interface LiveMessageElements {
  contentEl: HTMLElement;
  activityEl: HTMLElement | null;
  activityTextEl: HTMLElement | null;
}

function compactActivityText(text: string): string {
  const normalized = text.replace(/\s+/g, " ").replace(/…+/g, "…").trim();
  if (!normalized) {
    return "正在处理…";
  }
  return normalized.length > 48 ? `${normalized.slice(0, 47)}…` : normalized;
}

function inlineCodeText(text: string): string {
  return text.replace(/`/g, "'");
}

function formatBackendErrorMessage(message: string, lang: UiLanguage): string {
  const code = message.toLowerCase();
  if (message.includes(tr(lang, "message.maxTurnsError")) || message.includes(tr(lang, "message.incompleteError"))) {
    return message;
  }
  if (code.includes("error_max_turns")) {
    return `${tr(lang, "message.maxTurnsError")}\n\n\`${inlineCodeText(message)}\`\n\n${tr(lang, "message.maxTurnsHint")}`;
  }
  if (code.includes("incomplete_answer") || message.includes("工具调用后没有返回最终回答")) {
    return `${tr(lang, "message.incompleteError")}\n\n\`${inlineCodeText(message)}\`\n\n${tr(lang, "message.incompleteHint")}`;
  }
  return `${tr(lang, "message.backendError")}\n\n\`${inlineCodeText(message)}\`\n\n${tr(lang, "message.backendHint")}`;
}

/** Markdown shown for a message: backend errors become a friendly card, assistant
 * answers get their redundant trailing "来源：" block stripped (the collapsible
 * sources UI replaces it). Done content-driven so the live stream and the final
 * render agree — the block never flashes in then disappears on done. */
function displayMessageContent(message: ChatMessage, lang: UiLanguage): string {
  if (message.isError) {
    return formatBackendErrorMessage(message.content, lang);
  }
  if (message.role === "assistant") {
    return stripTrailingSourcesBlock(message.content);
  }
  return message.content;
}

function errorCodeFromMessage(message: string): string | undefined {
  if (message.includes("error_max_turns")) {
    return "error_max_turns";
  }
  if (message.includes("incomplete_answer") || message.includes("工具调用后没有返回最终回答")) {
    return "incomplete_answer";
  }
  if (message.includes("HTTP 503") || message.includes("队列")) {
    return "busy_or_queue";
  }
  return undefined;
}

function asksAboutRuntimeError(prompt: string): boolean {
  return /报错|错误|为什么.*错|怎么回事|后端|endpoint|error|max_turns|incomplete_answer/i.test(prompt);
}

export class RtimeAssistantView extends ItemView {
  private plugin: RtimeAssistantPlugin;
  private messages: ChatMessage[] = [];
  private promptEl: HTMLTextAreaElement | null = null;
  private statusEl: HTMLElement | null = null;
  private statusTextEl: HTMLElement | null = null;
  private contextIconEl: HTMLElement | null = null;
  private contextFileEl: HTMLElement | null = null;
  private selectionStateEl: HTMLElement | null = null;
  private selectionPreviewEl: HTMLElement | null = null;
  private modeSelectEl: HTMLSelectElement | null = null;
  private sessionSelectEl: HTMLSelectElement | null = null;
  private attachmentInputEl: HTMLInputElement | null = null;
  private attachmentListEl: HTMLElement | null = null;
  private memoryStatusEl: HTMLElement | null = null;
  private taskEl: HTMLElement | null = null;
  private historyEl: HTMLElement | null = null;
  private markdownRenderChildren: Component[] = [];
  private liveRenderChild: Component | null = null;
  private activeTaskMode: TaskMode;
  private lastTemplatePrompt = "";
  private isSubmitting = false;
  private stopRequested = false;
  private askButtonEl: HTMLButtonElement | null = null;
  private trashButtonEl: HTMLButtonElement | null = null;
  private liveRenderTimer: number | null = null;
  private pendingAttachments: AssistantAttachment[] = [];
  private memoryDisabledForNext = false;
  private memoryCommands: MemoryEvents["commands"] = [];
  private lastMemoryEvents: MemoryEvents = {
    referenced_count: 0,
    candidate_count: 0,
    auto_merged_count: 0,
    review_count: 0,
  };
  /** Bumped on switch/new/delete/close; a stale submit sees the mismatch and stands down. */
  private submitGeneration = 0;
  /** Unique-id source for collapsible source lists (aria-controls). */
  private sourcesListSeq = 0;

  constructor(leaf: WorkspaceLeaf, plugin: RtimeAssistantPlugin) {
    super(leaf);
    this.plugin = plugin;
    this.activeTaskMode = plugin.settings.defaultTaskMode;
  }

  getViewType(): string {
    return VIEW_TYPE_RTIME_ASSISTANT;
  }

  getDisplayText(): string {
    return tr(this.plugin.settings.uiLanguage, "app.title");
  }

  getIcon(): string {
    return "bot";
  }

  async onOpen(): Promise<void> {
    this.bindActiveConversation();
    this.render();
    this.plugin.schedulePrepare("view-open");
    void this.probeHealth();
  }

  /** Cheap, non-blocking connectivity check so an offline gateway shows up in
   * the status pill immediately instead of after a full request timeout. Uses
   * the client's short health cache; never runs on a timer. */
  private async probeHealth(): Promise<void> {
    if (this.isSubmitting) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    try {
      await this.plugin.checkHealth();
      if (!this.isSubmitting) {
        this.setStatus(tr(lang, "status.ready"), "ready");
      }
    } catch {
      if (!this.isSubmitting) {
        this.setStatus(tr(lang, "status.offline"), "error");
      }
    }
  }

  async onClose(): Promise<void> {
    this.submitGeneration += 1;
    this.plugin.cancelActiveRequest();
    void this.plugin.conversations.flush();
    this.cancelLiveRender();
    this.clearMarkdownRenderChildren();
  }

  /** Point the view at the store's active conversation (restored across reopen/restart). */
  private bindActiveConversation(): void {
    const store = this.plugin.conversations;
    const conversation = store.getActive() ?? store.startNew();
    this.messages = conversation.messages;
  }

  rerender(): void {
    this.render();
  }

  setPrompt(prompt: string, taskMode?: TaskMode): void {
    if (taskMode) {
      this.activeTaskMode = taskMode;
      this.renderTaskModes();
    }
    if (this.promptEl) {
      this.promptEl.value = prompt;
      this.lastTemplatePrompt = prompt;
      this.autoGrowComposer();
      this.promptEl.focus();
    }
  }

  refreshContextSummary(): void {
    if (!this.contextFileEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    const location = this.plugin.describeContextLocation();
    if (this.contextIconEl) {
      setIcon(this.contextIconEl, location.hasFile ? "file-text" : "file");
    }
    this.contextFileEl.setText(location.label ?? tr(lang, "context.noFile"));
    this.contextFileEl.setAttribute("title", location.title);
    this.refreshSelectionSummary();
    if (this.modeSelectEl) {
      this.modeSelectEl.value = this.plugin.settings.contextMode;
    }
  }

  private render(): void {
    const lang = this.plugin.settings.uiLanguage;
    const container = this.contentEl;
    this.cancelLiveRender();
    container.empty();

    const root = container.createDiv({ cls: "rtime-assistant-root" });

    // Header: title + status + icon-only utilities.
    const header = root.createDiv({ cls: "rtime-assistant-header" });
    const brand = header.createDiv({ cls: "rtime-assistant-brand" });
    const brandIcon = brand.createSpan({ cls: "rtime-assistant-brand-icon" });
    setIcon(brandIcon, "bot");
    brand.createSpan({ cls: "rtime-assistant-title", text: tr(lang, "app.title") });
    this.statusEl = header.createDiv({ cls: "rtime-assistant-status is-idle" });
    this.statusEl.setAttribute("title", this.plugin.settings.chatEndpoint);
    this.statusEl.setAttribute("role", "status");
    this.statusEl.setAttribute("aria-live", "polite");
    this.statusEl.createSpan({ cls: "rtime-assistant-status-dot" });
    this.statusTextEl = this.statusEl.createSpan({ cls: "rtime-assistant-status-text" });
    this.setStatus(tr(lang, "status.idle"), "idle");

    const headerActions = header.createDiv({ cls: "rtime-assistant-header-actions" });
    this.sessionSelectEl = headerActions.createEl("select", {
      cls: "rtime-assistant-select rtime-assistant-session-select",
    });
    this.sessionSelectEl.setAttribute("aria-label", tr(lang, "chat.sessionLabel"));
    this.sessionSelectEl.setAttribute("title", tr(lang, "chat.sessionLabel"));
    this.sessionSelectEl.addEventListener("change", () => {
      const id = this.sessionSelectEl?.value;
      if (id) {
        this.switchConversation(id);
      }
    });
    this.renderSessionOptions();
    this.createIconButton(headerActions, "plus", tr(lang, "button.newChat"), () => {
      this.startNewConversation();
    });
    this.trashButtonEl = this.createIconButton(headerActions, "trash-2", tr(lang, "button.deleteChat"), () => {
      this.deleteConversation();
    });
    this.createIconButton(headerActions, "activity", tr(lang, "button.health"), () => {
      void this.runHealthCheck();
    });
    this.createIconButton(headerActions, "settings", tr(lang, "button.settings"), () => {
      this.plugin.openSettings();
    });

    // History.
    this.historyEl = root.createDiv({ cls: "rtime-assistant-history" });
    this.renderMessages();

    // Composer.
    const composer = root.createDiv({ cls: "rtime-assistant-composer" });

    const contextBar = composer.createDiv({ cls: "rtime-assistant-contextbar" });
    const fileWrap = contextBar.createDiv({ cls: "rtime-assistant-context-file" });
    this.contextIconEl = fileWrap.createSpan({ cls: "rtime-assistant-context-icon" });
    this.contextFileEl = fileWrap.createSpan({ cls: "rtime-assistant-context-name" });
    this.selectionStateEl = contextBar.createSpan({ cls: "rtime-assistant-selection-chip" });
    const clearSelection = this.createIconButton(contextBar, "x", tr(lang, "button.clearSelection"), () => {
      this.plugin.clearPendingSelection();
    });
    clearSelection.addClass("rtime-assistant-selection-clear");
    this.modeSelectEl = contextBar.createEl("select", {
      cls: "rtime-assistant-select rtime-assistant-mode-select",
    });
    for (const mode of ["current-note", "selection", "vault"] as ContextMode[]) {
      this.modeSelectEl.createEl("option", { value: mode, text: contextModeLabel(lang, mode) });
    }
    this.modeSelectEl.addEventListener("change", () => {
      const value = this.modeSelectEl?.value as ContextMode | undefined;
      if (!value) {
        return;
      }
      this.plugin.settings.contextMode = value;
      void this.plugin.saveSettings();
    });
    this.refreshContextSummary();

    this.selectionPreviewEl = composer.createDiv({ cls: "rtime-assistant-selection-preview" });

    this.taskEl = composer.createDiv({ cls: "rtime-assistant-taskbar" });
    this.renderTaskModes();

    this.renderModelBar(composer);

    this.renderAdvancedControls(composer);

    this.promptEl = composer.createEl("textarea", { cls: "rtime-assistant-textarea" });
    this.promptEl.placeholder = tr(lang, "composer.placeholder");
    this.promptEl.addEventListener("paste", (event) => {
      const files = Array.from(event.clipboardData?.files ?? []);
      if (files.length) {
        void this.addAttachmentFiles(files, "paste");
      }
    });
    this.promptEl.addEventListener("input", () => this.autoGrowComposer());
    this.promptEl.addEventListener("keydown", (event) => {
      if (event.key === "Escape" && this.isSubmitting) {
        event.preventDefault();
        event.stopPropagation();
        this.stopActive();
        return;
      }
      if (this.shouldSubmitFromEnter(event)) {
        event.preventDefault();
        void this.submitPrompt();
      }
    });
    // Warm context/model the moment the user intends to type, not only on the
    // 1.5s poll — shaves the cold-prepare gap off the first send.
    this.promptEl.addEventListener("focus", () => {
      this.plugin.schedulePrepare("composer-focus");
    });

    this.renderAttachmentControls(composer);

    const actions = composer.createDiv({ cls: "rtime-assistant-actions" });
    const askButton = createActionButton(actions, "send", tr(lang, "button.ask"));
    askButton.addClass("rtime-assistant-send");
    this.askButtonEl = askButton;
    askButton.addEventListener("click", () => {
      if (this.isSubmitting) {
        this.stopActive();
      } else {
        void this.submitPrompt();
      }
    });
    this.setComposerBusy(this.isSubmitting);
  }

  /** Toggle the composer's primary button between Send and Stop, and reflect the
   * in-flight state so repeat clicks cancel instead of silently no-opping. */
  private setComposerBusy(busy: boolean): void {
    const button = this.askButtonEl;
    if (!button) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    button.toggleClass("is-stop", busy);
    button.setAttribute("aria-busy", busy ? "true" : "false");
    const label = busy ? tr(lang, "button.stop") : tr(lang, "button.ask");
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    const iconEl = button.querySelector<HTMLElement>(".rtime-assistant-button-icon");
    if (iconEl) {
      setIcon(iconEl, busy ? "square" : "send");
    }
    const labelEl = button.querySelector<HTMLElement>(".rtime-assistant-button-label");
    if (labelEl) {
      labelEl.setText(label);
    }
  }

  /** User-initiated cancel: abort the socket (frees the gateway slot) and let the
   * submit loop finalize whatever partial answer already streamed in. */
  private stopActive(): void {
    if (!this.isSubmitting) {
      return;
    }
    this.stopRequested = true;
    this.plugin.cancelActiveRequest();
    this.setStatus(tr(this.plugin.settings.uiLanguage, "status.stopped"), "idle");
  }

  /** The trash button is only meaningful when the active conversation has
   * content; disable it otherwise so the click isn't a silent no-op. */
  private refreshTrashState(): void {
    const button = this.trashButtonEl;
    if (!button) {
      return;
    }
    const active = this.plugin.conversations.getActive();
    const hasMessages = (active?.messages.length ?? 0) > 0;
    button.toggleAttribute("disabled", !hasMessages);
  }

  private refreshSelectionSummary(): void {
    if (!this.selectionStateEl || !this.selectionPreviewEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    const pending = this.plugin.getPendingSelection();
    if (!pending) {
      this.selectionStateEl.setText(tr(lang, "context.noSelection"));
      this.selectionStateEl.setAttribute("title", tr(lang, "context.noSelectionTitle"));
      this.selectionPreviewEl.setText("");
      this.selectionPreviewEl.hide();
      return;
    }
    const where = pending.pdf_page
      ? `${pending.file_name} p.${pending.pdf_page}`
      : pending.line
        ? `${pending.file_name} L${pending.line}:C${pending.column ?? 1}`
        : pending.file_name;
    this.selectionStateEl.setText(`${tr(lang, "context.lockedSelection")} ${pending.chars}${tr(lang, "unit.chars")}`);
    this.selectionStateEl.setAttribute("title", `${where}\n${pending.file_path}`);
    this.selectionPreviewEl.setText(`${where}: ${pending.text.slice(0, 100)}`);
    this.selectionPreviewEl.show();
  }

  private createIconButton(
    parent: HTMLElement,
    icon: string,
    label: string,
    onClick: () => void,
  ): HTMLButtonElement {
    const button = parent.createEl("button", { cls: "rtime-assistant-iconbtn" });
    setIcon(button, icon);
    button.setAttribute("aria-label", label);
    button.setAttribute("title", label);
    button.addEventListener("click", onClick);
    return button;
  }

  private shouldSubmitFromEnter(event: KeyboardEvent): boolean {
    return shouldSubmitFromComposerEvent(this.plugin.settings, event);
  }

  private renderTaskModes(): void {
    if (!this.taskEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    this.taskEl.empty();
    for (const item of getComposerTemplates()) {
      const button = this.taskEl.createEl("button", { text: tr(lang, item.labelKey) });
      button.toggleClass("is-active", this.activeTaskMode === item.taskMode);
      button.addEventListener("click", () => {
        this.applyTaskTemplate(item.taskMode);
      });
    }
  }

  /** Always-visible row to pick the source, its model, and thinking depth without
   * opening the "高级" section or the settings tab. */
  private renderModelBar(composer: HTMLElement): void {
    const lang = this.plugin.settings.uiLanguage;
    const bar = composer.createDiv({ cls: "rtime-assistant-modelbar" });
    const catalog = catalogForDisplay(this.plugin.settings.modelCatalog, this.plugin.settings);

    const providerField = bar.createEl("label", { cls: "rtime-assistant-target-field" });
    providerField.createSpan({ text: tr(lang, "composer.providerLabel") });
    const providerSelect = providerField.createEl("select", { cls: "rtime-assistant-select" });
    providerSelect.createEl("option", { value: "", text: tr(lang, "settings.model.gatewayDefault") });
    for (const provider of catalog?.providers ?? []) {
      providerSelect.createEl("option", { value: provider.id, text: provider.label ?? provider.id });
    }
    providerSelect.value = this.plugin.settings.modelProviderId;

    const modelField = bar.createEl("label", { cls: "rtime-assistant-target-field" });
    modelField.createSpan({ text: tr(lang, "composer.modelLabel") });
    const modelSelect = modelField.createEl("select", { cls: "rtime-assistant-select" });
    const fillModelOptions = (): void => {
      modelSelect.empty();
      modelSelect.createEl("option", { value: "", text: tr(lang, "settings.model.gatewayDefault") });
      const provider = findProvider(catalog, this.plugin.settings.modelProviderId);
      for (const model of provider?.models ?? []) {
        modelSelect.createEl("option", { value: model.id, text: model.label ?? model.id });
      }
      modelSelect.value = this.plugin.settings.modelId;
    };
    fillModelOptions();
    modelSelect.addEventListener("change", () => {
      this.plugin.settings.modelId = modelSelect.value;
      void this.plugin.saveSettings();
    });

    providerSelect.addEventListener("change", () => {
      const provider = findProvider(catalog, providerSelect.value);
      this.plugin.settings.modelProviderId = providerSelect.value;
      this.plugin.settings.modelId = "";
      this.plugin.settings.modelProtocol = provider?.protocol ?? "";
      void this.plugin.saveSettings();
      fillModelOptions(); // refresh the model list for the newly chosen source
    });

    const effortField = bar.createEl("label", { cls: "rtime-assistant-target-field" });
    effortField.createSpan({ text: tr(lang, "composer.effortLabel") });
    const effortSelect = effortField.createEl("select", { cls: "rtime-assistant-select" });
    const effortOptions: Array<{ value: ThinkingEffort; key: TranslationKey }> = [
      { value: "", key: "settings.thinking.effort.default" },
      { value: "low", key: "settings.thinking.effort.low" },
      { value: "medium", key: "settings.thinking.effort.medium" },
      { value: "high", key: "settings.thinking.effort.high" },
      { value: "xhigh", key: "settings.thinking.effort.xhigh" },
      { value: "max", key: "settings.thinking.effort.max" },
    ];
    for (const option of effortOptions) {
      effortSelect.createEl("option", { value: option.value, text: tr(lang, option.key) });
    }
    effortSelect.value = this.plugin.settings.modelThinkingEffort;
    effortSelect.addEventListener("change", () => {
      this.plugin.settings.modelThinkingEffort = effortSelect.value as ThinkingEffort;
      void this.plugin.saveSettings();
    });
  }

  private renderAdvancedControls(composer: HTMLElement): void {
    const lang = this.plugin.settings.uiLanguage;
    const advanced = composer.createEl("details", { cls: "rtime-assistant-advanced" });
    advanced.createEl("summary", { text: tr(lang, "button.advanced") });
    const body = advanced.createDiv({ cls: "rtime-assistant-advanced-body" });

    const moduleField = body.createEl("label", { cls: "rtime-assistant-target-field" });
    moduleField.createSpan({ text: tr(lang, "composer.moduleLabel") });
    const moduleSelect = moduleField.createEl("select", { cls: "rtime-assistant-select" });
    for (const module of getTargetModuleOptions()) {
      moduleSelect.createEl("option", { value: module.id, text: tr(lang, module.labelKey) });
    }
    moduleSelect.value = this.plugin.settings.targetModule;
    moduleSelect.addEventListener("change", () => {
      const value = moduleSelect.value as TargetModule;
      this.plugin.settings.targetModule = value;
      void this.plugin.saveSettings();
    });

    const folderField = body.createEl("label", { cls: "rtime-assistant-target-field rtime-assistant-target-field-wide" });
    folderField.createSpan({ text: tr(lang, "composer.folderLabel") });
    const datalistId = `rtime-assistant-folders-${Date.now().toString(36)}`;
    const datalist = body.createEl("datalist");
    datalist.id = datalistId;
    const activePath = this.app.workspace.getActiveFile()?.path ?? "";
    const markdownPaths = this.app.vault.getMarkdownFiles().map((file) => file.path);
    for (const folder of folderSuggestionsFromPaths(activePath, markdownPaths)) {
      datalist.createEl("option", { value: folder });
    }
    const folderInput = folderField.createEl("input", {
      cls: "rtime-assistant-folder-input",
      type: "text",
    });
    folderInput.placeholder = tr(lang, "composer.folderPlaceholder");
    folderInput.value = this.plugin.settings.targetFolder;
    folderInput.setAttribute("list", datalistId);
    folderInput.addEventListener("change", () => {
      this.plugin.settings.targetFolder = folderInput.value.trim();
      void this.plugin.saveSettings();
    });

    const memory = body.createDiv({ cls: "rtime-assistant-memory-row" });
    this.memoryStatusEl = memory.createSpan({ cls: "rtime-assistant-memory-status" });
    this.renderMemoryStatus();
    this.createSmallTextButton(memory, tr(lang, "memory.remember"), () => {
      this.memoryCommands = ["remember"];
      this.memoryDisabledForNext = false;
      this.renderMemoryStatus();
    });
    this.createSmallTextButton(memory, tr(lang, "memory.skip"), () => {
      this.memoryCommands = ["do_not_remember"];
      this.memoryDisabledForNext = true;
      this.renderMemoryStatus();
    });
    this.createSmallTextButton(memory, tr(lang, "memory.review"), () => {
      this.memoryCommands = ["open_review"];
      this.renderMemoryStatus();
    });
  }

  private createSmallTextButton(parent: HTMLElement, label: string, onClick: () => void): HTMLButtonElement {
    const button = parent.createEl("button", { cls: "rtime-assistant-smallbtn", text: label });
    button.addEventListener("click", onClick);
    return button;
  }

  private renderMemoryStatus(): void {
    if (!this.memoryStatusEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    const events = this.lastMemoryEvents;
    const disabled = this.memoryDisabledForNext || events.disabled;
    const text = disabled
      ? tr(lang, "memory.disabled")
      : `${tr(lang, "memory.status")} ${events.referenced_count}/${events.candidate_count}/${events.review_count}`;
    this.memoryStatusEl.setText(text);
    this.memoryStatusEl.setAttribute(
      "title",
      tr(lang, "memory.statusTitle"),
    );
  }

  private renderAttachmentControls(composer: HTMLElement): void {
    const lang = this.plugin.settings.uiLanguage;
    const row = composer.createDiv({ cls: "rtime-assistant-attachments-row" });
    this.attachmentInputEl = row.createEl("input", {
      attr: {
        type: "file",
        multiple: "true",
        accept: ".png,.jpg,.jpeg,.webp,.gif,.pdf,.md,.markdown,.txt,.csv,.tsv,.doc,.docx,.ppt,.pptx,.xls,.xlsx,.zip",
      },
      cls: "rtime-assistant-file-input",
    });
    this.attachmentInputEl.setAttribute("aria-label", tr(lang, "attachments.add"));
    this.attachmentInputEl.tabIndex = -1;
    this.attachmentInputEl.addEventListener("change", () => {
      const files = Array.from(this.attachmentInputEl?.files ?? []);
      if (files.length) {
        void this.addAttachmentFiles(files, "picker");
      }
      if (this.attachmentInputEl) {
        this.attachmentInputEl.value = "";
      }
    });
    const addButton = createActionButton(row, "paperclip", tr(lang, "attachments.add"));
    addButton.addClass("rtime-assistant-attach-button");
    addButton.addEventListener("click", () => this.attachmentInputEl?.click());
    this.attachmentListEl = row.createDiv({ cls: "rtime-assistant-attachment-list" });
    composer.addEventListener("dragover", (event) => {
      if (event.dataTransfer?.files.length) {
        event.preventDefault();
        row.addClass("is-dragging");
      }
    });
    composer.addEventListener("dragleave", () => row.removeClass("is-dragging"));
    composer.addEventListener("drop", (event) => {
      const files = Array.from(event.dataTransfer?.files ?? []);
      if (!files.length) {
        return;
      }
      event.preventDefault();
      row.removeClass("is-dragging");
      void this.addAttachmentFiles(files, "drop");
    });
    this.renderAttachments();
  }

  private async addAttachmentFiles(files: File[], source: "picker" | "paste" | "drop"): Promise<void> {
    const lang = this.plugin.settings.uiLanguage;
    for (const file of files.slice(0, 8)) {
      const attachment = await attachmentFromFile(file, source);
      this.pendingAttachments.push(attachment);
      if (attachment.status === "error") {
        new Notice(`${tr(lang, "attachments.unsupported")}: ${file.name}`);
      }
    }
    this.renderAttachments();
  }

  private renderAttachments(): void {
    if (!this.attachmentListEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    this.attachmentListEl.empty();
    for (const attachment of this.pendingAttachments) {
      const chip = this.attachmentListEl.createDiv({ cls: "rtime-assistant-attachment-chip" });
      chip.toggleClass("is-error", attachment.status === "error");
      const imageUrl = attachmentImageUrl(attachment);
      chip.toggleClass("has-preview", Boolean(imageUrl));
      chip.setAttribute("title", attachmentLabel(attachment));
      if (imageUrl) {
        chip.createEl("img", {
          attr: {
            src: imageUrl,
            alt: attachment.name,
          },
          cls: "rtime-assistant-attachment-preview",
        });
      }
      chip.createSpan({ cls: "rtime-assistant-attachment-name", text: attachment.name });
      chip.createSpan({
        cls: "rtime-assistant-attachment-state",
        text: tr(lang, attachment.status === "ready" ? "attachments.nextTurn" : "attachments.errorState"),
      });
      this.createIconButton(chip, "x", tr(lang, "attachments.remove"), () => {
        this.pendingAttachments = this.pendingAttachments.filter((item) => item.id !== attachment.id);
        this.renderAttachments();
      });
    }
  }

  private applyTaskTemplate(taskMode: TaskMode): void {
    this.activeTaskMode = taskMode;
    this.renderTaskModes();
    const promptKey = getPromptKeyForTemplate(taskMode);
    const nextPrompt = promptKey ? tr(this.plugin.settings.uiLanguage, promptKey) : "";
    if (!this.promptEl) {
      return;
    }
    const current = this.promptEl.value.trim();
    const canReplace = !current || current === this.lastTemplatePrompt;
    if (canReplace) {
      this.promptEl.value = nextPrompt;
      this.lastTemplatePrompt = nextPrompt;
      this.autoGrowComposer();
    }
    this.promptEl.focus();
  }

  private renderMessages(): void {
    if (!this.historyEl) {
      return;
    }
    this.cancelLiveRender();
    this.clearMarkdownRenderChildren();
    this.historyEl.empty();
    this.refreshTrashState();

    if (this.messages.length === 0) {
      this.renderEmptyState(this.historyEl);
      return;
    }

    for (const message of this.messages) {
      this.appendMessageCard(message, false);
    }
    this.scrollHistoryToBottom();
  }

  /** Empty state doubles as onboarding: clickable starters that prefill the
   * composer and surface the task templates. */
  private renderEmptyState(host: HTMLElement): void {
    const lang = this.plugin.settings.uiLanguage;
    const empty = host.createDiv({ cls: "rtime-assistant-empty" });
    const icon = empty.createSpan({ cls: "rtime-assistant-empty-icon" });
    setIcon(icon, "sparkles");
    empty.createDiv({
      cls: "rtime-assistant-empty-hint",
      text: tr(lang, "empty.hint"),
    });
    empty.createDiv({ cls: "rtime-assistant-empty-try", text: tr(lang, "empty.try") });
    const starters = empty.createDiv({ cls: "rtime-assistant-empty-starters" });
    for (const item of getComposerTemplates()) {
      const chip = starters.createEl("button", {
        cls: "rtime-assistant-empty-starter",
        text: tr(lang, item.labelKey),
      });
      chip.addEventListener("click", () => {
        this.applyTaskTemplate(item.taskMode);
      });
    }
  }

  /** Append a single message card; returns live DOM hooks when streaming. */
  private appendMessageCard(message: ChatMessage, live: false): HTMLElement;
  private appendMessageCard(message: ChatMessage, live: true): LiveMessageElements;
  private appendMessageCard(message: ChatMessage, live: boolean): HTMLElement | LiveMessageElements {
    const lang = this.plugin.settings.uiLanguage;
    const sourcePath = this.app.workspace.getActiveFile()?.path ?? "";
    const host = this.historyEl;
    const item = host
      ? host.createDiv({ cls: `rtime-assistant-message rtime-assistant-message-${message.role}` })
      : document.createElement("div");
    if (live) {
      item.addClass("is-streaming");
    }
    if (message.isError) {
      item.addClass("is-error");
    }

    item.createDiv({ cls: "rtime-assistant-message-role", text: roleLabel(lang, message.role) });
    let activityEl: HTMLElement | null = null;
    let activityTextEl: HTMLElement | null = null;
    if (live && message.role === "assistant") {
      activityEl = item.createDiv({ cls: "rtime-assistant-activity-card is-working" });
      const icon = activityEl.createSpan({ cls: "rtime-assistant-activity-icon" });
      setIcon(icon, "loader-2");
      const body = activityEl.createDiv({ cls: "rtime-assistant-activity-body" });
      body.createDiv({ cls: "rtime-assistant-activity-kicker", text: tr(lang, "activity.kicker") });
      activityTextEl = body.createDiv({ cls: "rtime-assistant-activity-text", text: tr(lang, "activity.starting") });
    }
    const contentEl = item.createDiv({ cls: "rtime-assistant-message-content markdown-rendered" });
    const displayContent = displayMessageContent(message, lang);
    if (live) {
      contentEl.setText(displayContent);
      contentEl.toggleClass("is-empty", !displayContent.trim());
    } else {
      const renderChild = this.createMarkdownRenderChild();
      void this.renderMarkdownMessage(contentEl, displayContent, sourcePath, renderChild);
    }
    if (!live && message.attachments?.length) {
      this.renderMessageAttachments(item, message.attachments);
    }

    if (!live && message.role === "assistant" && message.content.trim() && !message.isError) {
      const footer = item.createDiv({ cls: "rtime-assistant-message-actions" });
      this.createIconButton(footer, "copy", tr(lang, "button.copy"), () => {
        void this.copyMessage(message.content);
      });
      this.createIconButton(footer, "corner-down-left", tr(lang, "button.insert"), () => {
        this.plugin.insertText(message.content);
      });
    }
    if (!live && message.role === "assistant") {
      this.renderMessageMeta(item, message);
    }
    if (!live && message.sources?.length) {
      this.renderSources(item, message.sources);
    }
    return live ? { contentEl, activityEl, activityTextEl } : contentEl;
  }

  private renderMessageAttachments(item: HTMLElement, attachments: AssistantAttachment[]): void {
    const wrap = item.createDiv({ cls: "rtime-assistant-message-attachments" });
    for (const attachment of attachments.slice(0, 8)) {
      const imageUrl = attachmentImageUrl(attachment);
      if (imageUrl) {
        const figure = wrap.createDiv({ cls: "rtime-assistant-message-image" });
        figure.createEl("img", {
          attr: {
            src: imageUrl,
            alt: attachment.name,
          },
        });
        figure.createDiv({ cls: "rtime-assistant-message-attachment-caption", text: attachment.name });
        continue;
      }
      const file = wrap.createDiv({ cls: "rtime-assistant-message-file" });
      const icon = file.createSpan({ cls: "rtime-assistant-message-file-icon" });
      setIcon(icon, attachment.kind === "pdf" ? "file-text" : "paperclip");
      file.createSpan({ cls: "rtime-assistant-message-file-name", text: attachment.name });
    }
  }

  private updateActivityCard(liveEl: LiveMessageElements, text: string, tone: "working" | "error" | "done" = "working"): void {
    if (!liveEl.activityEl || !liveEl.activityTextEl) {
      return;
    }
    liveEl.activityEl.removeClass("is-working", "is-error", "is-done", "has-output");
    liveEl.activityEl.addClass(`is-${tone}`);
    liveEl.activityEl.toggleClass("has-output", !liveEl.contentEl.hasClass("is-empty"));
    liveEl.activityTextEl.setText(compactActivityText(text));
    liveEl.activityEl.setAttribute("title", text);
  }

  private renderMessageMeta(item: HTMLElement, message: ChatMessage): void {
    const parts: string[] = [];
    if (message.trace) {
      const start = message.trace.request_started_ms;
      const firstDelta = message.trace.first_delta_parsed_ms;
      const done = message.trace.done_received_ms;
      if (start && firstDelta) {
        parts.push(`first delta ${firstDelta - start}ms`);
      }
      if (start && done) {
        parts.push(`done ${done - start}ms`);
      }
    }
    if (message.memoryEvents) {
      parts.push(`memory ${message.memoryEvents.referenced_count}/${message.memoryEvents.candidate_count}/${message.memoryEvents.review_count}`);
    }
    if (!parts.length) {
      return;
    }
    item.createDiv({ cls: "rtime-assistant-message-meta", text: parts.join(" · ") });
  }

  private renderSources(item: HTMLElement, sources: AssistantSource[]): void {
    const lang = this.plugin.settings.uiLanguage;
    const wrap = item.createDiv({ cls: "rtime-assistant-sources" });
    const toggle = wrap.createEl("button", { cls: "rtime-assistant-sources-toggle" });
    const chevron = toggle.createSpan({ cls: "rtime-assistant-sources-chevron" });
    setIcon(chevron, "chevron-right");
    toggle.createSpan({ text: `${tr(lang, "sources.label")} ${sources.length}` });
    const list = wrap.createDiv({ cls: "rtime-assistant-sources-list" });
    const listId = `rtime-assistant-sources-${(this.sourcesListSeq += 1).toString(36)}`;
    list.id = listId;
    list.hide();
    toggle.setAttribute("aria-controls", listId);
    toggle.setAttribute("aria-expanded", "false");
    toggle.addEventListener("click", () => {
      const open = list.isShown();
      if (open) {
        list.hide();
      } else {
        list.show();
      }
      toggle.toggleClass("is-open", !open);
      toggle.setAttribute("aria-expanded", String(!open));
    });

    for (const source of sources) {
      const row = list.createEl("button", { cls: "rtime-assistant-source" });
      const icon = row.createSpan({ cls: "rtime-assistant-source-icon" });
      setIcon(icon, iconForSource(source));
      const name = source.title ?? source.path?.split("/").pop() ?? tr(lang, "source.fallback");
      const text = source.page !== undefined ? `${name} · p.${source.page}` : name;
      row.createSpan({ cls: "rtime-assistant-source-title", text });
      const detail = formatSource(source);
      if (detail) {
        row.setAttribute("title", detail);
      }
      row.addEventListener("click", () => {
        void this.openSource(source);
      });
    }
  }

  private async openSource(source: AssistantSource): Promise<void> {
    if (source.url) {
      window.open(source.url);
      return;
    }
    const raw = source.path;
    if (!raw) {
      return;
    }
    const basename = raw.split("/").pop() ?? raw;
    const dest = this.app.metadataCache.getFirstLinkpathDest(basename, "");
    if (dest) {
      const link = source.page !== undefined ? `${basename}#page=${source.page}` : basename;
      await this.app.workspace.openLinkText(link, "", false);
      return;
    }
    try {
      await navigator.clipboard.writeText(raw);
    } catch {
      // clipboard may be unavailable; the notice below still explains the path issue
    }
    new Notice(tr(this.plugin.settings.uiLanguage, "notice.sourceMissing"));
  }

  private async copyMessage(content: string): Promise<void> {
    const lang = this.plugin.settings.uiLanguage;
    try {
      await navigator.clipboard.writeText(content);
      new Notice(tr(lang, "notice.copied"));
    } catch {
      new Notice(tr(lang, "notice.copyFailed"));
    }
  }

  /** Refill the session dropdown (titles change after the first question). */
  private renderSessionOptions(): void {
    if (!this.sessionSelectEl) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    this.sessionSelectEl.empty();
    for (const conversation of this.plugin.conversations.list()) {
      this.sessionSelectEl.createEl("option", {
        value: conversation.id,
        text: conversation.title || tr(lang, "chat.untitled"),
      });
    }
    const activeId = this.plugin.conversations.getActiveId();
    if (activeId) {
      this.sessionSelectEl.value = activeId;
    }
  }

  private switchConversation(id: string): void {
    if (id === this.plugin.conversations.getActiveId()) {
      return;
    }
    this.submitGeneration += 1;
    this.plugin.cancelActiveRequest();
    this.isSubmitting = false;
    this.stopRequested = false;
    this.setComposerBusy(false);
    const conversation = this.plugin.conversations.setActive(id);
    if (!conversation) {
      this.renderSessionOptions();
      return;
    }
    this.messages = conversation.messages;
    void this.plugin.rememberActiveConversation();
    this.renderMessages();
    this.setStatus(tr(this.plugin.settings.uiLanguage, "status.idle"), "idle");
  }

  /** The old 清空对话 is now 新对话: the previous conversation stays in history. */
  private startNewConversation(): void {
    this.submitGeneration += 1;
    this.plugin.cancelActiveRequest();
    this.isSubmitting = false;
    this.stopRequested = false;
    this.setComposerBusy(false);
    const store = this.plugin.conversations;
    const active = store.getActive();
    const conversation = active && active.messages.length === 0 ? active : store.startNew();
    this.messages = conversation.messages;
    this.plugin.lastAnswer = "";
    void this.plugin.rememberActiveConversation();
    this.renderSessionOptions();
    this.renderMessages();
    this.setStatus(tr(this.plugin.settings.uiLanguage, "status.idle"), "idle");
  }

  private deleteConversation(): void {
    const lang = this.plugin.settings.uiLanguage;
    const active = this.plugin.conversations.getActive();
    if (!active || active.messages.length === 0) {
      this.startNewConversation();
      return;
    }
    if (!window.confirm(tr(lang, "confirm.deleteChat"))) {
      return;
    }
    this.submitGeneration += 1;
    this.plugin.cancelActiveRequest();
    this.isSubmitting = false;
    this.stopRequested = false;
    this.setComposerBusy(false);
    const next = this.plugin.conversations.deleteActive();
    this.messages = next.messages;
    void this.plugin.rememberActiveConversation();
    this.renderSessionOptions();
    this.renderMessages();
    this.setStatus(tr(lang, "status.idle"), "idle");
    new Notice(tr(lang, "notice.chatDeleted"));
  }

  private createMarkdownRenderChild(): Component {
    const child = new Component();
    this.addChild(child);
    child.load();
    this.markdownRenderChildren.push(child);
    return child;
  }

  private clearMarkdownRenderChildren(): void {
    for (const child of this.markdownRenderChildren) {
      child.unload();
    }
    this.markdownRenderChildren = [];
    if (this.liveRenderChild) {
      this.liveRenderChild.unload();
      this.liveRenderChild = null;
    }
  }

  private async renderMarkdownMessage(
    contentEl: HTMLElement,
    content: string,
    sourcePath: string,
    renderChild: Component,
  ): Promise<void> {
    try {
      await MarkdownRenderer.render(this.app, normalizeMathDelimiters(content), contentEl, sourcePath, renderChild);
    } catch (error) {
      contentEl.setText(content);
      console.error("Rtime Assistant markdown render failed", error);
    } finally {
      this.scrollHistoryToBottom();
    }
  }

  private async submitPrompt(): Promise<void> {
    if (this.isSubmitting) {
      return;
    }
    const lang = this.plugin.settings.uiLanguage;
    const prompt = this.promptEl?.value.trim() ?? "";
    if (!prompt) {
      new Notice(tr(lang, "notice.emptyPrompt"));
      return;
    }

    this.isSubmitting = true;
    this.stopRequested = false;
    this.setComposerBusy(true);
    const store = this.plugin.conversations;
    const generation = this.submitGeneration;
    // History excludes the question being asked — compute before appending.
    const readyAttachments = this.pendingAttachments.filter((attachment) => attachment.status === "ready");
    const askOptions: AskOptions = {
      conversationId: (store.getActive() ?? store.startNew()).id,
      history: buildHistoryPayload(this.messages),
      attachments: readyAttachments,
      memory: this.deriveMemoryEvents(),
      runtime: this.deriveRuntimeContext(prompt),
    };
    const displayAttachments = readyAttachments.map(displayAttachmentSnapshot);
    const conversation = store.appendMessage({
      role: "user",
      content: prompt,
      ...(displayAttachments.length ? { attachments: displayAttachments } : {}),
    });
    const activeId = conversation.id;
    if (conversation.messages.length === 1) {
      const location = this.plugin.describeContextLocation();
      store.setTitle(activeId, generatedConversationTitle({
        question: prompt,
        locationLabel: location.label ?? "",
        taskMode: this.activeTaskMode,
      }), "provisional");
    }
    this.messages = conversation.messages;
    void this.plugin.rememberActiveConversation();
    if (this.promptEl && this.plugin.settings.clearComposerAfterSubmit) {
      this.promptEl.value = "";
      this.autoGrowComposer();
    }
    this.lastTemplatePrompt = "";
    this.renderSessionOptions(); // first question titles the conversation
    this.renderMessages();

    const assistantMessage: ChatMessage = { role: "assistant", content: "" };
    this.messages = store.appendMessage(assistantMessage).messages;
    const liveEl = this.appendMessageCard(assistantMessage, true);
    this.updateActivityCard(liveEl, tr(lang, this.plugin.settings.streamEnabled ? "activity.streaming" : "activity.thinking"));
    this.setStatus(
      tr(lang, this.plugin.settings.streamEnabled ? "status.streaming" : "status.thinking"),
      "working",
    );
    this.scrollHistoryToBottom();

    try {
      const result = await runAsk(prompt, askOptions, this.makeAskHost(assistantMessage, liveEl));
      assistantMessage.content = result.answer;
      assistantMessage.sources = result.sources;
      assistantMessage.trace = result.trace;
      assistantMessage.memoryEvents = result.memoryEvents;
      this.lastMemoryEvents = result.memoryEvents ?? this.deriveMemoryEvents();
      store.setTitle(activeId, generatedConversationTitle({
        question: prompt,
        answer: result.answer,
        locationLabel: this.plugin.describeContextLocation().label ?? "",
        taskMode: this.activeTaskMode,
      }), "generated");
      store.touch();
      if (generation === this.submitGeneration) {
        this.setStatus(tr(lang, "status.ready"), "ready");
      }
    } catch (error) {
      const stale = generation !== this.submitGeneration;
      const cancelled = this.stopRequested || isCancelledError(error);
      if (stale || cancelled) {
        // Stopped by the user, or superseded by switch/new/delete/close.
        // Keep any partial answer as a normal (non-error) message; drop empties.
        if (!assistantMessage.content.trim()) {
          const index = conversation.messages.indexOf(assistantMessage);
          if (index >= 0) {
            conversation.messages.splice(index, 1);
          }
        } else {
          assistantMessage.isError = false;
        }
        store.touch();
        this.isSubmitting = false;
        this.stopRequested = false;
        if (!stale) {
          // Same conversation is still on screen: finalize the partial card and
          // reset the composer back to a ready state.
          this.setComposerBusy(false);
          this.setStatus(tr(lang, "status.stopped"), "idle");
          this.pendingAttachments = [];
          this.memoryCommands = [];
          this.memoryDisabledForNext = false;
          this.renderMessages();
          this.renderAttachments();
          this.renderMemoryStatus();
          this.focusComposerIfNeeded();
        }
        return;
      }
      const message = error instanceof Error ? error.message : String(error);
      assistantMessage.content = formatBackendErrorMessage(message, lang);
      assistantMessage.isError = true;
      store.touch();
      this.setStatus(tr(lang, "status.unavailable"), "error");
      // A transient backend failure shouldn't cost the user their typed question.
      if (this.promptEl && !this.promptEl.value.trim() && this.plugin.settings.clearComposerAfterSubmit) {
        this.promptEl.value = prompt;
        this.lastTemplatePrompt = prompt;
        this.autoGrowComposer();
      }
    }
    this.isSubmitting = false;
    this.stopRequested = false;
    this.setComposerBusy(false);
    this.pendingAttachments = [];
    this.memoryCommands = [];
    this.memoryDisabledForNext = false;
    if (generation === this.submitGeneration) {
      this.renderMessages();
      this.renderAttachments();
      this.renderMemoryStatus();
      this.focusComposerIfNeeded();
    }
  }

  private deriveMemoryEvents(): MemoryEvents {
    const remember = this.memoryCommands?.includes("remember") ? 1 : 0;
    return {
      referenced_count: 0,
      candidate_count: this.memoryDisabledForNext ? 0 : remember,
      auto_merged_count: 0,
      review_count: this.memoryDisabledForNext ? 0 : remember,
      disabled: this.memoryDisabledForNext,
      commands: this.memoryCommands,
    };
  }

  private deriveRuntimeContext(prompt: string): AssistantRuntimeContext | undefined {
    if (!asksAboutRuntimeError(prompt)) {
      return undefined;
    }
    const lastError = [...this.messages]
      .reverse()
      .find((message) => message.role === "assistant" && message.isError && message.content.trim());
    if (!lastError) {
      return undefined;
    }
    const message = lastError.content.replace(/\s+/g, " ").trim();
    return {
      last_error: {
        code: errorCodeFromMessage(message),
        message: message.slice(0, 500),
      },
    };
  }

  /** Bridge the view's DOM/plugin into the DOM-free ask runner (which owns the
   * busy-retry / stream-fallback / cancel orchestration and is unit-tested). */
  private makeAskHost(assistantMessage: ChatMessage, liveEl: LiveMessageElements): AskRunnerHost {
    return {
      streamEnabled: this.plugin.settings.streamEnabled,
      lang: this.plugin.settings.uiLanguage,
      taskMode: this.activeTaskMode,
      isStopRequested: () => this.stopRequested,
      ask: (prompt, taskMode, askOptions) => this.plugin.askAssistant(prompt, taskMode, askOptions),
      stream: (prompt, taskMode, handlers, askOptions) =>
        this.plugin.streamAssistant(prompt, taskMode, handlers, askOptions),
      setStatus: (text, tone) => this.setStatus(text, tone),
      setActivity: (text) => this.updateActivityCard(liveEl, text),
      appendDelta: (text) => {
        assistantMessage.content += text;
        this.queueLiveRender(liveEl, assistantMessage);
      },
      setTrace: (trace) => {
        assistantMessage.trace = trace;
      },
      resetForRetry: () => {
        assistantMessage.content = "";
        this.cancelLiveRender();
        liveEl.contentEl.setText("");
        liveEl.contentEl.addClass("is-empty");
      },
      sleep: (ms) => new Promise((resolve) => window.setTimeout(resolve, ms)),
    };
  }

  /** Streaming updates only touch the live card's text, throttled — no full re-render per token. */
  private queueLiveRender(liveEl: LiveMessageElements, message: ChatMessage): void {
    if (this.liveRenderTimer !== null) {
      return;
    }
    this.liveRenderTimer = window.setTimeout(() => {
      this.liveRenderTimer = null;
      const hasText = !!message.content.trim();
      liveEl.contentEl.toggleClass("is-empty", !hasText);
      liveEl.activityEl?.toggleClass("has-output", hasText);
      if (message.trace && message.trace.first_dom_painted_ms === undefined) {
        message.trace.first_dom_painted_ms = Date.now();
      }
      if (hasText) {
        void this.paintLiveMarkdown(liveEl.contentEl, message.content);
      } else {
        liveEl.contentEl.setText(message.content);
        this.scrollHistoryToBottom();
      }
    }, 120);
  }

  /** Render the in-progress answer as Markdown into an off-DOM node, then swap it
   * in — so streaming shows formatted text (headings/code/lists) instead of raw
   * markdown that "snaps" to rendered only when complete. Off-DOM render avoids
   * the clear-then-fill flicker; the final answer is authoritatively re-rendered
   * by renderMessages() on done, so any mid-stream partial markdown self-corrects. */
  private async paintLiveMarkdown(contentEl: HTMLElement, content: string): Promise<void> {
    const sourcePath = this.app.workspace.getActiveFile()?.path ?? "";
    const staging = createDiv();
    const child = new Component();
    this.addChild(child);
    child.load();
    try {
      const prepared = normalizeMathDelimiters(stripTrailingSourcesBlock(content));
      await MarkdownRenderer.render(this.app, prepared, staging, sourcePath, child);
      contentEl.empty();
      while (staging.firstChild) {
        contentEl.appendChild(staging.firstChild);
      }
    } catch (error) {
      contentEl.setText(content);
      console.error("Rtime Assistant live markdown render failed", error);
    } finally {
      // The just-painted nodes belong to `child`; retire the previous live child.
      if (this.liveRenderChild) {
        this.liveRenderChild.unload();
      }
      this.liveRenderChild = child;
      this.scrollHistoryToBottom();
    }
  }

  private cancelLiveRender(): void {
    if (this.liveRenderTimer !== null) {
      window.clearTimeout(this.liveRenderTimer);
      this.liveRenderTimer = null;
    }
  }

  private async runHealthCheck(): Promise<void> {
    const lang = this.plugin.settings.uiLanguage;
    this.setStatus(tr(lang, "status.checking"), "working");
    try {
      const result = await this.plugin.checkHealth();
      this.setStatus(`${tr(lang, "status.healthy")}: ${result.slice(0, 80)}`, "ready");
    } catch (error) {
      const message = error instanceof Error ? error.message : String(error);
      this.setStatus(tr(lang, "status.healthFailed"), "error");
      new Notice(message);
    }
  }

  private setStatus(text: string, tone: "idle" | "ready" | "working" | "error"): void {
    if (this.statusEl) {
      this.statusEl.removeClass("is-idle", "is-ready", "is-working", "is-error");
      this.statusEl.addClass(`is-${tone}`);
    }
    if (this.statusTextEl) {
      this.statusTextEl.setText(text);
    }
  }

  private scrollHistoryToBottom(): void {
    if (this.historyEl && this.plugin.settings.autoScrollResponses) {
      this.historyEl.scrollTop = this.historyEl.scrollHeight;
    }
  }

  private focusComposerIfNeeded(): void {
    if (this.promptEl && this.plugin.settings.focusComposerAfterSubmit) {
      this.promptEl.focus();
    }
  }

  /** Grow the composer to fit its content (up to the CSS max-height), so a
   * multi-line prompt is visible without manual resize. */
  private autoGrowComposer(): void {
    const el = this.promptEl;
    if (!el) {
      return;
    }
    el.style.height = "auto";
    el.style.height = `${Math.min(el.scrollHeight, 180)}px`;
  }
}
