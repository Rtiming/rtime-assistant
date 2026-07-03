// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { createAssistantClient } from "./services/assistant-client";
import type {
  AssistantContext,
  AssistantPrepareResult,
  AssistantResult,
  RtimeAssistantSettings,
  TaskMode,
} from "./types";

const defaultClient = createAssistantClient();

export async function postAssistantRequest(
  settings: RtimeAssistantSettings,
  context: AssistantContext,
  message: string,
  taskMode: TaskMode,
): Promise<AssistantResult> {
  return defaultClient.postAssistantRequest(settings, context, message, taskMode);
}

export async function prepareAssistantContext(
  settings: RtimeAssistantSettings,
  context: AssistantContext,
): Promise<AssistantPrepareResult> {
  return defaultClient.prepareContext(settings, context);
}

export async function checkBackendHealth(settings: RtimeAssistantSettings): Promise<string> {
  return defaultClient.checkBackendHealth(settings);
}
