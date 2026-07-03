// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { App, Notice, PluginSettingTab, Setting } from "obsidian";
import { getComposerTemplates, getTargetModuleOptions } from "./composer-contract";
import { contextModeLabel, targetModuleLabel, taskModeLabel, tr } from "./i18n";
import { fetchModelCatalog } from "./services/assistant-client";
import { createActionButton } from "./ui/elements";
import type {
  AssistantModelCatalog,
  AssistantModelCapabilities,
  AssistantModelProvider,
  ContextMode,
  PermissionMode,
  RtimeAssistantSettings,
  SubmitBehavior,
  TargetModule,
  TaskMode,
  ThinkingEffort,
  UiLanguage,
} from "./types";

export const DEFAULT_SETTINGS: RtimeAssistantSettings = {
  uiLanguage: "zh-CN",
  chatEndpoint: "http://127.0.0.1:8765/api/obsidian/chat",
  localClaudeEndpoint: "http://127.0.0.1:8765/api/obsidian/chat",
  healthEndpoint: "http://127.0.0.1:8765/healthz",
  contextMode: "current-note",
  defaultTaskMode: "ask",
  targetModule: "auto",
  targetFolder: "",
  includeSelection: true,
  includeActiveNoteBody: true,
  maxNoteChars: 12000,
  requestTimeoutMs: 180000,
  maxToolTurns: 0,
  requestRetryCount: 1,
  requestRetryDelayMs: 350,
  healthCacheMs: 5000,
  streamEnabled: true,
  prepareEnabled: true,
  modelPrewarmEnabled: true,
  prepareDebounceMs: 350,
  submitBehavior: "enter-send",
  clearComposerAfterSubmit: true,
  focusComposerAfterSubmit: true,
  autoScrollResponses: true,
  selftestWatchEnabled: true,
  modelProviderId: "",
  modelId: "",
  modelProtocol: "",
  modelCatalog: null,
  permissionMode: "bypassPermissions",
  approvalForwardingEnabled: true,
  modelThinkingEffort: "",
  modelEditMode: false,
  localClaudeWorkspace: "",
  localClaudeWorkspaceReadOnly: false,
  pluginUpdateUrl: "",
  pluginLastInstalledVersion: "",
  pluginLastInstalledBuildId: "",
  pluginLastCheckedAt: "",
  pluginLastAvailableVersion: "",
  pluginLastAvailableBuildId: "",
  pluginLastAvailableGeneratedAt: "",
  pluginLastUpdateStatus: "",
  lastConversationId: "",
};

export interface SettingsHost {
  manifest: { id: string; name?: string; version: string };
  settings: RtimeAssistantSettings;
  saveSettings(options?: { rerenderViews?: boolean }): Promise<void>;
  refreshPluginUpdateStatus(): Promise<void>;
  installPluginUpdate(): Promise<void>;
  reloadPlugin(): Promise<void>;
}

export class RtimeAssistantSettingTab extends PluginSettingTab {
  private host: SettingsHost;

  constructor(app: App, plugin: SettingsHost & ConstructorParameters<typeof PluginSettingTab>[1]) {
    super(app, plugin);
    this.host = plugin;
  }

