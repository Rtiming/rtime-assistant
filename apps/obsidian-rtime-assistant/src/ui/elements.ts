// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { setIcon } from "obsidian";

export function createActionButton(parent: HTMLElement, icon: string, label: string): HTMLButtonElement {
  const button = parent.createEl("button", { cls: "rtime-assistant-icon-button" });
  const iconEl = button.createSpan({ cls: "rtime-assistant-button-icon" });
  setIcon(iconEl, icon);
  button.createSpan({ cls: "rtime-assistant-button-label", text: label });
  button.setAttribute("aria-label", label);
  button.setAttribute("title", label);
  return button;
}
