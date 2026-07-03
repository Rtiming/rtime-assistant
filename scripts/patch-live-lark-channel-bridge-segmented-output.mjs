#!/usr/bin/env node
// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)

import fs from "node:fs";
import os from "node:os";
import path from "node:path";

const DEFAULT_TARGET = path.join(
  os.homedir(),
  ".npm-global/lib/node_modules/lark-channel-bridge/dist/cli.js",
);
const DEFAULT_CONFIG = path.join(os.homedir(), ".lark-channel/config.json");
const MARKER = "rtime-assistant segmented-output patch";
const WORKING_REACTION_LINE =
  `  const reactionPromise = replyMode === "card" ? void 0 : addWorkingReaction(channel, lastMsg.messageId);\n`;
const OLD_SEGMENTED_REACTION_SUPPRESSION =
  `  const reactionPromise = replyMode === "card" || outputStyle === "segmented" ? void 0 : addWorkingReaction(channel, lastMsg.messageId);\n`;

function usage() {
  return [
    "Usage:",
    "  patch-live-lark-channel-bridge-segmented-output.mjs [--target PATH] [--config PATH] [--check] [--dry-run] [--no-config]",
    "",
    "Patches the installed lark-channel-bridge dist/cli.js so assistant text is",
    "sent as separate chat messages at tool boundaries while the card remains a",
    "status/stop-control surface. The bridge still adds the normal working",
    "reaction under the triggering user message. Also sets active profile",
    "outputStyle=segmented and showToolCalls=false unless --no-config is provided.",
  ].join("\n");
}

function parseArgs(argv) {
  const out = {
    target: process.env.LARK_CHANNEL_BRIDGE_CLI || DEFAULT_TARGET,
    config: process.env.LARK_CHANNEL_CONFIG || DEFAULT_CONFIG,
    check: false,
    dryRun: false,
    updateConfig: true,
  };
  for (let i = 0; i < argv.length; i++) {
    const arg = argv[i];
    if (arg === "--target") {
      out.target = requireValue(argv, ++i, arg);
    } else if (arg === "--config") {
      out.config = requireValue(argv, ++i, arg);
    } else if (arg === "--check") {
      out.check = true;
    } else if (arg === "--dry-run") {
      out.dryRun = true;
    } else if (arg === "--no-config") {
      out.updateConfig = false;
    } else if (arg === "-h" || arg === "--help") {
      console.log(usage());
      process.exit(0);
    } else {
      throw new Error(`unknown argument: ${arg}`);
    }
  }
  return out;
}

function requireValue(argv, index, flag) {
  const value = argv[index];
  if (!value || value.startsWith("--")) {
    throw new Error(`${flag} requires a value`);
  }
  return value;
}

function replaceOnce(source, needle, replacement, label) {
  const count = source.split(needle).length - 1;
  if (count !== 1) {
    throw new Error(`expected one ${label} anchor, found ${count}`);
  }
  return source.replace(needle, replacement);
}

function patchCli(source) {
  if (source.includes(MARKER)) {
    const upgraded = source.replace(
      OLD_SEGMENTED_REACTION_SUPPRESSION,
      WORKING_REACTION_LINE
    );
    return { changed: upgraded !== source, text: upgraded };
  }

  let text = source;
  text = replaceOnce(
    text,
    `function getShowToolCalls(cfg) {
  return cfg.preferences?.showToolCalls !== false;
}
`,
    `function getShowToolCalls(cfg) {
  return cfg.preferences?.showToolCalls !== false;
}
function getOutputStyle(cfg) {
  return cfg.preferences?.outputStyle === "segmented" ? "segmented" : "default";
}
`,
    "getShowToolCalls"
  );

  text = replaceOnce(
    text,
    `function renderBlock(block) {
`,
    `function createSegmentedTextFlusher(sendSegment) {
  let nextBlockIndex = 0;
  return async (state) => {
    for (; nextBlockIndex < state.blocks.length; nextBlockIndex++) {
      const block = state.blocks[nextBlockIndex];
      if (block.kind !== "text") continue;
      if (block.streaming && state.terminal === "running") break;
      const content = block.content.trim();
      if (content) await sendSegment(content);
    }
  };
}
function segmentedStatusState(state) {
  const blocks = state.terminal === "done" ? [{ kind: "text", content: "\\u2705 \\u5DF2\\u5B8C\\u6210", streaming: false }] : [];
  const footer = state.footer === "tool_running" ? null : state.footer;
  return {
    ...state,
    blocks,
    reasoning: { content: "", active: false },
    footer
  };
}
// ${MARKER}
function renderBlock(block) {
`,
    "renderBlock"
  );

  text = replaceOnce(
    text,
    `  const replyMode = getMessageReplyMode(controls.cfg);
  log.info("flush", "reply-mode", { mode: replyMode });
`,
    `  const replyMode = getMessageReplyMode(controls.cfg);
  const outputStyle = getOutputStyle(controls.cfg);
  log.info("flush", "reply-mode", { mode: replyMode });
  log.info("flush", "output-style", { style: outputStyle });
`,
    "replyMode"
  );

  text = replaceOnce(
    text,
    `  try {
    if (replyMode === "card") {
`,
    `  try {
    if (outputStyle === "segmented") {
      const flushSegments = createSegmentedTextFlusher(async (content) => {
        const body = content.trim();
        if (!body) return;
        await channel.send(chatId, { markdown: body }, sendOpts);
      });
      let statusMessageId;
      try {
        const sent = await sendManagedCard(
          channel,
          chatId,
          renderCard(segmentedStatusState(initialState), cardRenderOptions),
          sendOpts
        );
        statusMessageId = sent.messageId;
      } catch (err) {
        log.fail("segmented", err, { step: "status-initial" });
      }
      await processAgentStream(
        handle,
        eventStream,
        scope,
        idleTimeoutMs,
        recordSession,
        async (state) => {
          await flushSegments(state);
          if (statusMessageId) {
            await updateManagedCard(
              channel,
              statusMessageId,
              renderCard(segmentedStatusState(state), cardRenderOptions)
            ).catch((err) => log.fail("segmented", err, { step: "status-update" }));
          }
        }
      );
    } else if (replyMode === "card") {
`,
    "replyMode branch"
  );

  return { changed: true, text };
}

