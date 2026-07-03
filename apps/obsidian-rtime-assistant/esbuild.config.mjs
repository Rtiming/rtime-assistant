// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import builtins from "builtin-modules";
import esbuild from "esbuild";

const production = process.argv.includes("production");
const watch = process.argv.includes("watch");

const banner =
  "/* Rtime Assistant for Obsidian. Built from apps/obsidian-rtime-assistant/src/main.ts */";

const options = {
  banner: { js: banner },
  bundle: true,
  entryPoints: ["src/main.ts"],
  external: [
    "obsidian",
    "electron",
    "@codemirror/autocomplete",
    "@codemirror/collab",
    "@codemirror/commands",
    "@codemirror/language",
    "@codemirror/lint",
    "@codemirror/search",
    "@codemirror/state",
    "@codemirror/view",
    "@lezer/common",
    "@lezer/highlight",
    "@lezer/lr",
    ...builtins,
  ],
  format: "cjs",
  logLevel: "info",
  minify: production,
  outfile: "main.js",
  platform: "browser",
  sourcemap: production ? false : "inline",
  target: "es2022",
  treeShaking: true,
};

if (watch) {
  const context = await esbuild.context(options);
  await context.watch();
  console.log("Watching Obsidian rtime assistant plugin...");
} else {
  await esbuild.build(options);
}
