// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { requestUrl, type RequestUrlParam, type RequestUrlResponse } from "obsidian";
import { nodeHttpRequest, supportsNodeTransport } from "./transport";

export interface HttpRequestOptions {
  url: string;
  method: string;
  contentType?: string;
  headers?: Record<string, string>;
  body?: string | ArrayBuffer;
  timeoutMs: number;
  retryCount: number;
  retryDelayMs: number;
}

export interface BasicHttpResponse {
  status: number;
  text: string;
  json?: unknown;
}

export async function requestWithRetry(options: HttpRequestOptions): Promise<BasicHttpResponse> {
  const attempts = Math.max(1, Math.floor(options.retryCount) + 1);
  let lastError: unknown = null;

  for (let attempt = 0; attempt < attempts; attempt += 1) {
    try {
      const response = await requestOnce(options);
      if (!isRetryableStatus(response.status) || attempt === attempts - 1) {
        return response;
      }
      await sleep(backoffDelayMs(options.retryDelayMs, attempt));
    } catch (error) {
      lastError = error;
      if (attempt === attempts - 1) {
        break;
      }
      await sleep(backoffDelayMs(options.retryDelayMs, attempt));
    }
  }

  throw lastError instanceof Error ? lastError : new Error(String(lastError ?? "HTTP request failed"));
}

async function requestOnce(options: HttpRequestOptions): Promise<BasicHttpResponse> {
  const url = normalizeLocalhostUrl(options.url);

  // Prefer the proxy-immune Node transport on desktop (string bodies only).
  if ((options.body === undefined || typeof options.body === "string") && supportsNodeTransport(url)) {
    const headers: Record<string, string> = { ...(options.headers ?? {}) };
    if (options.contentType) {
      headers["Content-Type"] = options.contentType;
    }
    return nodeHttpRequest({
      url,
      method: options.method,
      headers,
      body: options.body,
      timeoutMs: options.timeoutMs,
    });
  }

  const request: RequestUrlParam = {
    method: options.method,
    url,
    contentType: options.contentType,
    headers: options.headers,
    body: options.body,
    throw: false,
  };
  const response = await withTimeout(requestUrl(request), options.timeoutMs);
  return { status: response.status, text: response.text, json: safeJson(response) };
}

function safeJson(response: RequestUrlResponse): unknown {
  try {
    return response.json;
  } catch {
    return undefined;
  }
}

async function withTimeout<T>(promise: Promise<T>, timeoutMs: number): Promise<T> {
  let timeoutHandle: ReturnType<typeof setTimeout> | undefined;
  const timeout = new Promise<never>((_, reject) => {
    timeoutHandle = setTimeout(() => {
      reject(new Error(`Request timed out after ${timeoutMs} ms`));
    }, timeoutMs);
  });
  try {
    return await Promise.race([promise, timeout]);
  } finally {
    if (timeoutHandle !== undefined) {
      clearTimeout(timeoutHandle);
    }
  }
}

export function normalizeLocalhostUrl(url: string): string {
  return url.replace(/^http:\/\/localhost(?=[:/]|$)/, "http://127.0.0.1");
}

function isRetryableStatus(status: number): boolean {
  return status === 408 || status === 429 || (status >= 500 && status <= 599);
}

function backoffDelayMs(baseDelayMs: number, attempt: number): number {
  const base = Math.max(0, Math.floor(baseDelayMs));
  if (base === 0) {
    return 0;
  }
  return Math.min(base * 2 ** attempt, 3000);
}

function sleep(delayMs: number): Promise<void> {
  if (delayMs <= 0) {
    return Promise.resolve();
  }
  return new Promise((resolve) => {
    setTimeout(resolve, delayMs);
  });
}
