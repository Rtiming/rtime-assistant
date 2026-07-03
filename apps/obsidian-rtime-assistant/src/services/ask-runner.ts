// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { AskOptions } from "../main";
import { tr } from "../i18n";
import type { AssistantResult, StreamTrace, TaskMode, UiLanguage } from "../types";
import { isBusyError, isStreamServerError, type AssistantStreamHandlers } from "./assistant-client";

export type StatusTone = "idle" | "ready" | "working" | "error";

/** A user-initiated Stop (or view switch) aborts the socket; the transport
 * rejects with "Request cancelled" (Node) or an AbortError (fetch). */
export function isCancelledError(error: unknown): boolean {
  if (!(error instanceof Error)) {
    return false;
  }
  return error.name === "AbortError" || /request cancelled|aborted/i.test(error.message);
}

/** DOM-free seam the view implements, so the ask / stream / busy-retry / cancel /
 * fallback orchestration can be unit-tested without an ItemView or a real DOM. */
export interface AskRunnerHost {
  readonly streamEnabled: boolean;
  readonly lang: UiLanguage;
  readonly taskMode: TaskMode;
  isStopRequested(): boolean;
  ask(prompt: string, taskMode: TaskMode, askOptions: AskOptions): Promise<AssistantResult>;
  stream(
    prompt: string,
    taskMode: TaskMode,
    handlers: AssistantStreamHandlers,
    askOptions: AskOptions,
  ): Promise<AssistantResult>;
  setStatus(text: string, tone: StatusTone): void;
  setActivity(text: string): void;
  appendDelta(text: string): void;
  setTrace(trace: StreamTrace): void;
  /** Clear any partial answer + live card before a retry/fallback restarts it. */
  resetForRetry(): void;
  sleep(ms: number): Promise<void>;
}

const BUSY_RETRY_DELAYS = [2000, 4000, 6000, 8000, 10000];

/** Gateway busy (503) means "wait your turn", not an error — retry with backoff.
 * A user Stop short-circuits the loop. Streaming failures fall back to a
 * non-streaming request, EXCEPT busy / stream-server / cancellation errors,
 * which propagate so the caller handles them. */
export async function runAsk(
  prompt: string,
  askOptions: AskOptions,
  host: AskRunnerHost,
): Promise<AssistantResult> {
  for (let attempt = 0; ; attempt += 1) {
    if (host.isStopRequested()) {
      throw new Error("Request cancelled");
    }
    try {
      return host.streamEnabled
        ? await runStream(prompt, askOptions, host)
        : await host.ask(prompt, host.taskMode, askOptions);
    } catch (error) {
      if (!isBusyError(error) || attempt >= BUSY_RETRY_DELAYS.length) {
        throw error;
      }
      host.setStatus(tr(host.lang, "status.busyRetry"), "working");
      // The retry restarts the full answer, so any partial text is cleared —
      // say so instead of letting it look like a lost response.
      host.setActivity(tr(host.lang, "activity.retrying"));
      host.resetForRetry();
      await host.sleep(BUSY_RETRY_DELAYS[attempt]);
      if (host.isStopRequested()) {
        throw new Error("Request cancelled");
      }
    }
  }
}

async function runStream(
  prompt: string,
  askOptions: AskOptions,
  host: AskRunnerHost,
): Promise<AssistantResult> {
  try {
    return await host.stream(
      prompt,
      host.taskMode,
      {
        onStatus: (text) => {
          host.setStatus(text, "working");
          host.setActivity(text);
        },
        onApprovalRequest: (text) => {
          host.setStatus(text, "working");
          host.setActivity(text);
        },
        onDelta: (text) => host.appendDelta(text),
        onTrace: (trace) => host.setTrace(trace),
      },
      askOptions,
    );
  } catch (error) {
    if (isBusyError(error)) {
      throw error; // busy is handled by runAsk; a non-stream fallback would just 503 again
    }
    if (isStreamServerError(error)) {
      throw error;
    }
    if (isCancelledError(error) || host.isStopRequested()) {
      throw error; // user Stop / view switch: honor the cancel, don't re-fire a fresh request
    }
    console.warn("Rtime Assistant streaming failed; falling back to non-streaming request", error);
    host.setStatus(tr(host.lang, "status.fallback"), "working");
    host.setActivity(tr(host.lang, "status.fallback"));
    host.resetForRetry();
    return host.ask(prompt, host.taskMode, askOptions);
  }
}
