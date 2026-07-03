// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type {
  AssistantAttachment,
  AttachmentIntakeMode,
  AttachmentKind,
  AttachmentSource,
} from "./types";

export const MAX_TEXT_EXTRACT_CHARS = 12000;
export const MAX_IMAGE_CONTENT_BYTES = 2 * 1024 * 1024;
export const MAX_BINARY_CONTENT_BYTES = 16 * 1024 * 1024;
export const MAX_PREVIEW_BYTES = MAX_IMAGE_CONTENT_BYTES;

export interface FileLike {
  name: string;
  type?: string;
  size: number;
  text?: () => Promise<string>;
  arrayBuffer?: () => Promise<ArrayBuffer>;
}

export function classifyAttachment(name: string, mime = ""): AttachmentKind {
  const lower = name.toLowerCase();
  const type = mime.toLowerCase();
  if (type.startsWith("image/") || /\.(png|jpe?g|webp|gif|bmp|tiff?)$/.test(lower)) return "image";
  if (type === "application/pdf" || lower.endsWith(".pdf")) return "pdf";
  if (/\.(md|markdown)$/.test(lower)) return "markdown";
  if (/\.(csv|tsv)$/.test(lower)) return "csv";
  if (type.startsWith("text/") || /\.(txt|log)$/.test(lower)) return "text";
  if (/\.(xlsx?|numbers)$/.test(lower)) return "spreadsheet";
  if (/\.(docx?|pptx?|pages|key)$/.test(lower)) return "office";
  if (
    type === "application/zip" ||
    type === "application/x-zip-compressed" ||
    type === "application/x-zip" ||
    /\.zip$/.test(lower)
  ) return "archive";
  return "unknown";
}

export function allowedAttachment(name: string, mime = ""): boolean {
  return classifyAttachment(name, mime) !== "unknown";
}

function shouldSendBinaryContent(kind: AttachmentKind): boolean {
  return kind === "pdf" || kind === "office" || kind === "spreadsheet" || kind === "archive";
}

export async function attachmentFromFile(
  file: FileLike,
  source: AttachmentSource,
  intakeMode: AttachmentIntakeMode = "session",
): Promise<AssistantAttachment> {
  const mime = file.type || "";
  const kind = classifyAttachment(file.name, mime);
  const base: AssistantAttachment = {
    id: `att-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`,
    name: file.name,
    kind,
    mime,
    size: file.size,
    source,
    intake_mode: intakeMode,
    temporary: intakeMode === "session",
    status: allowedAttachment(file.name, mime) ? "ready" : "error",
  };
  if (base.status === "error") {
    return { ...base, error: "unsupported attachment type" };
  }
  if ((kind === "markdown" || kind === "text" || kind === "csv") && typeof file.text === "function") {
    const text = await file.text();
    base.extracted_text = text.slice(0, MAX_TEXT_EXTRACT_CHARS);
    base.extracted_chars = text.length;
  }
  if (kind === "image") {
    if (file.size > MAX_IMAGE_CONTENT_BYTES) {
      return { ...base, status: "error", error: "image exceeds inline attachment limit" };
    }
    if (typeof file.arrayBuffer === "function") {
      const mediaType = mime || "image/png";
      const encoded = base64FromArrayBuffer(await file.arrayBuffer());
      base.content_base64 = encoded;
      base.content_encoding = "base64";
      base.content_media_type = mediaType;
      base.preview_data_url = dataUrlFromBase64(encoded, mediaType);
    }
  }
  if (shouldSendBinaryContent(kind)) {
    if (file.size > MAX_BINARY_CONTENT_BYTES) {
      return { ...base, status: "error", error: "file exceeds inline attachment limit" };
    }
    if (typeof file.arrayBuffer === "function") {
      const mediaType = mime || binaryMimeFallback(kind);
      base.content_base64 = base64FromArrayBuffer(await file.arrayBuffer());
      base.content_encoding = "base64";
      base.content_media_type = mediaType;
    }
  }
  return base;
}

function binaryMimeFallback(kind: AttachmentKind): string {
  if (kind === "pdf") return "application/pdf";
  if (kind === "spreadsheet") return "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet";
  if (kind === "archive") return "application/zip";
  return "application/octet-stream";
}

export function base64FromArrayBuffer(buffer: ArrayBuffer): string {
  const bytes = new Uint8Array(buffer);
  let binary = "";
  const chunk = 0x8000;
  for (let offset = 0; offset < bytes.length; offset += chunk) {
    binary += String.fromCharCode(...bytes.slice(offset, offset + chunk));
  }
  return btoa(binary);
}

export function dataUrlFromBase64(encoded: string, mime: string): string {
  return `data:${mime || "application/octet-stream"};base64,${encoded}`;
}

export function attachmentImageUrl(attachment: AssistantAttachment): string | null {
  if (attachment.kind !== "image") {
    return null;
  }
  if (attachment.preview_data_url) {
    return attachment.preview_data_url;
  }
  if (attachment.content_base64) {
    return dataUrlFromBase64(attachment.content_base64, attachment.content_media_type || attachment.mime || "image/png");
  }
  return null;
}

export function displayAttachmentSnapshot(attachment: AssistantAttachment): AssistantAttachment {
  const { content_base64, content_encoding, content_media_type, ...display } = attachment;
  void content_base64;
  void content_encoding;
  void content_media_type;
  return display;
}

export function attachmentLabel(attachment: AssistantAttachment): string {
  const kb = attachment.size / 1024;
  const size = kb < 1024 ? `${Math.max(1, Math.round(kb))} KB` : `${(kb / 1024).toFixed(1)} MB`;
  const scope = attachment.status === "error" ? "不可发送" : "下条消息发送";
  return `${attachment.name} · ${size} · ${scope}`;
}
