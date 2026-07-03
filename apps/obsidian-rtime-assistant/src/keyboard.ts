// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { RtimeAssistantSettings } from "./types";

export interface ComposerKeyboardEvent {
  key: string;
  isComposing?: boolean;
  shiftKey?: boolean;
  metaKey?: boolean;
  ctrlKey?: boolean;
  altKey?: boolean;
}

export function shouldSubmitFromComposerEvent(
  settings: Pick<RtimeAssistantSettings, "submitBehavior">,
  event: ComposerKeyboardEvent,
): boolean {
  if (event.key !== "Enter" || event.isComposing || event.shiftKey) {
    return false;
  }
  if (event.metaKey || event.ctrlKey) {
    return true;
  }
  if (event.altKey) {
    return false;
  }
  return settings.submitBehavior === "enter-send";
}
