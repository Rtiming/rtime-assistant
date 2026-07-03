// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import assert from "node:assert/strict";

const DEFAULTS = {
  endpoint: process.env.RTIME_OBSIDIAN_SIM_ENDPOINT ?? "http://127.0.0.1:8765/api/obsidian/chat",
  template: process.env.RTIME_OBSIDIAN_SIM_TEMPLATE ?? "related",
  module: process.env.RTIME_OBSIDIAN_SIM_MODULE ?? "literature",
  folder: process.env.RTIME_OBSIDIAN_SIM_FOLDER ?? "knowledge/research",
  language: process.env.RTIME_OBSIDIAN_SIM_LANGUAGE ?? "zh-CN",
};

const VALID_TEMPLATES = new Set(["ask", "summarize", "explain", "related", "citation-review"]);
const VALID_MODULES = new Set(["auto", "brain", "literature", "project", "runtime"]);

function parseArgs(argv) {
  const options = { ...DEFAULTS, dryRun: false };
  for (let index = 0; index < argv.length; index += 1) {
    const arg = argv[index];
    if (arg === "--dry-run") {
      options.dryRun = true;
      continue;
    }
    const next = argv[index + 1];
    if (!next) {
      throw new Error(`${arg} requires a value`);
    }
    index += 1;
    if (arg === "--endpoint") options.endpoint = next;
    else if (arg === "--template") options.template = next;
    else if (arg === "--module") options.module = next;
    else if (arg === "--folder") options.folder = next;
    else if (arg === "--language") options.language = next;
    else throw new Error(`unknown argument: ${arg}`);
  }
  return options;
}

function buildPayload(options) {
  if (!VALID_TEMPLATES.has(options.template)) {
    throw new Error(`invalid template: ${options.template}`);
  }
  if (!VALID_MODULES.has(options.module)) {
    throw new Error(`invalid module: ${options.module}`);
  }

  return {
    schema_version: 1,
    entry: "obsidian",
    message: `模拟 ${options.template} 模板请求`,
    context: {
      vault: { name: "brain" },
      active_file: {
        path: `${options.folder || "Inbox"}/composer-simulation.md`,
        basename: "composer-simulation",
        extension: "md",
        size: 1024,
        ctime: 0,
        mtime: 0,
      },
      selection: {
        text: "用于测试模板条、模块选择和文件夹路由 hint 的选中文本。",
        chars: 28,
      },
      note: {
        text: "# Composer Simulation\n\n这个请求用于验证 Obsidian composer contract。",
        chars: 61,
        truncated: false,
      },
      metadata: {
        headings: [{ heading: "Composer Simulation", level: 1, line: 0 }],
        tags: [{ tag: "#rtime-assistant", line: 2 }],
        links: [{ link: "brain-library-index", line: 3 }],
      },
      requested_mode: "current-note",
      local_time: new Date().toISOString(),
    },
    options: {
      context_mode: "current-note",
      task_mode: options.template,
      template_id: options.template,
      target_module: options.module,
      target_folder: options.folder,
      ui_language: options.language,
      include_selection: true,
      include_active_note_body: true,
    },
  };
}

async function main() {
  const options = parseArgs(process.argv.slice(2));
  const payload = buildPayload(options);

  if (options.dryRun) {
    console.log(JSON.stringify({ endpoint: options.endpoint, payload }, null, 2));
    return;
  }

  const response = await fetch(options.endpoint, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
  });
  const text = await response.text();
  let data = text;
  try {
    data = JSON.parse(text);
  } catch {
    // Keep plain text responses inspectable.
  }

  assert.equal(response.ok, true, `HTTP ${response.status}: ${text.slice(0, 500)}`);
  assert.equal(typeof data, "object", "response should be JSON object");
  assert.equal(typeof data.answer, "string", "response.answer should be a string");

  console.log(JSON.stringify({
    ok: true,
    status: response.status,
    endpoint: options.endpoint,
    request: {
      template_id: payload.options.template_id,
      target_module: payload.options.target_module,
      target_folder: payload.options.target_folder,
    },
    answer_preview: data.answer.slice(0, 160),
    sources_count: Array.isArray(data.sources) ? data.sources.length : 0,
  }, null, 2));
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