function timestamp() {
  return new Date().toISOString().replace(/[-:]/g, "").replace(/\..+$/, "Z");
}

function backupFile(file) {
  const backup = `${file}.backup-${timestamp()}`;
  fs.copyFileSync(file, backup);
  return backup;
}

function patchConfig(configPath) {
  const raw = fs.readFileSync(configPath, "utf8");
  const cfg = JSON.parse(raw);
  const activeProfile = cfg.activeProfile || Object.keys(cfg.profiles || {})[0];
  if (!activeProfile || !cfg.profiles?.[activeProfile]) {
    throw new Error("active lark-channel profile not found in config");
  }
  const profile = cfg.profiles[activeProfile];
  profile.preferences = profile.preferences || {};
  const before = JSON.stringify(profile.preferences);
  profile.preferences.outputStyle = "segmented";
  profile.preferences.showToolCalls = false;
  const after = JSON.stringify(profile.preferences);
  return {
    changed: before !== after,
    text: `${JSON.stringify(cfg, null, 2)}\n`,
    activeProfile,
  };
}

function main() {
  const args = parseArgs(process.argv.slice(2));
  const source = fs.readFileSync(args.target, "utf8");
  const patched = patchCli(source);
  const configPatch =
    args.updateConfig && fs.existsSync(args.config) ? patchConfig(args.config) : void 0;

  if (args.check) {
    if (!source.includes(MARKER)) {
      throw new Error(`missing ${MARKER} in ${args.target}`);
    }
    if (source.includes(OLD_SEGMENTED_REACTION_SUPPRESSION)) {
      throw new Error(
        `installed segmented output patch suppresses working reactions: ${args.target}`
      );
    }
    if (!source.includes(WORKING_REACTION_LINE)) {
      throw new Error(`working reaction call not found in ${args.target}`);
    }
    if (args.updateConfig && configPatch?.changed) {
      throw new Error(`config is not set to segmented output: ${args.config}`);
    }
    console.log("ok: segmented output patch is installed");
    return;
  }

  if (args.dryRun) {
    console.log(
      JSON.stringify(
        {
          target: args.target,
          targetChanged: patched.changed,
          config: args.updateConfig ? args.config : null,
          configChanged: Boolean(configPatch?.changed),
        },
        null,
        2
      )
    );
    return;
  }

  if (patched.changed) {
    const backup = backupFile(args.target);
    fs.writeFileSync(args.target, patched.text);
    console.log(`patched ${args.target}`);
    console.log(`backup ${backup}`);
  } else {
    console.log(`already patched ${args.target}`);
  }

  if (args.updateConfig && configPatch) {
    if (configPatch.changed) {
      const backup = backupFile(args.config);
      fs.writeFileSync(args.config, configPatch.text);
      console.log(`updated ${args.config} activeProfile=${configPatch.activeProfile}`);
      console.log(`backup ${backup}`);
    } else {
      console.log(`config already segmented ${args.config}`);
    }
  }
}

try {
  main();
} catch (err) {
  console.error(err instanceof Error ? err.message : String(err));
  process.exit(1);
}
