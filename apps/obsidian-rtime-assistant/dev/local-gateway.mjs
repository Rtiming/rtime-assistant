// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { existsSync, statSync } from "node:fs";
import { mkdtemp, readFile, rm, writeFile } from "node:fs/promises";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { spawn } from "node:child_process";

const host = process.env.RTIME_OBSIDIAN_HOST ?? "127.0.0.1";
const port = Number.parseInt(process.env.RTIME_OBSIDIAN_PORT ?? "8765", 10);
const runner = process.env.RTIME_OBSIDIAN_RUNNER ?? "auto";
const runnerTimeoutMs = Number.parseInt(process.env.RTIME_OBSIDIAN_RUNNER_TIMEOUT_MS ?? "180000", 10);
const maxPromptChars = Number.parseInt(process.env.RTIME_OBSIDIAN_MAX_PROMPT_CHARS ?? "24000", 10);

const localWorkdir =
  process.env.RTIME_OBSIDIAN_WORKDIR ??
  firstPresentPath([
    path.join(os.homedir(), "OrangePi-Store", "sync", "brain"),
    path.join(os.homedir(), "Desktop", "brain-notes"),
    process.cwd(),
  ]);

const remoteNode = process.env.RTIME_OBSIDIAN_REMOTE_NODE ?? "orangepi";
const remoteWorkdir = process.env.RTIME_OBSIDIAN_REMOTE_WORKDIR ?? "/mnt/brain";
const remoteCli = process.env.RTIME_OBSIDIAN_REMOTE_CLI ?? "~/.local/bin/claude-kimi";
const remoteSsh = process.env.RTIME_OBSIDIAN_RTIME_SSH ?? path.join(os.homedir(), ".ai-skills", "rtime-remote", "scripts", "rtime-ssh");
const defaultPermissionMode = process.env.RTIME_OBSIDIAN_PERMISSION_MODE ?? "default";
const model = process.env.RTIME_OBSIDIAN_MODEL ?? "";
const codexModel = process.env.RTIME_OBSIDIAN_CODEX_MODEL ?? "";

// --- claude-local: Mac 本机 Claude Code(Opus),用订阅 OAuth token,只读检索 brain ---
const localClaudeModel = process.env.RTIME_OBSIDIAN_LOCAL_CLAUDE_MODEL ?? "opus";
const localClaudeEffort = process.env.RTIME_OBSIDIAN_LOCAL_CLAUDE_EFFORT ?? "high";
const localClaudeCwd = process.env.RTIME_OBSIDIAN_LOCAL_CLAUDE_CWD ?? os.tmpdir();
// claude-local 用更短的超时(交互式问答):brain MCP 偶发卡住时快速失败,不空等满 180s
const localClaudeTimeoutMs = Number.parseInt(process.env.RTIME_OBSIDIAN_LOCAL_CLAUDE_TIMEOUT_MS ?? "90000", 10);
const localClaudeMcpConfig = process.env.RTIME_OBSIDIAN_LOCAL_CLAUDE_MCP_CONFIG ?? "";
const oauthTokenFile =
  process.env.RTIME_OBSIDIAN_OAUTH_TOKEN_FILE ??
  path.join(os.homedir(), ".config", "rtime", "obsidian-gateway.env");
// brain MCP 的 ssh 连接复用:ControlMaster 多路复用可省 ~0.9s SSH 握手,但 orangepi 网关重启后
// 持久化的主连接会变陈旧、令新会话挂起(实测踩过 180s 挂起);省的远小于这个风险,故【默认关闭】,
// 仅 RTIME_OBSIDIAN_SSH_CONTROL_MASTER=1 时 opt-in。
const sshControlMaster = (process.env.RTIME_OBSIDIAN_SSH_CONTROL_MASTER ?? "0") === "1";
const SSH_CONTROL_PATH = path.join(os.homedir(), ".ssh", "cm-rtime-obsidian-%C");
const SSH_CONTROL_PERSIST = process.env.RTIME_OBSIDIAN_SSH_CONTROL_PERSIST ?? "300";
const EFFORT_LEVELS = new Set(["low", "medium", "high", "xhigh", "max"]);
// 只读门:--disallowedTools 黑名单硬禁写/Bash(任何权限模式都不可覆盖)。
// 实测:--tools 收窄可用集会把 MCP 工具一并排除、导致 brain 检索失效,故不用 --tools,只用黑名单 + dontAsk。
const READONLY_DISALLOWED = "Bash,Edit,Write,MultiEdit,NotebookEdit,WebFetch,WebSearch";
// 编辑模式硬门:默认只读;请求里的 edit_mode 不被信任,仅服务端显式 RTIME_OBSIDIAN_ALLOW_EDIT=1 才放开写。
const allowEditMode = (process.env.RTIME_OBSIDIAN_ALLOW_EDIT ?? "0") === "1";
const PERMISSION_MODES = new Set(["dontAsk", "default", "acceptEdits", "plan", "bypassPermissions", "auto"]);
// 回传客户端的错误信息里抹掉订阅 token,避免 stderr 泄漏凭据。
function redactSecrets(s) {
  return String(s == null ? "" : s).replace(/sk-ant-[A-Za-z0-9_-]{8,}/g, "sk-ant-***");
}

