// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { AssistantSource, ChatMessage } from "../types";

export function iconForRole(role: ChatMessage["role"]): string {
  if (role === "user") return "user";
  if (role === "assistant") return "sparkles";
  return "shield";
}

export function iconForSource(source: AssistantSource): string {
  if (source.kind?.includes("citation") || source.kind?.includes("cite")) return "quote";
  if (source.url) return "link";
  if (source.page !== undefined) return "book-open";
  return "file-text";
}

export function formatSource(source: AssistantSource): string {
  const parts = [
    source.kind,
    source.path,
    source.page !== undefined ? `page ${source.page}` : undefined,
    source.line !== undefined ? `line ${source.line}` : undefined,
    source.url,
    source.snippet,
  ].filter((part): part is string => Boolean(part));
  return parts.join(" | ");
}
