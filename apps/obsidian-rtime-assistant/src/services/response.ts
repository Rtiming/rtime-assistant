// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { AssistantSource, MemoryEvents, StreamTrace } from "../types";

export function parseAssistantResult(data: unknown): { answer: string; sources: AssistantSource[]; trace?: StreamTrace; memoryEvents?: MemoryEvents } {
  return {
    answer: extractAnswer(data),
    sources: extractSources(data),
    trace: extractTrace(data),
    memoryEvents: extractMemoryEvents(data),
  };
}

export function parseJsonOrText(text: string): unknown | string {
  if (!text.trim()) {
    return "";
  }
  try {
    return JSON.parse(text) as unknown;
  } catch {
    return text;
  }
}

function extractAnswer(data: unknown): string {
  if (typeof data === "string") {
    return data;
  }
  if (!isRecord(data)) {
    return JSON.stringify(data, null, 2);
  }

  const direct =
    asString(data.answer) ??
    asString(data.text) ??
    asString(data.response) ??
    asString(data.content);
  if (direct) {
    return direct;
  }

  const message = data.message;
  if (typeof message === "string") {
    return message;
  }
  if (isRecord(message)) {
    const content = asString(message.content);
    if (content) {
      return content;
    }
  }

  const choices = data.choices;
  if (Array.isArray(choices) && choices.length > 0 && isRecord(choices[0])) {
    const choice = choices[0];
    if (isRecord(choice.message)) {
      const content = asString(choice.message.content);
      if (content) {
        return content;
      }
    }
    const text = asString(choice.text);
    if (text) {
      return text;
    }
  }

  return JSON.stringify(data, null, 2);
}

function extractSources(data: unknown): AssistantSource[] {
  if (!isRecord(data)) {
    return [];
  }
  // Cap the merged list once — slicing each input to 12 first would drop every
  // citation whenever there are already 12 sources.
  return normalizeSources(data.sources).concat(normalizeSources(data.citations)).slice(0, 12);
}

function extractTrace(data: unknown): StreamTrace | undefined {
  if (!isRecord(data) || !isRecord(data.trace)) {
    return undefined;
  }
  return { gateway: data.trace as Record<string, number | string | null> };
}

function extractMemoryEvents(data: unknown): MemoryEvents | undefined {
  if (!isRecord(data) || !isRecord(data.memory_events)) {
    return undefined;
  }
  const raw = data.memory_events;
  return {
    referenced_count: asNumber(raw.referenced_count) ?? 0,
    candidate_count: asNumber(raw.candidate_count) ?? 0,
    auto_merged_count: asNumber(raw.auto_merged_count) ?? 0,
    review_count: asNumber(raw.review_count) ?? 0,
    disabled: raw.disabled === true,
    summary: asString(raw.summary) ?? undefined,
  };
}

function normalizeSources(value: unknown): AssistantSource[] {
  if (!Array.isArray(value)) {
    return [];
  }
  return value.map(normalizeSource).filter((item): item is AssistantSource => item !== null);
}

function normalizeSource(value: unknown): AssistantSource | null {
  if (typeof value === "string" && value.trim()) {
    return { title: value };
  }
  if (!isRecord(value)) {
    return null;
  }
  const source: AssistantSource = {};
  const title = asString(value.title) ?? asString(value.name);
  const path = asString(value.path) ?? asString(value.file);
  const line = asNumber(value.line);
  const page = asNumber(value.page);
  const url = asString(value.url);
  const kind = asString(value.kind) ?? asString(value.type);
  const snippet = asString(value.snippet) ?? asString(value.text);
  if (title) source.title = title;
  if (path) source.path = path;
  if (line !== undefined) source.line = line;
  if (page !== undefined) source.page = page;
  if (url) source.url = url;
  if (kind) source.kind = kind;
  if (snippet) source.snippet = snippet;
  return Object.keys(source).length ? source : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asString(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value : null;
}

function asNumber(value: unknown): number | undefined {
  return typeof value === "number" && Number.isFinite(value) ? value : undefined;
}