function firstPresentPath(candidates) {
  for (const candidate of candidates) {
    if (candidate && existsSync(candidate)) {
      return candidate;
    }
  }
  return process.cwd();
}

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
    "access-control-allow-origin": "app://obsidian.md",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type, accept",
  });
  response.end(JSON.stringify(payload, null, 2));
}

function isRecord(value) {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function asText(value) {
  return typeof value === "string" ? value : "";
}

function truncate(text, limit) {
  if (!text || text.length <= limit) {
    return text ?? "";
  }
  return `${text.slice(0, limit)}\n\n[truncated ${text.length - limit} chars]`;
}

function taskInstruction(taskMode, language) {
  const chinese = language !== "en";
  const instructions = {
    ask: chinese
      ? "直接回答用户问题；如果上下文不足，明确说明还缺什么。"
      : "Answer the user's question directly. If context is insufficient, say what is missing.",
    summarize: chinese
      ? "总结当前笔记，输出关键判断、待确认问题和下一步动作。"
      : "Summarize the active note with decisions, open questions, and next actions.",
    explain: chinese
      ? "解释选区或当前笔记的核心论点，优先使用用户给出的笔记上下文。"
      : "Explain the selection or the note's main argument using the provided context first.",
    related: chinese
      ? "查找或推断相关材料方向；没有真实检索证据时不要编造路径。"
      : "Find or infer related material directions. Do not invent paths without evidence.",
    "citation-review": chinese
      ? "审阅引文覆盖与来源可追溯性；无法验证时列出具体缺口。"
      : "Review citation coverage and source traceability. List concrete gaps when unverifiable.",
  };
  return instructions[taskMode] ?? instructions.ask;
}

function formatMetadata(metadata) {
  if (!isRecord(metadata)) {
    return "none";
  }
  const headings = Array.isArray(metadata.headings)
    ? metadata.headings.map((item) => `${"#".repeat(Number(item.level) || 1)} ${item.heading}`).slice(0, 20)
    : [];
  const tags = Array.isArray(metadata.tags)
    ? metadata.tags.map((item) => item.tag).filter(Boolean).slice(0, 20)
    : [];
  const links = Array.isArray(metadata.links)
    ? metadata.links.map((item) => item.link).filter(Boolean).slice(0, 20)
    : [];
  return [
    headings.length ? `Headings:\n${headings.join("\n")}` : "",
    tags.length ? `Tags: ${tags.join(", ")}` : "",
    links.length ? `Links: ${links.join(", ")}` : "",
  ]
    .filter(Boolean)
    .join("\n");
}

function buildPrompt(body) {
  const context = isRecord(body.context) ? body.context : {};
  const options = isRecord(body.options) ? body.options : {};
  const language = options.ui_language === "en" ? "en" : "zh-CN";
  const taskMode = asText(options.task_mode) || "ask";
  const templateId = asText(options.template_id) || taskMode;
  const targetModule = asText(options.target_module) || "auto";
  const targetFolder = asText(options.target_folder);
  const activeFile = isRecord(context.active_file) ? context.active_file : null;
  const selection = isRecord(context.selection) ? context.selection : null;
  const note = isRecord(context.note) ? context.note : null;
  const filePath = asText(activeFile?.path) || "no-active-note";
  const noteText = asText(note?.text);
  const selectionText = asText(selection?.text);
  const userMessage = asText(body.message).trim();

  const prompt = [
    "You are Rtime Assistant, serving the Obsidian side-panel adapter.",
    "This run is read-only from the user's perspective. Do not modify Obsidian files, Zotero data, DocPacks, reminders, deployments, or secrets.",
    language === "en"
      ? "Respond in English unless the note content clearly requires another language."
      : "请用简体中文回复，除非用户明确要求其他语言。",
    taskInstruction(taskMode, language),
    "",
    `Task mode: ${taskMode}`,
    `Template id: ${templateId}`,
    `Target module: ${targetModule}`,
    `Target folder hint: ${targetFolder || "none"}`,
    `Active note path: ${filePath}`,
    `Vault: ${isRecord(context.vault) ? asText(context.vault.name) : "unknown"}`,
    `Requested context mode: ${asText(context.requested_mode) || asText(options.context_mode) || "unknown"}`,
    "",
    "User request:",
    userMessage || "(empty)",
    "",
    selectionText ? `Selected text:\n${truncate(selectionText, 8000)}` : "Selected text: none",
    "",
    noteText
      ? `Active note body${note?.truncated ? " (already truncated by plugin)" : ""}:\n${truncate(noteText, 14000)}`
      : "Active note body: not included",
    "",
    `Metadata:\n${formatMetadata(context.metadata)}`,
    "",
    "Return only the user-facing answer. Keep it concise and practical. Mention uncertainty explicitly.",
  ].join("\n");

  return truncate(prompt, maxPromptChars);
}

function sourceCards(body, runnerName) {
  const context = isRecord(body.context) ? body.context : {};
  const options = isRecord(body.options) ? body.options : {};
  const activeFile = isRecord(context.active_file) ? context.active_file : null;
  const selection = isRecord(context.selection) ? context.selection : null;
  const note = isRecord(context.note) ? context.note : null;
  const sources = [];
  if (activeFile?.path) {
    sources.push({
      title: "Obsidian active note",
      path: activeFile.path,
      kind: "obsidian-context",
      snippet: `selection=${selection?.chars ?? 0}, note=${note?.chars ?? 0}`,
    });
  }
  sources.push({
    title: `rtime assistant runner: ${runnerName}`,
    kind: "assistant-runner",
    snippet: [
      runnerName === "remote-claude-kimi" ? `${remoteNode}:${remoteWorkdir}` : localWorkdir,
      `module=${asText(options.target_module) || "auto"}`,
      `folder=${asText(options.target_folder) || "none"}`,
    ].join(" | "),
  });
  return sources;
}

function shellQuote(value) {
  return `'${String(value).replace(/'/g, "'\\''")}'`;
}

// `subtype` is the authoritative terminal status — mirror the python gateway.
// The Claude CLI sometimes sets is_error=true on an otherwise-successful run
// (transient brain-MCP / hook error mid-stream) while subtype stays "success".
// Failing then discards a complete answer; only error_* subtypes and real API
// errors are genuine terminal failures. Returns the fatal error text, or null.
function fatalResultError(d) {
  const subtype = String(d.subtype ?? "");
  if (subtype.startsWith("error")) {
    return d.result || d.error || `模型流以非成功状态结束：${subtype}`;
  }
  if (d.api_error_status) {
    return d.result || d.error || `Claude API error ${d.api_error_status}`;
  }
  if (d.is_error && !subtype) {
    return d.result || d.error || "Claude runner returned an error";
  }
  if (d.is_error) {
    console.error(
      `local-gateway: claude is_error=true on non-error subtype (subtype=${subtype || "∅"}); answer kept`,
    );
  }
  return null;
}

function extractClaudeText(stdout) {
  let resultText = "";
  let assistantText = "";
  let streamedText = "";
  let errorText = "";
  let isError = false;

  for (const line of stdout.split(/\n/)) {
    if (!line.trim()) {
      continue;
    }
    let data;
    try {
      data = JSON.parse(line);
    } catch {
      continue;
    }
    if (data.type === "result") {
      if (typeof data.result === "string") {
        resultText = data.result;
      }
      const fatal = fatalResultError(data);
      if (fatal) {
        isError = true;
        errorText = fatal;
      }
    }
    if (data.type === "assistant" && isRecord(data.message) && Array.isArray(data.message.content)) {
      assistantText += data.message.content
        .filter((part) => part?.type === "text")
        .map((part) => part.text || "")
        .join("");
      if (data.error) {
        errorText = data.error;
      }
    }
    if (data.type === "stream_event") {
      const event = data.event ?? {};
      if (event.type === "content_block_delta" && event.delta?.type === "text_delta") {
        streamedText += event.delta.text || "";
      }
    }
  }

  const text = (resultText || streamedText || assistantText).trim();
  if (isError) {
    throw new Error(errorText || text || "Claude runner returned an error");
  }
  return text;
}

async function runProcess({ command, args, cwd, prompt, env, timeoutMs }) {
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, {
      cwd,
      env,
      stdio: ["pipe", "pipe", "pipe"],
    });

    let stdout = "";
    let stderr = "";
    let settled = false;
    const timer = setTimeout(() => {
      if (!settled) {
        child.kill("SIGTERM");
        setTimeout(() => { if (!settled) child.kill("SIGKILL"); }, 5000).unref();
      }
    }, timeoutMs);

    child.stdout.on("data", (chunk) => {
      stdout += chunk.toString("utf8");
      if (stdout.length > 10 * 1024 * 1024) {
        stdout = stdout.slice(-8 * 1024 * 1024);
      }
    });
    child.stderr.on("data", (chunk) => {
      stderr += chunk.toString("utf8");
      if (stderr.length > 1024 * 1024) {
        stderr = stderr.slice(-512 * 1024);
      }
    });
    child.on("error", (error) => {
      settled = true;
      clearTimeout(timer);
      reject(error);
    });
    child.on("close", (code, signal) => {
      settled = true;
      clearTimeout(timer);
      resolve({ code, signal, stdout, stderr });
    });

    child.stdin.end(`${prompt}\n`);
  });
}

