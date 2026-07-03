// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { normalizePath, Notice, Plugin, WorkspaceLeaf } from "obsidian";
import type { Editor, MarkdownFileInfo, MarkdownView } from "obsidian";
import { VIEW_TYPE_RTIME_ASSISTANT } from "./constants";
import {
  captureCurrentContextSnapshot,
  captureMarkdownEditorSnapshot,
  collectAssistantContext,
  describeContextLocation,
  findContextMarkdownView,
  pendingSelectionFromSnapshot,
  type ContextLocationDisplay,
  type EditorContextSnapshot,
} from "./context";
import { ConversationStore } from "./conversations";
import { tr } from "./i18n";
import { pollSelftestRequest, runSelftestManually } from "./selftest";
import {
  createAssistantClient,
  type AssistantClient,
  type AssistantSessionInfo,
  type AssistantStreamHandlers,
} from "./services/assistant-client";
import {
  downloadPluginRelease,
  REQUIRED_PLUGIN_RELEASE_FILES,
  compareVersions,
  fetchPluginReleaseManifest,
  pluginReleaseStatus,
  type RequiredPluginReleaseFile,
} from "./services/plugin-updater";
import { DEFAULT_SETTINGS, RtimeAssistantSettingTab } from "./settings";
import type {
  AssistantContext,
  AssistantAttachment,
  AssistantPrepareResult,
  AssistantResult,
  AssistantRuntimeContext,
  HistoryItem,
  MemoryEvents,
  PendingSelection,
  RtimeAssistantSettings,
  TaskMode,
} from "./types";
import { RtimeAssistantView } from "./view";

const RELOAD_NOTICE_STORAGE_KEY = "rtime-assistant.reloadNotice";

/** Per-request session options threaded from the view (or selftest) to the gateway. */
export interface AskOptions {
  conversationId?: string;
  history?: HistoryItem[];
  attachments?: AssistantAttachment[];
  memory?: MemoryEvents;
  runtime?: AssistantRuntimeContext;
}

export default class RtimeAssistantPlugin extends Plugin {
  readonly viewType = VIEW_TYPE_RTIME_ASSISTANT;
  settings: RtimeAssistantSettings = DEFAULT_SETTINGS;
  lastAnswer = "";
  conversations!: ConversationStore;
  private readonly assistantClient: AssistantClient = createAssistantClient();
  private inFlightRequests = 0;
  private contextSnapshot: EditorContextSnapshot | null = null;
  private pendingSelection: PendingSelection | null = null;
  private prepareTimer: number | null = null;
  private lastPrepareKey = "";
  private lastPrepareAt = 0;
  private lastPrepareExpiresAt = 0;
  private lastPrepareFailureKey = "";
  private lastPrepareFailureAt = 0;
  private lastPrepareResult: (AssistantPrepareResult & { key: string }) | null = null;
  private prepareInFlightKey = "";
  private prepareQueuedAfterFlight = false;

  /** True while a user/selftest request is talking to the gateway. */
  isRequestInFlight(): boolean {
    return this.inFlightRequests > 0;
  }

  /** Cancel the in-flight streaming request — used on view close and unload. */
  cancelActiveRequest(): void {
    this.assistantClient.cancelActive();
  }

