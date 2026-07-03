// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
/**
 * Node-direct HTTP transport.
 *
 * Obsidian's `requestUrl` and `window.fetch` both ride Chromium's network
 * stack, which obeys the macOS system proxy. A local proxy typically has no
 * rule for Tailnet/CGNAT ranges, so requests to the gateway come back as
 * HTTP 502 from the proxy. Node sockets connect directly and ignore the
 * system proxy entirely, so on desktop we prefer this transport for both
 * regular and SSE requests.
 */

export interface TransportRequest {
  url: string;
  method: string;
  headers?: Record<string, string>;
  body?: string;
  timeoutMs: number;
  /** Aborting destroys the socket — the gateway sees the disconnect and frees its slot. */
  signal?: AbortSignal;
}

export interface TransportResponse {
  status: number;
  text: string;
}

interface NodeHttpResponseLike {
  statusCode?: number;
  setEncoding(encoding: string): void;
  on(event: "data", listener: (chunk: string) => void): void;
  on(event: "end", listener: () => void): void;
  on(event: "error", listener: (error: Error) => void): void;
}

interface NodeHttpRequestLike {
  on(event: "error", listener: (error: Error) => void): void;
  destroy(error?: Error): void;
  end(body?: string): void;
}

interface NodeHttpModuleLike {
  request(
    url: string,
    options: { method: string; headers?: Record<string, string> },
    callback: (response: NodeHttpResponseLike) => void,
  ): NodeHttpRequestLike;
}

function nodeRequire<T>(name: string): T | null {
  try {
    const candidates: unknown[] = [
      typeof require === "function" ? require : undefined,
      typeof window !== "undefined"
        ? (window as unknown as { require?: unknown }).require
        : undefined,
    ];
    for (const candidate of candidates) {
      if (typeof candidate === "function") {
        const dynamicRequire = candidate as (id: string) => unknown;
        return dynamicRequire(name) as T;
      }
    }
  } catch {
    // fall through
  }
  return null;
}

function loadHttpModule(url: string): NodeHttpModuleLike | null {
  if (/^https:\/\//i.test(url)) {
    return nodeRequire<NodeHttpModuleLike>("https");
  }
  if (/^http:\/\//i.test(url)) {
    return nodeRequire<NodeHttpModuleLike>("http");
  }
  return null;
}

export function supportsNodeTransport(url: string): boolean {
  return loadHttpModule(url) !== null;
}

export function nodeHttpRequest(request: TransportRequest): Promise<TransportResponse> {
  return nodeHttpExchange(request, null);
}

/**
 * Streaming variant: `onChunk` receives decoded UTF-8 text as it arrives
 * (only for status < 400; error bodies are buffered into the response so the
 * caller can surface them). Resolves when the response ends.
 */
export function nodeHttpStream(
  request: TransportRequest,
  onChunk: (text: string) => void,
): Promise<TransportResponse> {
  return nodeHttpExchange(request, onChunk);
}

function nodeHttpExchange(
  request: TransportRequest,
  onChunk: ((text: string) => void) | null,
): Promise<TransportResponse> {
  const module = loadHttpModule(request.url);
  if (!module) {
    return Promise.reject(new Error("Node HTTP transport is not available"));
  }

  if (request.signal?.aborted) {
    return Promise.reject(new Error("Request cancelled"));
  }

  return new Promise((resolve, reject) => {
    let settled = false;
    let deadline: ReturnType<typeof setTimeout> | undefined;
    const finish = (callback: () => void): void => {
      if (!settled) {
        settled = true;
        if (deadline !== undefined) {
          clearTimeout(deadline);
        }
        callback();
      }
    };

    const req = module.request(
      request.url,
      { method: request.method, headers: request.headers },
      (response) => {
        response.setEncoding("utf8");
        const status = response.statusCode ?? 0;
        const streaming = onChunk !== null && status < 400;
        const collected: string[] = [];
        response.on("data", (chunk) => {
          if (!streaming) {
            collected.push(chunk);
            return;
          }
          try {
            onChunk?.(chunk);
          } catch (error) {
            req.destroy();
            finish(() => reject(error instanceof Error ? error : new Error(String(error))));
          }
        });
        response.on("end", () => {
          finish(() => resolve({ status, text: collected.join("") }));
        });
        response.on("error", (error) => {
          finish(() => reject(error));
        });
      },
    );

    deadline = setTimeout(() => {
      const error = new Error(`Request timed out after ${request.timeoutMs} ms`);
      req.destroy(error);
      finish(() => reject(error));
    }, Math.max(1, request.timeoutMs));

    req.on("error", (error) => {
      finish(() => reject(error));
    });
    request.signal?.addEventListener(
      "abort",
      () => {
        const error = new Error("Request cancelled");
        req.destroy(error);
        finish(() => reject(error));
      },
      { once: true },
    );
    req.end(request.body ?? undefined);
  });
}