let cachedOauthToken; // undefined=未读, ""=无, string=有
async function readOauthToken() {
  if (cachedOauthToken !== undefined) {
    return cachedOauthToken;
  }
  if (process.env.CLAUDE_CODE_OAUTH_TOKEN) {
    cachedOauthToken = process.env.CLAUDE_CODE_OAUTH_TOKEN;
    return cachedOauthToken;
  }
  cachedOauthToken = "";
  try {
    if (existsSync(oauthTokenFile)) {
      const text = await readFile(oauthTokenFile, "utf8");
      const m = text.match(/^\s*CLAUDE_CODE_OAUTH_TOKEN\s*=\s*(.+?)\s*$/m);
      if (m && m[1]) {
        cachedOauthToken = m[1].replace(/^["']|["']$/g, "").trim();
      }
    }
  } catch {
    /* ignore: token optional; claude-local will surface auth errors */
  }
  return cachedOauthToken;
}

let cachedMcpConfigPath; // undefined=未试, ""=无, string=path
// brain 只读 MCP 配置:优先用 env 指定的文件,否则从 ~/.claude.json 抽出 rtime-library-gateway
// 这一个 server 写到临时文件,配合 --strict-mcp-config 让 claude-local 只看到 brain 网关。
async function brainMcpConfigPath() {
  if (cachedMcpConfigPath !== undefined) {
    return cachedMcpConfigPath;
  }
  if (localClaudeMcpConfig) {
    cachedMcpConfigPath = localClaudeMcpConfig;
    return cachedMcpConfigPath;
  }
  cachedMcpConfigPath = "";
  try {
    const claudeJson = path.join(os.homedir(), ".claude.json");
    if (existsSync(claudeJson)) {
      const data = JSON.parse(await readFile(claudeJson, "utf8"));
      const server = data?.mcpServers?.["rtime-library-gateway"];
      if (server) {
        const warmed = withSshControlMaster(server);
        const dir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-mcp-"));
        const out = path.join(dir, "brain-mcp.json");
        await writeFile(out, JSON.stringify({ mcpServers: { "rtime-library-gateway": warmed } }), { encoding: "utf8", mode: 0o600 });
        cachedMcpConfigPath = out;
      }
    }
  } catch {
    /* ignore: brain retrieval optional; claude-local still answers read-only without it */
  }
  return cachedMcpConfigPath;
}

// 给 ssh 型 MCP server 注入 ControlMaster 多路复用选项(连接复用 → 提速);非 ssh 原样返回
function withSshControlMaster(server) {
  if (!sshControlMaster) {
    return server; // 默认关闭:避免 orangepi 重启后陈旧 ssh 主连接导致 MCP 会话挂起
  }
  try {
    const cmd = String(server?.command || "");
    if (!Array.isArray(server?.args) || !/(^|\/)ssh(\.exe)?$/i.test(cmd)) {
      return server;
    }
    const opts = [
      "-o", "ControlMaster=auto",
      "-o", `ControlPath=${SSH_CONTROL_PATH}`,
      "-o", `ControlPersist=${SSH_CONTROL_PERSIST}`,
    ];
    return { ...server, args: [...opts, ...server.args] };
  } catch {
    return server;
  }
}

// 预热 brain 的 ssh 主连接,让 claude-local 首问也走已建立的多路复用连接(消除首次 SSH 握手延迟)
async function prewarmBrainSsh() {
  try {
    const cfgPath = await brainMcpConfigPath();
    if (!cfgPath) {
      return;
    }
    const data = JSON.parse(await readFile(cfgPath, "utf8"));
    const server = data?.mcpServers?.["rtime-library-gateway"];
    if (!server || !Array.isArray(server.args)) {
      return;
    }
    const bashIdx = server.args.indexOf("bash");
    const head = bashIdx > 0 ? server.args.slice(0, bashIdx) : server.args; // ssh 选项 + host(已含 ControlMaster)
    const env = { ...process.env };
    if (!String(env.PATH || "").split(":").includes("/opt/homebrew/bin")) {
      env.PATH = `/opt/homebrew/bin:${env.PATH ?? ""}`;
    }
    const child = spawn(String(server.command || "ssh"), [...head, "true"], { stdio: "ignore", env });
    child.on("error", () => {});
  } catch {
    /* ignore: 预热失败不影响功能,只是首问慢一点 */
  }
}

function permissionModeFromBody(body) {
  const options = isRecord(body?.options) ? body.options : {};
  const value = asText(options.permission_mode).trim();
  return PERMISSION_MODES.has(value) ? value : defaultPermissionMode;
}

function claudeArgs(body) {
  const args = [
    "--print",
    "--output-format",
    "stream-json",
    "--verbose",
    "--permission-mode",
    permissionModeFromBody(body),
  ];
  if (model) {
    args.push("--model", model);
  }
  return args;
}

// 本地工作区:本机 Claude 可在用户选定的 Mac 文件夹里工作(默认可读可写,当正常 Claude 用;
// 插件里可切只读)。仅接受【绝对路径 + 真实存在的目录】,否则忽略(退回中性沙盒 + 只读门),
// 防止脏路径越权或破坏。可用 RTIME_OBSIDIAN_LOCAL_WORKSPACE_FORCE_READONLY=1 全局强制只读。
const localWorkspaceForceReadOnly = (process.env.RTIME_OBSIDIAN_LOCAL_WORKSPACE_FORCE_READONLY ?? "0") === "1";

function resolveWorkspace(options) {
  const raw = asText(options?.local_workspace).trim();
  if (!raw || !path.isAbsolute(raw)) {
    return "";
  }
  try {
    if (existsSync(raw) && statSync(raw).isDirectory()) {
      return raw;
    }
  } catch {
    /* unreadable path → treat as unset */
  }
  return "";
}

function workspaceIsReadOnly(options) {
  // 默认可读可写(当正常 Claude 用);仅请求显式 read_only 或服务端强制时才只读。
  return localWorkspaceForceReadOnly || options?.local_workspace_read_only === true;
}

function resolveLocalClaudeCwd(body) {
  const options = isRecord(body?.options) ? body.options : {};
  return resolveWorkspace(options) || localClaudeCwd;
}

function claudeLocalArgs(body, mcpConfigPath, stream = false) {
  const options = isRecord(body?.options) ? body.options : {};
  const reqModel = asText(options.model_id).trim() || asText(options.model).trim() || localClaudeModel;
  const reqEffort = (
    asText(options.thinking_effort).trim() ||
    asText(options.effort).trim() ||
    localClaudeEffort
  ).toLowerCase();
  const workspace = resolveWorkspace(options);
  const workspaceWrite = workspace !== "" && !workspaceIsReadOnly(options);
  // legacy 整库编辑硬门:默认只读;仅服务端 RTIME_OBSIDIAN_ALLOW_EDIT=1 才认请求里的 edit_mode
  const editMode = allowEditMode && options.edit_mode === true;
  const writeAllowed = workspaceWrite || editMode;
  const args = [
    "--print",
    "--output-format",
    "stream-json",
    "--verbose",
    // 只加载 user 层设置,忽略 cwd 的 .claude/settings.json(绕开 brain 文件夹的 kimi apiKeyHelper)
    "--setting-sources",
    "user",
  ];
  if (stream) {
    // SSE 流式:让 claude 吐 token 级 content_block_delta
    args.push("--include-partial-messages");
  }
  if (reqModel) {
    args.push("--model", reqModel);
  }
  if (EFFORT_LEVELS.has(reqEffort)) {
    args.push("--effort", reqEffort);
  }
  if (mcpConfigPath) {
    // 只暴露 brain 网关这一个 MCP server
    args.push("--mcp-config", mcpConfigPath, "--strict-mcp-config");
  }
  // 让模型能访问的真实目录:优先用户工作区,否则(legacy 编辑模式)用 vault。
  // 只读工作区也 --add-dir(让 Read/Glob/Grep 能读它),但走下面的只读门、禁写。
  const addDir = workspace || (editMode ? localWorkdir : "");
  if (addDir) {
    args.push("--add-dir", addDir);
  }
  if (writeAllowed) {
    // 可写:当正常 Claude 用(Edit/Write/Bash),权限模式按请求(用户设的,默认 bypassPermissions)。
    args.push("--permission-mode", permissionModeFromBody(body));
  } else {
    // 只读门(实测组合 T1):dontAsk 不挂起 + 白名单放行只读类与 brain MCP + 黑名单硬禁写/Bash。
    // 不用 --tools:实测它会把 MCP 工具整段排除、导致 brain 检索失效。
    // 白名单用通配 mcp__rtime-library-gateway__*,对 brain 工具名 lib.search→lib_search 改名天然兼容。
    args.push("--permission-mode", "dontAsk");
    const allowed = ["Read", "Grep", "Glob"];
    if (mcpConfigPath) {
      allowed.push("mcp__rtime-library-gateway__*");
    }
    args.push("--allowedTools", ...allowed);
    args.push("--disallowedTools", READONLY_DISALLOWED);
  }
  return args;
}

async function runLocalClaude(prompt, body) {
  const command = process.env.RTIME_OBSIDIAN_CLAUDE_CLI ?? "claude";
  const env = { ...process.env };
  delete env.CLAUDECODE;
  const result = await runProcess({
    command,
    args: claudeArgs(body),
    cwd: localWorkdir,
    prompt,
    env,
    timeoutMs: runnerTimeoutMs,
  });
  const text = extractClaudeText(result.stdout);
  if (result.code !== 0 && !text) {
    throw new Error(`claude exited with ${result.code}: ${result.stderr.slice(0, 500) || "no stderr"}`);
  }
  return text;
}

async function runLocalClaudeOpus(prompt, body) {
  const command = process.env.RTIME_OBSIDIAN_CLAUDE_CLI ?? "claude";
  const env = { ...process.env };
  delete env.CLAUDECODE;
  delete env.CLAUDE_CODE_ENTRYPOINT;
  // 鉴权优先级:ANTHROPIC_API_KEY / ANTHROPIC_AUTH_TOKEN 高于 OAuth,必须清掉
  // (kimi 那套用 ANTHROPIC_AUTH_TOKEN;不清会静默走错鉴权)
  delete env.ANTHROPIC_API_KEY;
  delete env.ANTHROPIC_AUTH_TOKEN;
  delete env.ANTHROPIC_MODEL;
  delete env.ANTHROPIC_SMALL_FAST_MODEL;
  delete env.ANTHROPIC_BASE_URL;
  const token = await readOauthToken();
  if (token) {
    env.CLAUDE_CODE_OAUTH_TOKEN = token;
  }
  if (!String(env.PATH || "").split(":").includes("/opt/homebrew/bin")) {
    env.PATH = `/opt/homebrew/bin:${env.PATH ?? ""}`;
  }
  const mcpConfigPath = await brainMcpConfigPath();
  const result = await runProcess({
    command,
    args: claudeLocalArgs(body, mcpConfigPath),
    cwd: resolveLocalClaudeCwd(body),
    prompt,
    env,
    timeoutMs: localClaudeTimeoutMs,
  });
  const text = extractClaudeText(result.stdout);
  if (result.code !== 0 && !text) {
    throw new Error(`claude-local exited with ${result.code}: ${redactSecrets(result.stderr.slice(0, 500)) || "no stderr"}`);
  }
  return text;
}

// 流式版 claude-local:逐行解析 stream-json,把 token / 工具状态通过 emit 推给 SSE,返回最终全文
async function streamLocalClaudeOpus(prompt, body, emit, signal) {
  const command = process.env.RTIME_OBSIDIAN_CLAUDE_CLI ?? "claude";
  const env = { ...process.env };
  delete env.CLAUDECODE;
  delete env.CLAUDE_CODE_ENTRYPOINT;
  delete env.ANTHROPIC_API_KEY;
  delete env.ANTHROPIC_AUTH_TOKEN;
  delete env.ANTHROPIC_MODEL;
  delete env.ANTHROPIC_SMALL_FAST_MODEL;
  delete env.ANTHROPIC_BASE_URL;
  const token = await readOauthToken();
  if (token) {
    env.CLAUDE_CODE_OAUTH_TOKEN = token;
  }
  if (!String(env.PATH || "").split(":").includes("/opt/homebrew/bin")) {
    env.PATH = `/opt/homebrew/bin:${env.PATH ?? ""}`;
  }
  const mcpConfigPath = await brainMcpConfigPath();
  const args = claudeLocalArgs(body, mcpConfigPath, true);
  return new Promise((resolve, reject) => {
    const child = spawn(command, args, { cwd: resolveLocalClaudeCwd(body), env, stdio: ["pipe", "pipe", "pipe"] });
    let buffer = "";
    let stderr = "";
    let resultText = "";
    let assistantText = "";
    let streamedText = "";
    let errorText = "";
    let isError = false;
    let settled = false;
    let sawDelta = false;
    const decoder = new TextDecoder("utf-8"); // 跨 chunk 安全解码,避免多字节中文在分块边界被切坏
    const killTwoStage = () => {
      if (settled) return;
      try { child.kill("SIGTERM"); } catch { /* */ }
      setTimeout(() => { if (!settled) { try { child.kill("SIGKILL"); } catch { /* */ } } }, 5000).unref();
    };
    const timer = setTimeout(killTwoStage, localClaudeTimeoutMs);
    // 客户端断开(response close)→ abort → 杀子进程,不空跑到超时
    const onAbort = () => killTwoStage();
    if (signal) {
      if (signal.aborted) onAbort();
      else signal.addEventListener("abort", onAbort, { once: true });
    }
    child.stdout.on("data", (chunk) => {
      buffer += decoder.decode(chunk, { stream: true });
      let nl;
      while ((nl = buffer.indexOf("\n")) >= 0) {
        const line = buffer.slice(0, nl);
        buffer = buffer.slice(nl + 1);
        if (!line.trim()) {
          continue;
        }
        let d;
        try {
          d = JSON.parse(line);
        } catch {
          continue;
        }
        if (d.type === "stream_event" && d.event?.type === "content_block_delta" && d.event.delta?.type === "text_delta") {
          const t = d.event.delta.text || "";
          if (t) {
            streamedText += t;
            sawDelta = true;
            emit({ type: "delta", text: t });
          }
        } else if (d.type === "assistant" && isRecord(d.message) && Array.isArray(d.message.content)) {
          for (const part of d.message.content) {
            if (part?.type === "tool_use") {
              emit({ type: "status", text: `检索中:${part.name || "tool"}` });
            } else if (part?.type === "text" && typeof part.text === "string") {
              assistantText += part.text;
              // 兜底:若该 CLI 版本没吐 token 级 delta,把整块文本作为一次 delta 推出,避免前端无增量
              if (!sawDelta && part.text) {
                sawDelta = true;
                emit({ type: "delta", text: part.text });
              }
            }
          }
          if (d.error) {
            errorText = d.error;
          }
        } else if (d.type === "result") {
          if (typeof d.result === "string") {
            resultText = d.result;
          }
          const fatal = fatalResultError(d);
          if (fatal) {
            isError = true;
            errorText = fatal;
          }
        }
      }
    });
    child.stderr.on("data", (c) => {
      stderr += c.toString("utf8");
      if (stderr.length > 1024 * 1024) {
        stderr = stderr.slice(-512 * 1024);
      }
    });
    child.on("error", (e) => { settled = true; clearTimeout(timer); reject(e); });
    child.on("close", (code) => {
      settled = true;
      clearTimeout(timer);
      buffer += decoder.decode(); // flush 残留多字节
      const tail = buffer.trim(); // 末行可能无换行,补解析一次(尤其 result 帧)
      if (tail) {
        try {
          const d = JSON.parse(tail);
          if (d.type === "result" && typeof d.result === "string") {
            resultText = resultText || d.result;
          }
          if (d.type === "result") {
            const fatal = fatalResultError(d);
            if (fatal) {
              isError = true;
              errorText = errorText || fatal;
            }
          }
        } catch { /* 残尾非完整 JSON,忽略 */ }
      }
      const text = (resultText || streamedText || assistantText).trim();
      if (isError) {
        reject(new Error(redactSecrets(errorText || text || "claude-local stream error")));
        return;
      }
      if (code !== 0 && !text) {
        reject(new Error(`claude-local exited with ${code}: ${redactSecrets(stderr.slice(0, 500)) || "no stderr"}`));
        return;
      }
      resolve(text);
    });
    child.stdin.end(`${prompt}\n`);
  });
}

async function runRemoteClaudeKimi(prompt, body) {
  const timeoutSeconds = Math.max(10, Math.ceil(runnerTimeoutMs / 1000));
  const remoteCommand = [
    "cd",
    shellQuote(remoteWorkdir),
    "&&",
    "exec",
    "timeout",
    `${timeoutSeconds}s`,
    shellQuote(remoteCli),
    ...claudeArgs(body).map(shellQuote),
  ].join(" ");
  const result = await runProcess({
    command: remoteSsh,
    args: [remoteNode, remoteCommand],
    cwd: process.cwd(),
    prompt,
    env: {
      ...process.env,
      HOME: process.env.HOME ?? os.homedir(),
      PATH: process.env.PATH ?? "/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin",
    },
    timeoutMs: runnerTimeoutMs + 5000,
  });
  const text = extractClaudeText(result.stdout);
  if (result.code !== 0 && !text) {
    throw new Error(`remote claude-kimi exited with ${result.code}: ${result.stderr.slice(0, 500) || "no stderr"}`);
  }
  return text;
}

async function runCodex(prompt) {
  const tmpDir = await mkdtemp(path.join(os.tmpdir(), "rtime-obsidian-codex-"));
  const answerPath = path.join(tmpDir, "answer.md");
  try {
    const args = [
      "-s",
      "read-only",
      "-a",
      "never",
      "exec",
      "--ephemeral",
      "--ignore-rules",
      "-C",
      localWorkdir,
      "--output-last-message",
      answerPath,
      "-",
    ];
    if (codexModel) {
      args.splice(0, 0, "-m", codexModel);
    }
    const result = await runProcess({
      command: process.env.RTIME_OBSIDIAN_CODEX_CLI ?? "codex",
      args,
      cwd: localWorkdir,
      prompt,
      env: process.env,
      timeoutMs: runnerTimeoutMs,
    });
    const answer = (await readFile(answerPath, "utf8").catch(() => "")).trim();
    if (result.code !== 0 && !answer) {
      throw new Error(`codex exited with ${result.code}: ${result.stderr.slice(0, 500) || "no stderr"}`);
    }
    return answer || result.stdout.trim();
  } finally {
    await rm(tmpDir, { recursive: true, force: true });
  }
}

async function runNamedRunner(name, prompt, body) {
  if (name === "remote-claude-kimi") {
    return { runner: name, answer: await runRemoteClaudeKimi(prompt, body) };
  }
  if (name === "claude") {
    return { runner: name, answer: await runLocalClaude(prompt, body) };
  }
  if (name === "codex") {
    return { runner: name, answer: await runCodex(prompt) };
  }
  if (name === "claude-local") {
    return { runner: name, answer: await runLocalClaudeOpus(prompt, body) };
  }
  throw new Error(`unknown runner: ${name}`);
}

async function runAssistant(prompt, body) {
  // 按请求里的 provider 选 runner:claude-local = Mac 本机 Opus(订阅 token)
  const reqProvider = asText(isRecord(body?.options) ? body.options.model_provider_id : "").trim();
  if (reqProvider === "claude-local") {
    return runNamedRunner("claude-local", prompt, body);
  }
  if (runner !== "auto") {
    return runNamedRunner(runner, prompt, body);
  }
  const names = (process.env.RTIME_OBSIDIAN_AUTO_RUNNERS ?? "remote-claude-kimi,claude,codex")
    .split(",")
    .map((item) => item.trim())
    .filter(Boolean);
  const errors = [];
  for (const name of names) {
    try {
      return await runNamedRunner(name, prompt, body);
    } catch (error) {
      errors.push(`${name}: ${error instanceof Error ? error.message : String(error)}`);
    }
  }
  throw new Error(`all runners failed: ${errors.join(" | ")}`);
}

async function handleChat(body) {
  const prompt = buildPrompt(body);
  const startedAt = Date.now();
  const result = await runAssistant(prompt, body);
  const answer = result.answer.trim();
  if (!answer) {
    throw new Error(`${result.runner} returned an empty answer`);
  }
  return {
    answer,
    sources: sourceCards(body, result.runner),
    meta: {
      runner: result.runner,
      elapsed_ms: Date.now() - startedAt,
      prompt_chars: prompt.length,
    },
  };
}

// SSE 流式回答:claude-local 增量吐字;其它 runner 跑完一次性发 done(都返回合法 SSE,插件无需关流式)
async function handleChatStream(body, response) {
  response.writeHead(200, {
    "content-type": "text/event-stream; charset=utf-8",
    "cache-control": "no-store",
    "connection": "keep-alive",
    "access-control-allow-origin": "app://obsidian.md",
    "access-control-allow-methods": "GET, POST, OPTIONS",
    "access-control-allow-headers": "content-type, accept",
  });
  const emit = (obj) => {
    try {
      response.write(`data: ${JSON.stringify(obj)}\n\n`);
    } catch {
      /* client gone */
    }
  };
  const startedAt = Date.now();
  const prompt = buildPrompt(body);
  const provider = asText(isRecord(body?.options) ? body.options.model_provider_id : "").trim();
  const ac = new AbortController();
  response.on("close", () => ac.abort()); // 客户端断开 → 杀掉本机 claude 子进程,不空跑到超时
  try {
    emit({ type: "status", text: "正在处理…" });
    let answer = "";
    let runnerName = "";
    if (provider === "claude-local") {
      runnerName = "claude-local";
      answer = await streamLocalClaudeOpus(prompt, body, emit, ac.signal);
    } else {
      const result = await runAssistant(prompt, body);
      runnerName = result.runner;
      answer = result.answer;
    }
    answer = (answer || "").trim();
    if (!answer) {
      emit({ type: "error", message: `${runnerName || "runner"} returned an empty answer` });
      response.end();
      return;
    }
    emit({
      type: "done",
      answer,
      sources: sourceCards(body, runnerName),
      meta: { runner: runnerName, elapsed_ms: Date.now() - startedAt, prompt_chars: prompt.length },
    });
    response.end();
  } catch (error) {
    emit({ type: "error", message: error instanceof Error ? error.message : String(error) });
    response.end();
  }
}

function buildModelCatalog() {
  const now = new Date().toISOString();
  const cap = (extra) => ({
    agent_tools: true,
    code: true,
    chat: true,
    vision: false,
    long_context: 200000,
    thinking: "extended",
    ...(extra || {}),
  });
  return {
    schema_version: 1,
    generated_at: now,
    last_refreshed: now,
    providers: [
      {
        id: "claude-local",
        label: "本机 Claude Code（Opus）",
        protocol: "claude-wrapper/agent-tools",
        base_url_label: "local CLI · OAuth 订阅",
        models: [
          { id: "opus", label: "Claude Opus（最强）", protocol: "claude-wrapper/agent-tools", capabilities: cap() },
          { id: "sonnet", label: "Claude Sonnet（均衡）", protocol: "claude-wrapper/agent-tools", capabilities: cap() },
          { id: "haiku", label: "Claude Haiku（快速）", protocol: "claude-wrapper/agent-tools", capabilities: cap() },
        ],
      },
      {
        id: "kimi-code-wrapper",
        label: "Kimi Code（orangepi 常驻）",
        protocol: "claude-wrapper/agent-tools",
        base_url_label: "remote · ssh orangepi",
        models: [
          { id: "kimi-code", label: "Kimi Code", protocol: "claude-wrapper/agent-tools", capabilities: cap({ long_context: 256000 }) },
        ],
      },
    ],
  };
}

const server = http.createServer(async (request, response) => {
  const url = new URL(request.url ?? "/", `http://${host}:${port}`);

  if (request.method === "OPTIONS") {
    sendJson(response, 204, {});
    return;
  }

  if (request.method === "GET" && url.pathname === "/healthz") {
    sendJson(response, 200, {
      ok: true,
      service: "rtime-obsidian-local-gateway",
      runner,
      local_workdir: localWorkdir,
      remote_node: remoteNode,
      remote_workdir: remoteWorkdir,
      timeout_ms: runnerTimeoutMs,
      oauth_token_configured: !!(await readOauthToken()),
      neutral_cwd: localClaudeCwd,
      local_claude_model: localClaudeModel,
    });
    return;
  }

  if (request.method === "GET" && url.pathname === "/api/obsidian/models") {
    sendJson(response, 200, buildModelCatalog());
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/obsidian/models/refresh") {
    sendJson(response, 200, buildModelCatalog());
    return;
  }

  if (request.method === "POST" && url.pathname === "/api/obsidian/chat") {
    try {
      const rawBody = await readBody(request);
      const body = rawBody.trim() ? JSON.parse(rawBody) : {};
      const message = isRecord(body) ? asText(body.message).trim() : "";
      if (!message) {
        sendJson(response, 400, {
          ok: false,
          error: "empty_message",
        });
        return;
      }
      if (body.stream === true || body.stream === "true") {
        await handleChatStream(body, response);
      } else {
        const result = await handleChat(body);
        sendJson(response, 200, result);
      }
    } catch (error) {
      sendJson(response, 500, {
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

server.listen(port, host, async () => {
  const token = await readOauthToken();
  console.log(
    `rtime obsidian local gateway listening on http://${host}:${port} runner=${runner}` +
      (token ? " claude-local:oauth-ok" : " claude-local:NO-OAUTH-TOKEN"),
  );
  // 仅在显式开启 ssh ControlMaster 时才预热/续热主连接(默认关闭,见 sshControlMaster 注释)
  if (sshControlMaster) {
    prewarmBrainSsh();
    setInterval(prewarmBrainSsh, 240000).unref();
  }
});