  async onload(): Promise<void> {
    await this.loadSettings();
    this.consumeReloadNotice();
    this.conversations = new ConversationStore(
      this.app.vault.adapter,
      `${this.app.vault.configDir}/plugins/${this.manifest.id}`,
    );
    await this.conversations.load(this.settings.lastConversationId);
    const lang = this.settings.uiLanguage;

    this.registerView(
      VIEW_TYPE_RTIME_ASSISTANT,
      (leaf) => new RtimeAssistantView(leaf, this),
    );

    this.addRibbonIcon("bot", tr(lang, "command.open"), () => {
      void this.activateView();
    });

    this.addCommand({
      id: "open-rtime-assistant",
      name: tr(lang, "command.open"),
      callback: () => {
        void this.activateView();
      },
    });

    this.addCommand({
      id: "ask-about-current-note",
      name: tr(lang, "command.askNote"),
      callback: () => {
        void this.activateView(
          tr(this.settings.uiLanguage, "prompt.askNote"),
          "summarize",
        );
      },
    });

    this.addCommand({
      id: "explain-selection",
      name: tr(lang, "command.explainSelection"),
      editorCallback: () => {
        void this.activateView(
          tr(this.settings.uiLanguage, "prompt.explainSelection"),
          "explain",
        );
      },
    });

    this.addCommand({
      id: "find-related-notes",
      name: tr(lang, "command.related"),
      callback: () => {
        void this.activateView(
          tr(this.settings.uiLanguage, "prompt.related"),
          "related",
        );
      },
    });

    this.addCommand({
      id: "citation-review",
      name: tr(lang, "command.citationReview"),
      callback: () => {
        void this.activateView(
          tr(this.settings.uiLanguage, "prompt.citationReview"),
          "citation-review",
        );
      },
    });

    this.addCommand({
      id: "insert-last-answer",
      name: tr(lang, "command.insertLast"),
      callback: () => this.insertLastAnswer(),
    });

    this.addCommand({
      id: "run-selftest",
      name: tr(lang, "command.selftest"),
      callback: () => {
        void this.runSelftestNow();
      },
    });

    this.addCommand({
      id: "open-rtime-assistant-settings",
      name: tr(lang, "button.settings"),
      callback: () => this.openSettings(),
    });

    this.registerEvent(
      this.app.workspace.on("active-leaf-change", () => {
        this.rememberCurrentContext();
        this.refreshViews();
      }),
    );
    this.registerEvent(
      this.app.workspace.on("editor-change", (editor, info) => {
        this.rememberEditorContext(editor, info);
        this.refreshViews();
      }),
    );
    this.registerDomEvent(document, "mouseup", () => {
      this.rememberCurrentContext();
      this.refreshViews();
    });
    this.registerDomEvent(document, "keyup", () => {
      this.rememberCurrentContext();
      this.refreshViews();
    });
    this.registerInterval(
      window.setInterval(() => {
        this.rememberCurrentContext();
        this.refreshViews();
      }, 1500),
    );

    this.addSettingTab(new RtimeAssistantSettingTab(this.app, this));

    // File-trigger selftest channel for command-line agents (see selftest.ts).
    if (this.settings.selftestWatchEnabled) {
      this.app.workspace.onLayoutReady(() => {
        void pollSelftestRequest(this);
      });
      this.registerInterval(
        window.setInterval(() => {
          void pollSelftestRequest(this);
        }, 20000),
      );
    }
  }

  onunload(): void {
    if (this.prepareTimer !== null) {
      window.clearTimeout(this.prepareTimer);
      this.prepareTimer = null;
    }
    this.assistantClient.cancelActive();
    void this.conversations.flush();
    this.app.workspace.detachLeavesOfType(VIEW_TYPE_RTIME_ASSISTANT);
  }

  /** Persist which conversation the sidebar should restore next time. */
  async rememberActiveConversation(): Promise<void> {
    const id = this.conversations.getActiveId() ?? "";
    if (this.settings.lastConversationId === id) {
      return;
    }
    this.settings.lastConversationId = id;
    await this.saveData(this.settings);
  }

  async activateView(seedPrompt?: string, taskMode?: TaskMode): Promise<void> {
    this.rememberCurrentContext();
    let leaf: WorkspaceLeaf | null = this.app.workspace.getLeavesOfType(VIEW_TYPE_RTIME_ASSISTANT)[0] ?? null;
    if (!leaf) {
      leaf = this.app.workspace.getRightLeaf(false);
      if (!leaf) {
        new Notice(tr(this.settings.uiLanguage, "notice.openFailed"));
        return;
      }
      await leaf.setViewState({ type: VIEW_TYPE_RTIME_ASSISTANT, active: true });
    }
    this.app.workspace.revealLeaf(leaf);
    this.schedulePrepare("activate-view");
    const view = leaf.view;
    if (view instanceof RtimeAssistantView && seedPrompt) {
      view.setPrompt(seedPrompt, taskMode);
    }
  }

