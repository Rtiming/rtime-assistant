// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
// Publish an Obsidian plugin release to the private update channel in ONE command.
//
// Why this exists: the plugin distributes to clients (Mac/Windows) through a
// static release directory served by the orangepi gateway at
// `/api/obsidian/plugin-release/`, NOT by copying main.js into a vault. Copying
// into a vault is only a throwaway local test — it does not bump the version,
// the running plugin can revert data.json, and the in-app updater later
// overwrites it. The correct, durable path is: bump version -> build -> package
// -> publish here -> client clicks 刷新版本信息 / 检查并安装 / 重载插件.
//
// This script does build + package + scp + remote SHA-256 verification, and warns
// if the version was not bumped (old clients only discover updates by version).
//
// Targets are env-overridable (same `?? default` idiom as dev/e2e-node-transport.mjs):
//   RTIME_OBSIDIAN_RELEASE_SSH   ssh host/alias        (default: <runtime-host>)
//   RTIME_OBSIDIAN_RELEASE_DIR   remote static dir     (default: runtime plugin-release dir)
//   RTIME_GATEWAY_URL            gateway base URL       (default: http://127.0.0.1:8765)

import { execFileSync } from "node:child_process";
import { readFileSync } from "node:fs";
import path from "node:path";

const root = path.resolve(import.meta.dirname, "..");
const distDir = path.join(root, "dist", "rtime-assistant");
const VERIFIED_FILES = ["manifest.json", "main.js", "styles.css"];
const SCP_FILES = ["release.json", ...VERIFIED_FILES];

const SSH = process.env.RTIME_OBSIDIAN_RELEASE_SSH ?? "runtime-host";
const REMOTE_DIR =
  process.env.RTIME_OBSIDIAN_RELEASE_DIR ??
  "~/.local/share/rtime-assistant/plugin-release/rtime-assistant";
const GATEWAY = (process.env.RTIME_GATEWAY_URL ?? "http://127.0.0.1:8765").replace(/\/$/, "");

function run(cmd, args, opts = {}) {
  return execFileSync(cmd, args, { encoding: "utf8", cwd: root, ...opts });
}

function main() {
  // 1) Build + package (regenerates main.js and release.json with a fresh build_id).
  console.log("• building + packaging (npm run package:plugin) …");
  run("npm", ["run", "package:plugin"], { stdio: "inherit" });

  const release = JSON.parse(readFileSync(path.join(distDir, "release.json"), "utf8"));
  console.log(`• packaged ${release.id} ${release.version}  build=${release.build_id}`);

  // 2) Refuse-soft if the version was not bumped: old clients compare manifest.version
  //    only, so a same-version build_id change is invisible to them.
  try {
    const remote = JSON.parse(
      run("curl", ["-fsS", "-H", "Accept: application/json", "--max-time", "8",
        `${GATEWAY}/api/obsidian/plugin-release/release.json`], { stdio: ["ignore", "pipe", "ignore"] }),
    );
    if (remote.version === release.version) {
      console.warn(
        `\n⚠ The channel already serves version ${remote.version}. Clients that compare\n` +
        "  manifest.version only (older installs) will NOT see this as an update.\n" +
        "  For any user-facing change, bump version in manifest.json + versions.json +\n" +
        "  package.json before publishing.\n",
      );
    }
  } catch {
    // remote check is best-effort; continue to publish.
  }

  // 3) Publish: scp the release files into the static channel directory.
  console.log(`• publishing to ${SSH}:${REMOTE_DIR} …`);
  run("scp", ["-q", ...SCP_FILES.map((f) => path.join(distDir, f)), `${SSH}:${REMOTE_DIR}/`], {
    stdio: "inherit",
  });

  // 4) Verify each published file's SHA-256 matches release.json. The updater
  //    verifies these before installing; a mismatch makes 检查并安装 fail.
  console.log("• verifying remote SHA-256 …");
  const remoteShaOut = run("ssh", [SSH,
    `cd ${REMOTE_DIR} && for f in ${VERIFIED_FILES.join(" ")}; do printf '%s %s\\n' "$f" "$(sha256sum "$f" | cut -d' ' -f1)"; done`],
    { stdio: ["ignore", "pipe", "inherit"] });
  const remoteShas = Object.fromEntries(remoteShaOut.trim().split("\n").map((line) => line.split(" ")));

  let ok = true;
  for (const file of VERIFIED_FILES) {
    const expected = release.files[file]?.sha256;
    const actual = remoteShas[file];
    const match = !!expected && expected === actual;
    ok = ok && match;
    console.log(`  ${match ? "✓" : "✗"} ${file} ${(actual ?? "(missing)").slice(0, 12)}`);
  }
  if (!ok) {
    throw new Error("remote SHA-256 mismatch — published files do not match release.json (publish FAILED)");
  }

  console.log(`\n✅ published ${release.version} (${release.build_id}) to the private channel.`);
  console.log("   In Obsidian: 刷新版本信息 → 检查并安装 → 重载插件.");
}

try {
  main();
} catch (error) {
  console.error(`\n✗ publish failed: ${error instanceof Error ? error.message : String(error)}`);
  process.exitCode = 1;
}
