// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { TranslationKey } from "./i18n";
import type { RtimeAssistantSettings, TargetModule, TaskMode } from "./types";

export interface ComposerTemplate {
  id: TaskMode;
  taskMode: TaskMode;
  labelKey: TranslationKey;
  promptKey?: TranslationKey;
}

export interface TargetModuleOption {
  id: TargetModule;
  labelKey: TranslationKey;
}

export interface ComposerRouteHint {
  template_id: TaskMode;
  target_module: TargetModule;
  target_folder: string;
}

const COMPOSER_TEMPLATES: ComposerTemplate[] = [
  { id: "ask", taskMode: "ask", labelKey: "task.ask" },
  { id: "summarize", taskMode: "summarize", labelKey: "task.summarize", promptKey: "prompt.askNote" },
  { id: "explain", taskMode: "explain", labelKey: "task.explain", promptKey: "prompt.explainSelection" },
  { id: "related", taskMode: "related", labelKey: "task.related", promptKey: "prompt.related" },
  {
    id: "citation-review",
    taskMode: "citation-review",
    labelKey: "task.citation-review",
    promptKey: "prompt.citationReview",
  },
];

const TARGET_MODULE_OPTIONS: TargetModuleOption[] = [
  { id: "auto", labelKey: "module.auto" },
  { id: "brain", labelKey: "module.brain" },
  { id: "literature", labelKey: "module.literature" },
  { id: "project", labelKey: "module.project" },
  { id: "runtime", labelKey: "module.runtime" },
];

export function getComposerTemplates(): readonly ComposerTemplate[] {
  return COMPOSER_TEMPLATES;
}

export function getTargetModuleOptions(): readonly TargetModuleOption[] {
  return TARGET_MODULE_OPTIONS;
}

export function getPromptKeyForTemplate(taskMode: TaskMode): TranslationKey | undefined {
  return COMPOSER_TEMPLATES.find((item) => item.taskMode === taskMode)?.promptKey;
}

export function buildComposerRouteHint(
  settings: Pick<RtimeAssistantSettings, "targetModule" | "targetFolder">,
  taskMode: TaskMode,
): ComposerRouteHint {
  return {
    template_id: taskMode,
    target_module: settings.targetModule,
    target_folder: settings.targetFolder.trim(),
  };
}

export function folderSuggestionsFromPaths(
  activePath: string,
  markdownPaths: string[],
  limit = 80,
): string[] {
  const suggestions = new Set<string>();
  const activeFolder = folderOf(activePath);
  if (activeFolder) {
    suggestions.add(activeFolder);
  }

  for (const filePath of markdownPaths) {
    const parts = filePath.split("/").filter(Boolean);
    if (parts.length > 1) {
      suggestions.add(parts[0]);
    }
    if (parts.length > 2) {
      suggestions.add(`${parts[0]}/${parts[1]}`);
    }
    if (suggestions.size >= limit) {
      break;
    }
  }

  return Array.from(suggestions).sort((a, b) => {
    if (a === activeFolder) return -1;
    if (b === activeFolder) return 1;
    return a.localeCompare(b);
  });
}

function folderOf(path: string): string {
  const index = path.lastIndexOf("/");
  return index > 0 ? path.slice(0, index) : "";
}