  display(): void {
    const { containerEl } = this;
    const lang = this.host.settings.uiLanguage;
    containerEl.empty();
    containerEl.createEl("h2", { text: tr(lang, "settings.title") });

    new Setting(containerEl)
      .setName(tr(lang, "settings.language.name"))
      .setDesc(tr(lang, "settings.language.desc"))
      .addDropdown((dropdown) =>
        dropdown
          .addOption("zh-CN", "简体中文")
          .addOption("en", "English")
          .setValue(this.host.settings.uiLanguage)
          .onChange(async (value) => {
            this.host.settings.uiLanguage = value as UiLanguage;
            await this.host.saveSettings({ rerenderViews: true });
            this.display();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.chat.name"))
      .setDesc(tr(lang, "settings.chat.desc"))
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.chatEndpoint)
          .setValue(this.host.settings.chatEndpoint)
          .onChange(async (value) => {
            this.host.settings.chatEndpoint = value.trim() || DEFAULT_SETTINGS.chatEndpoint;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.localClaude.name"))
      .setDesc(tr(lang, "settings.localClaude.desc"))
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.localClaudeEndpoint)
          .setValue(this.host.settings.localClaudeEndpoint)
          .onChange(async (value) => {
            this.host.settings.localClaudeEndpoint = value.trim() || DEFAULT_SETTINGS.localClaudeEndpoint;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.health.name"))
      .setDesc(tr(lang, "settings.health.desc"))
      .addText((text) =>
        text
          .setPlaceholder(DEFAULT_SETTINGS.healthEndpoint)
          .setValue(this.host.settings.healthEndpoint)
          .onChange(async (value) => {
            this.host.settings.healthEndpoint = value.trim() || DEFAULT_SETTINGS.healthEndpoint;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.stream.name"))
      .setDesc(tr(lang, "settings.stream.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.streamEnabled).onChange(async (value) => {
          this.host.settings.streamEnabled = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.prepare.name"))
      .setDesc(tr(lang, "settings.prepare.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.prepareEnabled).onChange(async (value) => {
          this.host.settings.prepareEnabled = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.prewarm.name"))
      .setDesc(tr(lang, "settings.prewarm.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.modelPrewarmEnabled).onChange(async (value) => {
          this.host.settings.modelPrewarmEnabled = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.prepareDebounce.name"))
      .setDesc(tr(lang, "settings.prepareDebounce.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.prepareDebounceMs))
          .setValue(String(this.host.settings.prepareDebounceMs))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.prepareDebounceMs = Number.isFinite(parsed) && parsed >= 100
              ? parsed
              : DEFAULT_SETTINGS.prepareDebounceMs;
            await this.host.saveSettings();
          }),
      );

    containerEl.createEl("h3", { text: tr(lang, "settings.model.section") });
    const catalog = catalogForDisplay(this.host.settings.modelCatalog, this.host.settings);
    const selectedProvider = findProvider(catalog, this.host.settings.modelProviderId);
    const selectedModel = selectedProvider?.models.find((model) => model.id === this.host.settings.modelId);
    const capabilityText = modelCapabilityLabel(selectedModel?.capabilities);
    const catalogState = catalog
      ? `${tr(lang, "settings.model.catalogLoaded")} ${catalog.last_refreshed ?? catalog.generated_at ?? ""}`.trim()
      : tr(lang, "settings.model.catalogMissing");

    new Setting(containerEl)
      .setName(tr(lang, "settings.model.provider.name"))
      .setDesc(tr(lang, "settings.model.provider.desc"))
      .addDropdown((dropdown) => {
        dropdown.addOption("", tr(lang, "settings.model.gatewayDefault"));
        for (const provider of catalog?.providers ?? []) {
          dropdown.addOption(provider.id, provider.label ?? provider.id);
        }
        dropdown.setValue(this.host.settings.modelProviderId).onChange(async (value) => {
          const provider = findProvider(catalog, value);
          this.host.settings.modelProviderId = value;
          this.host.settings.modelId = "";
          this.host.settings.modelProtocol = provider?.protocol ?? "";
          await this.host.saveSettings();
          this.display();
        });
      });

    new Setting(containerEl)
      .setName(tr(lang, "settings.model.protocol.name"))
      .setDesc(tr(lang, "settings.model.protocol.desc"))
      .addText((text) =>
        text
          .setValue(this.host.settings.modelProtocol || tr(lang, "settings.model.gatewayDefault"))
          .setDisabled(true),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.model.model.name"))
      .setDesc(capabilityText || tr(lang, "settings.model.model.desc"))
      .addDropdown((dropdown) => {
        dropdown.addOption("", tr(lang, "settings.model.gatewayDefault"));
        for (const model of selectedProvider?.models ?? []) {
          dropdown.addOption(model.id, model.label ?? model.id);
        }
        dropdown.setValue(this.host.settings.modelId).onChange(async (value) => {
          const provider = findProvider(catalog, this.host.settings.modelProviderId);
          const model = provider?.models.find((item) => item.id === value);
          this.host.settings.modelId = value;
          this.host.settings.modelProtocol = model?.protocol ?? provider?.protocol ?? "";
          await this.host.saveSettings();
          this.display();
        });
      });

    new Setting(containerEl)
      .setName(tr(lang, "settings.model.catalog.name"))
      .setDesc(catalogState)
      .addButton((button) =>
        button.setButtonText(tr(lang, "settings.model.refresh")).onClick(async () => {
          try {
            this.host.settings.modelCatalog = await fetchModelCatalog(this.host.settings, true);
            const provider = findProvider(
              catalogForDisplay(this.host.settings.modelCatalog, this.host.settings),
              this.host.settings.modelProviderId,
            );
            if (!provider) {
              this.host.settings.modelProviderId = "";
              this.host.settings.modelId = "";
              this.host.settings.modelProtocol = "";
            }
            await this.host.saveSettings();
            new Notice(tr(lang, "settings.model.refreshOk"));
            this.display();
          } catch (error) {
            new Notice(`${tr(lang, "settings.model.refreshFailed")} ${error instanceof Error ? error.message : String(error)}`);
          }
        }),
      )
      .addButton((button) =>
        button.setButtonText(tr(lang, "settings.model.test")).onClick(async () => {
          try {
            this.host.settings.modelCatalog = await fetchModelCatalog(this.host.settings, false);
            await this.host.saveSettings();
            new Notice(tr(lang, "settings.model.testOk"));
            this.display();
          } catch (error) {
            new Notice(`${tr(lang, "settings.model.testFailed")} ${error instanceof Error ? error.message : String(error)}`);
          }
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.thinking.effort.name"))
      .setDesc(tr(lang, "settings.thinking.effort.desc"))
      .addDropdown((dropdown) =>
        dropdown
          .addOption("", tr(lang, "settings.thinking.effort.default"))
          .addOption("low", tr(lang, "settings.thinking.effort.low"))
          .addOption("medium", tr(lang, "settings.thinking.effort.medium"))
          .addOption("high", tr(lang, "settings.thinking.effort.high"))
          .addOption("xhigh", tr(lang, "settings.thinking.effort.xhigh"))
          .addOption("max", tr(lang, "settings.thinking.effort.max"))
          .setValue(this.host.settings.modelThinkingEffort)
          .onChange(async (value) => {
            this.host.settings.modelThinkingEffort = value as ThinkingEffort;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.thinking.editMode.name"))
      .setDesc(tr(lang, "settings.thinking.editMode.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.modelEditMode).onChange(async (value) => {
          this.host.settings.modelEditMode = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.workspace.name"))
      .setDesc(tr(lang, "settings.workspace.desc"))
      .addText((text) =>
        text
          .setPlaceholder(tr(lang, "settings.workspace.placeholder"))
          .setValue(this.host.settings.localClaudeWorkspace)
          .onChange(async (value) => {
            this.host.settings.localClaudeWorkspace = value.trim();
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.workspace.readonly.name"))
      .setDesc(tr(lang, "settings.workspace.readonly.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.localClaudeWorkspaceReadOnly).onChange(async (value) => {
          this.host.settings.localClaudeWorkspaceReadOnly = value;
          await this.host.saveSettings();
        }),
      );

    containerEl.createEl("h3", { text: tr(lang, "settings.permissions.section") });

    new Setting(containerEl)
      .setName(tr(lang, "settings.permissions.mode.name"))
      .setDesc(tr(lang, "settings.permissions.mode.desc"))
      .addDropdown((dropdown) =>
        dropdown
          .addOption("dontAsk", tr(lang, "settings.permissions.mode.dontAsk"))
          .addOption("default", tr(lang, "settings.permissions.mode.default"))
          .addOption("acceptEdits", tr(lang, "settings.permissions.mode.acceptEdits"))
          .addOption("plan", tr(lang, "settings.permissions.mode.plan"))
          .addOption("bypassPermissions", tr(lang, "settings.permissions.mode.bypassPermissions"))
          .setValue(this.host.settings.permissionMode)
          .onChange(async (value) => {
            this.host.settings.permissionMode = value as PermissionMode;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.permissions.approvals.name"))
      .setDesc(tr(lang, "settings.permissions.approvals.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.approvalForwardingEnabled).onChange(async (value) => {
          this.host.settings.approvalForwardingEnabled = value;
          await this.host.saveSettings();
        }),
      );

    containerEl.createEl("h3", { text: tr(lang, "settings.update.section") });
    new Setting(containerEl)
      .setName(tr(lang, "settings.update.url.name"))
      .setDesc(tr(lang, "settings.update.url.desc"))
      .addText((text) =>
        text
          .setPlaceholder(tr(lang, "settings.update.url.placeholder"))
          .setValue(this.host.settings.pluginUpdateUrl)
          .onChange(async (value) => {
            this.host.settings.pluginUpdateUrl = value.trim();
            await this.host.saveSettings();
          }),
      );

    const installedLabel = this.host.settings.pluginLastInstalledBuildId
      ? `${this.host.settings.pluginLastInstalledVersion || tr(lang, "settings.update.versionUnknown")} · ${this.host.settings.pluginLastInstalledBuildId}`
      : tr(lang, "settings.update.notInstalled");
    const checkedLabel = this.host.settings.pluginLastCheckedAt || tr(lang, "settings.update.notChecked");
    const availableLabel = this.host.settings.pluginLastAvailableVersion
      ? [
          this.host.settings.pluginLastAvailableVersion,
          this.host.settings.pluginLastAvailableBuildId,
          this.host.settings.pluginLastAvailableGeneratedAt,
        ].filter(Boolean).join(" · ")
      : tr(lang, "settings.update.versionUnknown");
    const statusLabel = pluginUpdateStatusLabel(lang, this.host.settings.pluginLastUpdateStatus);
    const statusTone = pluginUpdateStatusTone(this.host.settings.pluginLastUpdateStatus);
    const updateCard = containerEl.createDiv({ cls: "rtime-assistant-settings-update-card" });
    const updateHead = updateCard.createDiv({ cls: "rtime-assistant-settings-update-head" });
    updateHead.createDiv({ cls: "rtime-assistant-settings-update-title", text: tr(lang, "settings.update.status.name") });
    const statusBadge = updateHead.createDiv({
      cls: `rtime-assistant-settings-update-badge is-${statusTone}`,
      text: statusLabel,
    });
    statusBadge.setAttribute("title", statusLabel);

    const updateGrid = updateCard.createDiv({ cls: "rtime-assistant-settings-update-grid" });
    createUpdateMetric(updateGrid, tr(lang, "settings.update.current"), this.host.manifest.version);
    createUpdateMetric(updateGrid, tr(lang, "settings.update.available"), availableLabel);
    createUpdateMetric(updateGrid, tr(lang, "settings.update.lastChecked"), checkedLabel);
    createUpdateMetric(updateGrid, tr(lang, "settings.update.lastInstalled"), installedLabel);

    const updateActions = updateCard.createDiv({ cls: "rtime-assistant-settings-update-actions" });
    const refreshLabel = tr(lang, "settings.update.refresh.button");
    const installLabel = tr(lang, "settings.update.install.button");
    const reloadLabel = tr(lang, "settings.update.reload.button");
    const checkingLabel = tr(lang, "settings.update.checking");
    const refreshButton = createActionButton(updateActions, "refresh-cw", refreshLabel);
    refreshButton.addClass("rtime-assistant-settings-update-action");
    refreshButton.addEventListener("click", async () => {
      await runBusyButton(refreshButton, refreshLabel, checkingLabel, async () => {
        await this.host.refreshPluginUpdateStatus();
        this.display();
      });
    });
    const installButton = createActionButton(updateActions, "download-cloud", installLabel);
    installButton.addClass("rtime-assistant-settings-update-action");
    installButton.addClass("is-primary");
    installButton.addEventListener("click", async () => {
      await runBusyButton(installButton, installLabel, checkingLabel, async () => {
        await this.host.installPluginUpdate();
        this.display();
      });
    });
    const reloadButton = createActionButton(updateActions, "rotate-cw", reloadLabel);
    reloadButton.addClass("rtime-assistant-settings-update-action");
    reloadButton.addEventListener("click", async () => {
      await runBusyButton(reloadButton, reloadLabel, tr(lang, "settings.update.reloading"), async () => {
        await this.host.reloadPlugin();
      });
    });

    new Setting(containerEl)
      .setName(tr(lang, "settings.context.name"))
      .setDesc(tr(lang, "settings.context.desc"))
      .addDropdown((dropdown) =>
        dropdown
          .addOption("current-note", contextModeLabel(lang, "current-note"))
          .addOption("selection", contextModeLabel(lang, "selection"))
          .addOption("vault", contextModeLabel(lang, "vault"))
          .setValue(this.host.settings.contextMode)
          .onChange(async (value) => {
            this.host.settings.contextMode = value as ContextMode;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.task.name"))
      .setDesc(tr(lang, "settings.task.desc"))
      .addDropdown((dropdown) => {
        for (const item of getComposerTemplates()) {
          dropdown.addOption(item.taskMode, taskModeLabel(lang, item.taskMode));
        }
        dropdown.setValue(this.host.settings.defaultTaskMode).onChange(async (value) => {
          this.host.settings.defaultTaskMode = value as TaskMode;
          await this.host.saveSettings();
        });
      });

    new Setting(containerEl)
      .setName(tr(lang, "settings.module.name"))
      .setDesc(tr(lang, "settings.module.desc"))
      .addDropdown((dropdown) => {
        for (const item of getTargetModuleOptions()) {
          dropdown.addOption(item.id, targetModuleLabel(lang, item.id));
        }
        dropdown.setValue(this.host.settings.targetModule).onChange(async (value) => {
          this.host.settings.targetModule = value as TargetModule;
          await this.host.saveSettings({ rerenderViews: true });
        });
      });

    new Setting(containerEl)
      .setName(tr(lang, "settings.folder.name"))
      .setDesc(tr(lang, "settings.folder.desc"))
      .addText((text) =>
        text
          .setPlaceholder(tr(lang, "settings.folder.placeholder"))
          .setValue(this.host.settings.targetFolder)
          .onChange(async (value) => {
            this.host.settings.targetFolder = value.trim();
            await this.host.saveSettings({ rerenderViews: true });
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.selection.name"))
      .setDesc(tr(lang, "settings.selection.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.includeSelection).onChange(async (value) => {
          this.host.settings.includeSelection = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.note.name"))
      .setDesc(tr(lang, "settings.note.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.includeActiveNoteBody).onChange(async (value) => {
          this.host.settings.includeActiveNoteBody = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.maxChars.name"))
      .setDesc(tr(lang, "settings.maxChars.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.maxNoteChars))
          .setValue(String(this.host.settings.maxNoteChars))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.maxNoteChars = Number.isFinite(parsed) && parsed > 0
              ? parsed
              : DEFAULT_SETTINGS.maxNoteChars;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.timeout.name"))
      .setDesc(tr(lang, "settings.timeout.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.requestTimeoutMs))
          .setValue(String(this.host.settings.requestTimeoutMs))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.requestTimeoutMs = Number.isFinite(parsed) && parsed > 0
              ? parsed
              : DEFAULT_SETTINGS.requestTimeoutMs;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.maxToolTurns.name"))
      .setDesc(tr(lang, "settings.maxToolTurns.desc"))
      .addText((text) =>
        text
          .setPlaceholder("0")
          .setValue(String(this.host.settings.maxToolTurns))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.maxToolTurns = Number.isFinite(parsed) && parsed > 0
              ? parsed
              : 0;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.retryCount.name"))
      .setDesc(tr(lang, "settings.retryCount.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.requestRetryCount))
          .setValue(String(this.host.settings.requestRetryCount))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.requestRetryCount = Number.isFinite(parsed) && parsed >= 0
              ? Math.min(parsed, 3)
              : DEFAULT_SETTINGS.requestRetryCount;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.retryDelay.name"))
      .setDesc(tr(lang, "settings.retryDelay.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.requestRetryDelayMs))
          .setValue(String(this.host.settings.requestRetryDelayMs))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.requestRetryDelayMs = Number.isFinite(parsed) && parsed >= 0
              ? parsed
              : DEFAULT_SETTINGS.requestRetryDelayMs;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.healthCache.name"))
      .setDesc(tr(lang, "settings.healthCache.desc"))
      .addText((text) =>
        text
          .setPlaceholder(String(DEFAULT_SETTINGS.healthCacheMs))
          .setValue(String(this.host.settings.healthCacheMs))
          .onChange(async (value) => {
            const parsed = Number.parseInt(value, 10);
            this.host.settings.healthCacheMs = Number.isFinite(parsed) && parsed >= 0
              ? parsed
              : DEFAULT_SETTINGS.healthCacheMs;
            await this.host.saveSettings();
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.submitBehavior.name"))
      .setDesc(tr(lang, "settings.submitBehavior.desc"))
      .addDropdown((dropdown) =>
        dropdown
          .addOption("enter-send", tr(lang, "settings.submitBehavior.enterSend"))
          .addOption("mod-enter-send", tr(lang, "settings.submitBehavior.modEnterSend"))
          .setValue(this.host.settings.submitBehavior)
          .onChange(async (value) => {
            this.host.settings.submitBehavior = value as SubmitBehavior;
            await this.host.saveSettings({ rerenderViews: true });
          }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.clearComposer.name"))
      .setDesc(tr(lang, "settings.clearComposer.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.clearComposerAfterSubmit).onChange(async (value) => {
          this.host.settings.clearComposerAfterSubmit = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.focusComposer.name"))
      .setDesc(tr(lang, "settings.focusComposer.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.focusComposerAfterSubmit).onChange(async (value) => {
          this.host.settings.focusComposerAfterSubmit = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.autoScroll.name"))
      .setDesc(tr(lang, "settings.autoScroll.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.autoScrollResponses).onChange(async (value) => {
          this.host.settings.autoScrollResponses = value;
          await this.host.saveSettings();
        }),
      );

    new Setting(containerEl)
      .setName(tr(lang, "settings.selftest.name"))
      .setDesc(tr(lang, "settings.selftest.desc"))
      .addToggle((toggle) =>
        toggle.setValue(this.host.settings.selftestWatchEnabled).onChange(async (value) => {
          this.host.settings.selftestWatchEnabled = value;
          await this.host.saveSettings();
        }),
      );
  }
}

export function findProvider(catalog: AssistantModelCatalog | null | undefined, id: string): AssistantModelProvider | null {
  if (!catalog || !id) {
    return null;
  }
  return catalog.providers.find((provider) => provider.id === id) ?? null;
}

// The Mac-local "claude-local" provider lives on the local gateway, not the main
// (orangepi) one, so it is absent from a main-only catalog and from a stale cache.
// Inject it for display whenever a local endpoint is configured, so it is ALWAYS
// selectable regardless of whether the catalog merge ran/succeeded — chat routing
// (resolveChatEndpoint) already sends this provider to localClaudeEndpoint.
const LOCAL_CLAUDE_PROVIDER: AssistantModelProvider = {
  id: "claude-local",
  label: "本机 Claude Code（Opus）",
  protocol: "claude-wrapper/agent-tools",
  base_url_label: "local CLI · OAuth 订阅",
  models: [
    { id: "opus", label: "Claude Opus（最强）", protocol: "claude-wrapper/agent-tools" },
    { id: "sonnet", label: "Claude Sonnet（均衡）", protocol: "claude-wrapper/agent-tools" },
    { id: "haiku", label: "Claude Haiku（快速）", protocol: "claude-wrapper/agent-tools" },
  ],
};

export function catalogForDisplay(
  catalog: AssistantModelCatalog | null,
  settings: RtimeAssistantSettings,
): AssistantModelCatalog | null {
  if (!settings.localClaudeEndpoint?.trim()) {
    return catalog;
  }
  const providers = catalog?.providers ?? [];
  if (providers.some((provider) => provider.id === "claude-local")) {
    return catalog;
  }
  if (catalog) {
    return { ...catalog, providers: [LOCAL_CLAUDE_PROVIDER, ...providers] };
  }
  return { schema_version: 1, providers: [LOCAL_CLAUDE_PROVIDER] };
}

function modelCapabilityLabel(capabilities: AssistantModelCapabilities | undefined): string {
  if (!capabilities) {
    return "";
  }
  const parts = [];
  parts.push(capabilities.agent_tools ? "tools/code" : "chat-only");
  if (capabilities.vision) {
    parts.push("vision");
  }
  if (capabilities.long_context) {
    parts.push(`${capabilities.long_context} ctx`);
  }
  if (capabilities.thinking) {
    parts.push(`thinking:${capabilities.thinking}`);
  }
  return parts.join(" · ");
}

function pluginUpdateStatusLabel(language: UiLanguage, status: string): string {
  if (!status) {
    return tr(language, "settings.update.statusUnknown");
  }
  if (status === "available-newer") {
    return tr(language, "settings.update.status.availableNewer");
  }
  if (status === "available-build") {
    return tr(language, "settings.update.status.availableBuild");
  }
  if (status === "available-current") {
    return tr(language, "settings.update.status.availableCurrent");
  }
  if (status === "available-older") {
    return tr(language, "settings.update.status.availableOlder");
  }
  if (status === "installed") {
    return tr(language, "settings.update.status.installed");
  }
  if (status.startsWith("failed:")) {
    return `${tr(language, "settings.update.status.failed")} ${status.slice("failed:".length).trim()}`;
  }
  return status;
}

function pluginUpdateStatusTone(status: string): "neutral" | "success" | "warning" | "error" | "accent" {
  if (!status) {
    return "neutral";
  }
  if (status === "available-newer" || status === "available-build") {
    return "accent";
  }
  if (status === "available-current") {
    return "success";
  }
  if (status === "available-older" || status === "installed") {
    return "warning";
  }
  if (status.startsWith("failed:")) {
    return "error";
  }
  return "neutral";
}

function createUpdateMetric(parent: HTMLElement, label: string, value: string): void {
  parent.createDiv({ cls: "rtime-assistant-settings-update-label", text: label });
  const valueEl = parent.createDiv({ cls: "rtime-assistant-settings-update-value", text: value });
  valueEl.setAttribute("title", value);
}

async function runBusyButton(
  button: HTMLButtonElement,
  normalLabel: string,
  busyLabel: string,
  action: () => Promise<void>,
): Promise<void> {
  setActionButtonState(button, busyLabel, true);
  try {
    await action();
  } finally {
    setActionButtonState(button, normalLabel, false);
  }
}

function setActionButtonState(button: HTMLButtonElement, label: string, busy: boolean): void {
  button.disabled = busy;
  button.toggleClass("is-busy", busy);
  button.setAttribute("aria-busy", String(busy));
  button.setAttribute("title", label);
  const labelEl = button.querySelector<HTMLElement>(".rtime-assistant-button-label");
  if (labelEl) {
    labelEl.setText(label);
  }
}
