// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import { createHash } from "node:crypto";
import { mkdir, rm, copyFile, readFile, writeFile } from "node:fs/promises";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const outDir = path.join(root, "dist", "rtime-assistant");
const requiredFiles = ["manifest.json", "main.js", "styles.css"];

async function main() {
  await rm(outDir, { recursive: true, force: true });
  await mkdir(outDir, { recursive: true });

  for (const file of requiredFiles) {
    await copyFile(path.join(root, file), path.join(outDir, file));
  }

  const manifest = JSON.parse(await readFile(path.join(root, "manifest.json"), "utf8"));
  const fileManifest = {};
  for (const file of requiredFiles) {
    const data = await readFile(path.join(outDir, file));
    fileManifest[file] = {
      path: file,
      sha256: createHash("sha256").update(data).digest("hex"),
      size: data.length,
    };
  }
  const release = {
    schema_version: 1,
    id: manifest.id,
    name: manifest.name,
    version: manifest.version,
    minAppVersion: manifest.minAppVersion,
    generated_at: new Date().toISOString(),
    build_id: `${manifest.version}+${fileManifest["main.js"].sha256.slice(0, 12)}`,
    files: fileManifest,
  };
  await writeFile(path.join(outDir, "release.json"), `${JSON.stringify(release, null, 2)}\n`, "utf8");
  await writeFile(
    path.join(outDir, "README.txt"),
    [
      `${manifest.name} ${manifest.version}`,
      "",
      "Copy this folder into:",
      "<vault>/.obsidian/plugins/rtime-assistant/",
      "",
      "Required files are included:",
      requiredFiles.map((file) => `- ${file}`).join("\n"),
      "",
      "Private updater manifest:",
      "- release.json",
      "",
    ].join("\n"),
    "utf8",
  );

  console.log(`Packaged Obsidian plugin files into ${path.relative(root, outDir)}`);
}

main().catch((error) => {
  console.error(error instanceof Error ? error.message : String(error));
  process.exitCode = 1;
});
