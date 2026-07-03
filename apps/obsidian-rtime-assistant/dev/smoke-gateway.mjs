// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import http from "node:http";

const host = process.env.RTIME_OBSIDIAN_SMOKE_HOST ?? "127.0.0.1";
const port = Number.parseInt(process.env.RTIME_OBSIDIAN_SMOKE_PORT ?? "8765", 10);

function readBody(request) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    request.on("data", (chunk) => chunks.push(chunk));
    request.on("end", () => resolve(Buffer.concat(chunks).toString("utf8")));
    request.on("error", reject);
  });
}

function sendJson(response, status, payload) {
  response.writeHead(status, {
    "content-type": "application/json; charset=utf-8",
    "cache-control": "no-store",
  });
  response.end(JSON.stringify(payload, null, 2));
}

function buildAnswer(body) {
  const context = body.context ?? {};
  const options = body.options ?? {};
  const activeFile = context.active_file;
  const filePath = activeFile?.path ?? "no-active-note";
  const selectionChars = context.selection?.chars ?? 0;
  const noteChars = context.note?.chars ?? 0;
  const taskMode = options.task_mode ?? "ask";
  const targetModule = options.target_module ?? "auto";
  const targetFolder = options.target_folder ?? "";
  const language = options.ui_language ?? "zh-CN";

  if (language === "en") {
    return {
      answer:
        `Smoke gateway is connected. I received task "${taskMode}" from Obsidian, active note "${filePath}", ` +
        `${selectionChars} selected characters, ${noteChars} note characters, target module "${targetModule}", ` +
        `and target folder "${targetFolder || "none"}". ` +
        "This validates the plugin-to-local-gateway path; retrieval and model execution are still mocked.",
      sources: [
        {
          title: "Obsidian active note payload",
          path: filePath,
          kind: "smoke-context",
          snippet: `selection=${selectionChars}, note=${noteChars}, module=${targetModule}, folder=${targetFolder || "none"}`,
        },
        {
          title: "rtime-assistant smoke gateway",
          kind: "local-health",
          url: `http://${host}:${port}/healthz`,
        },
      ],
    };
  }

  return {
    answer:
      `Smoke gateway 已连通。我收到了 Obsidian 发来的 "${taskMode}" 任务，当前笔记为 "${filePath}"，` +
      `选区 ${selectionChars} 字符，笔记正文 ${noteChars} 字符，目标模块 "${targetModule}"，` +
      `目标文件夹 "${targetFolder || "未指定"}"。` +
      "这证明插件到本地 gateway 的链路已经通了；真实检索和模型执行还没有接入。",
    sources: [
      {
        title: "Obsidian 当前笔记 payload",
        path: filePath,
        kind: "smoke-context",
        snippet: `selection=${selectionChars}, note=${noteChars}, module=${targetModule}, folder=${targetFolder || "none"}`,
      },
      {
        title: "rtime-assistant smoke gateway",
        kind: "local-health",
        url: `http://${host}:${port}/healthz`,
      },
    ],
  };
}

function buildPrepare(body) {
  const context = body.context ?? {};
  const activeFile = context.active_file;
  return {
    ok: true,
    schema_version: 1,
    prepare_id: `prep-smoke-${Date.now()}`,
    cache_ttl_seconds: 180,
    dur_ms: 1,
    unlock_count: activeFile?.path ? 1 : 0,
    related_count: 0,
    memory_referenced_count: 0,
    model_catalog_cached: true,
    model_provider_count: 1,
    unlocks: activeFile?.path ? [{ label: "active", path: activeFile.path }] : [],
  };
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${port}`);

  if (request.method === "GET" && url.pathname === "/healthz") {
    sendJson(response, 200, {
      ok: true,
      service: "rtime-obsidian-smoke-gateway",
      port,
    });
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/obsidian/chat") {
    try {
      const rawBody = await readBody(request);
      const body = rawBody.trim() ? JSON.parse(rawBody) : {};
      sendJson(response, 200, buildAnswer(body));
    } catch (error) {
      sendJson(response, 400, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/obsidian/prepare") {
    try {
      const rawBody = await readBody(request);
      const body = rawBody.trim() ? JSON.parse(rawBody) : {};
      sendJson(response, 200, buildPrepare(body));
    } catch (error) {
      sendJson(response, 400, {
        ok: false,
        error: error instanceof Error ? error.message : String(error),
      });
    }
    return;
  }

  sendJson(response, 404, {
    ok: false,
    error: "not_found",
  });
});

server.listen(port, host, () => {
  console.log(`rtime obsidian smoke gateway listening on http://${host}:${port}`);
});