  async loadSettings(): Promise<void> {
    this.settings = Object.assign({}, DEFAULT_SETTINGS, await this.loadData());
  }

  async saveSettings(options?: { rerenderViews?: boolean }): Promise<void> {
    await this.saveData(this.settings);
    this.assistantClient.clearHealthCache();
    this.refreshViews(options);
  }

  insertLastAnswer(): void {
    this.insertText(this.lastAnswer);
  }

  insertText(text: string): void {
    if (!text.trim()) {
      new Notice(tr(this.settings.uiLanguage, "notice.noAnswer"));
      return;
    }
    const markdownView = findContextMarkdownView(this.app, this.contextSnapshot);
    if (!markdownView) {
      new Notice(tr(this.settings.uiLanguage, "notice.noEditor"));
      return;
    }
    markdownView.editor.replaceSelection(text);
    new Notice(tr(this.settings.uiLanguage, "notice.inserted"));
  }

  async runSelftestNow(): Promise<void> {
    const report = await runSelftestManually(this);
    if (!report) {
      return;
    }
    new Notice(
      tr(this.settings.uiLanguage, report.ok ? "notice.selftestOk" : "notice.selftestFailed"),
    );
  }

  openSettings(): void {
    const appWithSettings = this.app as typeof this.app & {
      setting?: {
        open(): void;
        openTabById(id: string): void;
      };
    };
    if (!appWithSettings.setting) {
      new Notice(tr(this.settings.uiLanguage, "button.settings"));
      return;
    }
    appWithSettings.setting.open();
    appWithSettings.setting.openTabById(this.manifest.id);
  }

  async installPluginUpdate(): Promise<void> {
    const lang = this.settings.uiLanguage;
    if (!this.settings.pluginUpdateUrl.trim()) {
      new Notice(tr(lang, "settings.update.missingUrl"));
      return;
    }
    try {
      new Notice(tr(lang, "settings.update.installing"));
      const result = await downloadPluginRelease(this.settings);
      if (result.release.id !== this.manifest.id) {
        throw new Error(`release id ${result.release.id} does not match ${this.manifest.id}`);
      }
      const versionDelta = compareVersions(result.release.version, this.manifest.version);
      await this.writePluginUpdateFiles(result.files);
      this.recordPluginReleaseCheck(result.release, "installed");
      this.settings.pluginLastInstalledVersion = result.release.version;
      this.settings.pluginLastInstalledBuildId = result.release.build_id ?? result.release.generated_at ?? "";
      await this.saveData(this.settings);
      const suffix = versionDelta < 0 ? ` (${result.release.version} < ${this.manifest.version})` : "";
      new Notice(`${tr(lang, "settings.update.installed")} ${result.release.version}${suffix}`);
    } catch (error) {
      new Notice(`${tr(lang, "settings.update.failed")} ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async refreshPluginUpdateStatus(): Promise<void> {
    const lang = this.settings.uiLanguage;
    if (!this.settings.pluginUpdateUrl.trim()) {
      new Notice(tr(lang, "settings.update.missingUrl"));
      return;
    }
    try {
      const result = await fetchPluginReleaseManifest(this.settings);
      if (result.release.id !== this.manifest.id) {
        throw new Error(`release id ${result.release.id} does not match ${this.manifest.id}`);
      }
      this.recordPluginReleaseCheck(
        result.release,
        pluginReleaseStatus(
          result.release,
          this.manifest.version,
          this.settings.pluginLastInstalledVersion,
          this.settings.pluginLastInstalledBuildId,
        ),
      );
      await this.saveData(this.settings);
      new Notice(`${tr(lang, "settings.update.refresh.ok")} ${result.release.version}`);
    } catch (error) {
      this.settings.pluginLastCheckedAt = new Date().toISOString();
      this.settings.pluginLastUpdateStatus = `failed: ${error instanceof Error ? error.message : String(error)}`;
      await this.saveData(this.settings);
      new Notice(`${tr(lang, "settings.update.failed")} ${error instanceof Error ? error.message : String(error)}`);
    }
  }

  async reloadPlugin(): Promise<void> {
    const lang = this.settings.uiLanguage;
    await this.conversations.flush();
    this.assistantClient.cancelActive();
    await this.saveData(this.settings);
    new Notice(tr(lang, "settings.update.reloading"));
    this.markReloadNotice();
    window.setTimeout(async () => {
      // A full Obsidian reload is the only method that reliably reflects an
      // UPDATED main.js on every platform: a bare `window.location.reload()` has
      // been observed to no-op in some Obsidian/Electron states, and a soft
      // plugin disable/enable can serve a cached module on Windows. Try the
      // built-in reload command first, then a targeted plugin reload, then a
      // hard window reload — whichever the running app actually honors.
      try {
        const commands = (this.app as unknown as { commands?: { executeCommandById?: (id: string) => boolean } }).commands;
        if (commands?.executeCommandById?.("app:reload")) {
          return; // the window is reloading from disk
        }
      } catch {
        // fall through to the next method
      }
      try {
        const plugins = (this.app as unknown as {
          plugins?: { disablePlugin?: (id: string) => Promise<void>; enablePlugin?: (id: string) => Promise<void> };
        }).plugins;
        if (plugins?.disablePlugin && plugins?.enablePlugin) {
          await plugins.disablePlugin(this.manifest.id);
          await plugins.enablePlugin(this.manifest.id);
          this.clearReloadNotice();
          new Notice(`${tr(lang, "settings.update.reloaded")} ${this.manifest.version}`);
          return;
        }
      } catch {
        // fall through to the hard reload
      }
      try {
        window.location.reload();
      } catch (error) {
        this.clearReloadNotice();
        new Notice(`${tr(lang, "settings.update.reload.failed")} ${error instanceof Error ? error.message : String(error)}`);
      }
    }, 120);
  }

  private markReloadNotice(): void {
    try {
      window.sessionStorage.setItem(
        RELOAD_NOTICE_STORAGE_KEY,
        JSON.stringify({ pluginId: this.manifest.id, requestedAt: Date.now() }),
      );
    } catch {
      // Session storage can be unavailable in unusual WebView states; reload still works.
    }
  }

  private clearReloadNotice(): void {
    try {
      window.sessionStorage.removeItem(RELOAD_NOTICE_STORAGE_KEY);
    } catch {
      // Ignore storage failures; this is only a post-reload notice hint.
    }
  }

  private consumeReloadNotice(): void {
    try {
      const raw = window.sessionStorage.getItem(RELOAD_NOTICE_STORAGE_KEY);
      if (!raw) {
        return;
      }
      window.sessionStorage.removeItem(RELOAD_NOTICE_STORAGE_KEY);
      const payload = JSON.parse(raw) as { pluginId?: string };
      if (payload.pluginId === this.manifest.id) {
        new Notice(`${tr(this.settings.uiLanguage, "settings.update.reloaded")} ${this.manifest.version}`);
      }
    } catch {
      this.clearReloadNotice();
    }
  }

  private recordPluginReleaseCheck(release: { version: string; build_id?: string; generated_at?: string }, status: string): void {
    this.settings.pluginLastCheckedAt = new Date().toISOString();
    this.settings.pluginLastAvailableVersion = release.version;
    this.settings.pluginLastAvailableBuildId = release.build_id ?? "";
    this.settings.pluginLastAvailableGeneratedAt = release.generated_at ?? "";
    this.settings.pluginLastUpdateStatus = status;
  }

  private async writePluginUpdateFiles(files: Record<RequiredPluginReleaseFile, string>): Promise<void> {
    const adapter = this.app.vault.adapter;
    const pluginDir = normalizePath(`${this.app.vault.configDir}/plugins/${this.manifest.id}`);
    const backupDir = normalizePath(`${pluginDir}/updates/${new Date().toISOString().replace(/[:.]/g, "-")}`);
    await this.ensureAdapterDir(pluginDir);
    await this.ensureAdapterDir(normalizePath(`${pluginDir}/updates`));
    await this.ensureAdapterDir(backupDir);
    for (const file of REQUIRED_PLUGIN_RELEASE_FILES) {
      const currentPath = normalizePath(`${pluginDir}/${file}`);
      if (await adapter.exists(currentPath)) {
        const previous = await adapter.read(currentPath);
        await adapter.write(normalizePath(`${backupDir}/${file}`), previous);
      }
    }
    for (const file of REQUIRED_PLUGIN_RELEASE_FILES) {
      await adapter.write(normalizePath(`${pluginDir}/${file}`), files[file]);
    }
  }

  private async ensureAdapterDir(path: string): Promise<void> {
    const adapter = this.app.vault.adapter;
    if (!(await adapter.exists(path))) {
      await adapter.mkdir(path);
    }
  }

  async checkHealth(): Promise<string> {
    return this.assistantClient.checkBackendHealth(this.settings);
  }

  async collectContext(options: Pick<AskOptions, "attachments" | "memory" | "runtime"> = {}): Promise<AssistantContext> {
    this.rememberCurrentContext();
    return collectAssistantContext(this.app, this.settings, this.contextSnapshot, {
      pendingSelection: this.pendingSelection,
      attachments: options.attachments,
      memory: options.memory,
      runtime: options.runtime,
    });
  }

  schedulePrepare(_reason: string): void {
    if (!this.settings.prepareEnabled) {
      return;
    }
    if (this.prepareTimer !== null) {
      window.clearTimeout(this.prepareTimer);
    }
    const delay = Math.max(100, this.settings.prepareDebounceMs || DEFAULT_SETTINGS.prepareDebounceMs);
    this.prepareTimer = window.setTimeout(() => {
      this.prepareTimer = null;
      void this.prepareCurrentContext();
    }, delay);
  }

  private async prepareCurrentContext(): Promise<void> {
    if (!this.settings.prepareEnabled || this.isRequestInFlight()) {
      return;
    }
    this.rememberCurrentContext();
    const prepareSettings: RtimeAssistantSettings = {
      ...this.settings,
      includeActiveNoteBody: false,
      maxNoteChars: Math.min(this.settings.maxNoteChars, 2000),
    };
    const context = await collectAssistantContext(this.app, prepareSettings, this.contextSnapshot, {
      pendingSelection: this.pendingSelection,
    });
    const key = this.prepareKeyForContext(context);
    const now = Date.now();
    if (!key || (key === this.lastPrepareKey && now < this.lastPrepareExpiresAt)) {
      return;
    }
    if (key === this.lastPrepareFailureKey && now - this.lastPrepareFailureAt < 5000) {
      return;
    }
    if (this.prepareInFlightKey) {
      if (this.prepareInFlightKey !== key) {
        this.prepareQueuedAfterFlight = true;
      }
      return;
    }
    this.prepareInFlightKey = key;
    try {
      const result = await this.assistantClient.prepareContext(prepareSettings, context);
      const ttlMs = Math.max(30000, ((result.cache_ttl_seconds ?? 120) * 1000) - 5000);
      this.lastPrepareKey = key;
      this.lastPrepareAt = Date.now();
      this.lastPrepareExpiresAt = this.lastPrepareAt + ttlMs;
      this.lastPrepareFailureKey = "";
      this.lastPrepareFailureAt = 0;
      this.lastPrepareResult = { ...result, key };
    } catch (error) {
      this.lastPrepareResult = null;
      this.lastPrepareFailureKey = key;
      this.lastPrepareFailureAt = Date.now();
      console.debug("Rtime Assistant prepare skipped", error);
    } finally {
      this.prepareInFlightKey = "";
      if (this.prepareQueuedAfterFlight) {
        this.prepareQueuedAfterFlight = false;
        this.schedulePrepare("prepare-follow-up");
      }
    }
  }

  describeContextLocation(): ContextLocationDisplay {
    this.rememberCurrentContext();
    return describeContextLocation(this.app, this.contextSnapshot);
  }

  getPendingSelection(): PendingSelection | null {
    this.rememberCurrentContext();
    return this.pendingSelection;
  }

  clearPendingSelection(): void {
    this.pendingSelection = null;
    if (this.contextSnapshot) {
      this.contextSnapshot = { ...this.contextSnapshot, selectionText: "", updatedAt: Date.now() };
    }
    this.refreshViews();
  }

  private rememberCurrentContext(): void {
    this.contextSnapshot = captureCurrentContextSnapshot(this.app, this.contextSnapshot);
    this.rememberPendingSelection();
  }

  private rememberEditorContext(editor: Editor, info: MarkdownView | MarkdownFileInfo): void {
    this.contextSnapshot = captureMarkdownEditorSnapshot(editor, info, this.contextSnapshot) ?? this.contextSnapshot;
    this.rememberPendingSelection();
  }

  private rememberPendingSelection(): void {
    const pending = pendingSelectionFromSnapshot(this.app, this.contextSnapshot);
    if (pending) {
      this.pendingSelection = pending;
    }
  }

  async askAssistant(
    message: string,
    taskMode: TaskMode = this.settings.defaultTaskMode,
    options: AskOptions = {},
  ): Promise<AssistantResult> {
    this.inFlightRequests += 1;
    try {
      const context = await this.collectContext(options);
      if (options.history?.length) {
        context.history = options.history;
      }
      const result = await this.assistantClient.postAssistantRequest(
        this.settings,
        context,
        message,
        taskMode,
        this.sessionInfo(options, context),
      );
      this.lastAnswer = result.answer;
      return result;
    } finally {
      this.inFlightRequests -= 1;
    }
  }

  async streamAssistant(
    message: string,
    taskMode: TaskMode = this.settings.defaultTaskMode,
    handlers: AssistantStreamHandlers = {},
    options: AskOptions = {},
  ): Promise<AssistantResult> {
    this.inFlightRequests += 1;
    try {
      const context = await this.collectContext(options);
      if (options.history?.length) {
        context.history = options.history;
      }
      const result = await this.assistantClient.postAssistantStreamRequest(
        this.settings,
        context,
        message,
        taskMode,
        handlers,
        this.sessionInfo(options, context),
      );
      this.lastAnswer = result.answer;
      return result;
    } finally {
      this.inFlightRequests -= 1;
    }
  }

  private sessionInfo(options: AskOptions, context?: AssistantContext): AssistantSessionInfo | undefined {
    const session: AssistantSessionInfo = {};
    if (options.conversationId) {
      session.conversationId = options.conversationId;
    }
    const prepareKey = context ? this.prepareKeyForContext(context) : "";
    if (prepareKey && this.lastPrepareResult?.key === prepareKey) {
      session.prepareId = this.lastPrepareResult.prepare_id;
    }
    return session.conversationId || session.prepareId ? session : undefined;
  }

  private prepareKeyForContext(context: AssistantContext): string {
    const active = context.active_file;
    if (!active?.path && !this.settings.modelPrewarmEnabled) {
      return "";
    }
    return JSON.stringify({
      path: active?.path ?? "",
      mtime: active?.mtime ?? 0,
      page: context.pdf?.page ?? null,
      selection: context.selection?.chars ?? 0,
      mode: context.requested_mode,
      targetModule: this.settings.targetModule,
      targetFolder: this.settings.targetFolder,
      modelProviderId: this.settings.modelProviderId,
      modelId: this.settings.modelId,
      modelProtocol: this.settings.modelProtocol,
      modelPrewarmEnabled: this.settings.modelPrewarmEnabled,
      permissionMode: this.settings.permissionMode,
      approvalForwardingEnabled: this.settings.approvalForwardingEnabled,
    });
  }

  refreshViews(options?: { rerenderViews?: boolean }): void {
    let hasAssistantView = false;
    for (const leaf of this.app.workspace.getLeavesOfType(VIEW_TYPE_RTIME_ASSISTANT)) {
      const view = leaf.view;
      if (view instanceof RtimeAssistantView) {
        hasAssistantView = true;
        if (options?.rerenderViews) {
          view.rerender();
        } else {
          view.refreshContextSummary();
        }
      }
    }
    if (hasAssistantView) {
      this.schedulePrepare("refresh");
    }
  }
}
