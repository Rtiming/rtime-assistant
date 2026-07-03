// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import type { RtimeAssistantSettings } from "../types";
import { requestWithRetry } from "./http";

export const REQUIRED_PLUGIN_RELEASE_FILES = ["manifest.json", "main.js", "styles.css"] as const;

export type RequiredPluginReleaseFile = (typeof REQUIRED_PLUGIN_RELEASE_FILES)[number];

export interface PluginReleaseFile {
  path: string;
  sha256: string;
  size?: number;
}

export interface PluginReleaseManifest {
  schema_version: 1;
  id: string;
  name?: string;
  version: string;
  minAppVersion?: string;
  generated_at?: string;
  build_id?: string;
  files: Record<RequiredPluginReleaseFile, PluginReleaseFile>;
}

export interface DownloadedPluginRelease {
  manifestUrl: string;
  release: PluginReleaseManifest;
  files: Record<RequiredPluginReleaseFile, string>;
}

export interface CheckedPluginRelease {
  manifestUrl: string;
  release: PluginReleaseManifest;
}

export type PluginReleaseStatus =
  | "available-newer"
  | "available-build"
  | "available-current"
  | "available-older"
  | "installed";

export function releaseManifestUrl(updateUrl: string): string {
  const trimmed = updateUrl.trim();
  if (!trimmed) {
    throw new Error("Plugin update URL is empty");
  }
  if (/\.json(?:[?#].*)?$/i.test(trimmed)) {
    return trimmed;
  }
  return `${trimmed.replace(/\/+$/, "")}/release.json`;
}

export function resolveReleaseAssetUrl(manifestUrl: string, assetPath: string): string {
  return new URL(assetPath, manifestUrl).toString();
}

export function parsePluginReleaseManifest(value: unknown): PluginReleaseManifest {
  if (!isRecord(value)) {
    throw new Error("Plugin release manifest must be a JSON object");
  }
  if (value.schema_version !== 1) {
    throw new Error("Unsupported plugin release manifest schema");
  }
  const id = stringField(value, "id");
  const version = stringField(value, "version");
  const filesValue = value.files;
  if (!isRecord(filesValue)) {
    throw new Error("Plugin release manifest is missing files");
  }
  const files = {} as Record<RequiredPluginReleaseFile, PluginReleaseFile>;
  for (const file of REQUIRED_PLUGIN_RELEASE_FILES) {
    const parsed = parseReleaseFile(file, filesValue[file]);
    files[file] = parsed;
  }
  return {
    schema_version: 1,
    id,
    name: optionalStringField(value, "name"),
    version,
    minAppVersion: optionalStringField(value, "minAppVersion"),
    generated_at: optionalStringField(value, "generated_at"),
    build_id: optionalStringField(value, "build_id"),
    files,
  };
}

export async function fetchPluginReleaseManifest(settings: RtimeAssistantSettings): Promise<CheckedPluginRelease> {
  const manifestUrl = releaseManifestUrl(settings.pluginUpdateUrl);
  const response = await requestWithRetry({
    url: manifestUrl,
    method: "GET",
    timeoutMs: updateTimeoutMs(settings),
    retryCount: settings.requestRetryCount,
    retryDelayMs: settings.requestRetryDelayMs,
  });
  if (response.status < 200 || response.status >= 300) {
    throw new Error(`Plugin release manifest returned HTTP ${response.status}`);
  }
  const release = parsePluginReleaseManifest(parseResponseJson(response.text, response.json));
  return { manifestUrl, release };
}

export async function downloadPluginRelease(settings: RtimeAssistantSettings): Promise<DownloadedPluginRelease> {
  const { manifestUrl, release } = await fetchPluginReleaseManifest(settings);
  const files = {} as Record<RequiredPluginReleaseFile, string>;
  for (const file of REQUIRED_PLUGIN_RELEASE_FILES) {
    const info = release.files[file];
    const fileUrl = resolveReleaseAssetUrl(manifestUrl, info.path);
    const fileResponse = await requestWithRetry({
      url: fileUrl,
      method: "GET",
      timeoutMs: updateTimeoutMs(settings),
      retryCount: settings.requestRetryCount,
      retryDelayMs: settings.requestRetryDelayMs,
    });
    if (fileResponse.status < 200 || fileResponse.status >= 300) {
      throw new Error(`${file} returned HTTP ${fileResponse.status}`);
    }
    const bytes = new TextEncoder().encode(fileResponse.text);
    if (typeof info.size === "number" && info.size !== bytes.length) {
      throw new Error(`${file} size mismatch`);
    }
    const digest = await sha256Hex(bytes);
    if (digest !== info.sha256) {
      throw new Error(`${file} SHA-256 mismatch`);
    }
    files[file] = fileResponse.text;
  }
  return { manifestUrl, release, files };
}

export function compareVersions(a: string, b: string): number {
  const left = numericVersionParts(a);
  const right = numericVersionParts(b);
  const length = Math.max(left.length, right.length);
  for (let index = 0; index < length; index += 1) {
    const delta = (left[index] ?? 0) - (right[index] ?? 0);
    if (delta !== 0) {
      return delta > 0 ? 1 : -1;
    }
  }
  return 0;
}

export function pluginReleaseStatus(
  release: Pick<PluginReleaseManifest, "version" | "build_id">,
  currentVersion: string,
  installedVersion = "",
  installedBuildId = "",
): PluginReleaseStatus {
  const installedDelta = installedVersion ? compareVersions(installedVersion, currentVersion) : 0;
  if (
    installedDelta > 0 &&
    release.version === installedVersion &&
    (!release.build_id || release.build_id === installedBuildId)
  ) {
    return "installed";
  }
  const versionDelta = compareVersions(release.version, currentVersion);
  if (versionDelta > 0) {
    return "available-newer";
  }
  if (versionDelta < 0) {
    return "available-older";
  }
  if (release.build_id && release.build_id !== installedBuildId) {
    return "available-build";
  }
  return "available-current";
}

async function sha256Hex(bytes: Uint8Array): Promise<string> {
  if (!globalThis.crypto?.subtle) {
    throw new Error("WebCrypto SHA-256 is unavailable in this Obsidian runtime");
  }
  const input = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength) as ArrayBuffer;
  const digest = await globalThis.crypto.subtle.digest("SHA-256", input);
  return Array.from(new Uint8Array(digest))
    .map((byte) => byte.toString(16).padStart(2, "0"))
    .join("");
}

function parseReleaseFile(file: RequiredPluginReleaseFile, value: unknown): PluginReleaseFile {
  if (!isRecord(value)) {
    throw new Error(`Plugin release manifest is missing ${file}`);
  }
  const assetPath = optionalStringField(value, "path") ?? file;
  if (!isSafeRelativeAssetPath(assetPath)) {
    throw new Error(`${file} has an unsafe release path`);
  }
  const sha256 = stringField(value, "sha256").toLowerCase();
  if (!/^[a-f0-9]{64}$/.test(sha256)) {
    throw new Error(`${file} has an invalid SHA-256`);
  }
  const size = value.size;
  if (size !== undefined && (typeof size !== "number" || !Number.isInteger(size) || size < 0)) {
    throw new Error(`${file} has an invalid size`);
  }
  return { path: assetPath, sha256, size: typeof size === "number" ? size : undefined };
}

function parseResponseJson(text: string, json: unknown): unknown {
  if (json !== undefined) {
    return json;
  }
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`Plugin release manifest is not valid JSON: ${error instanceof Error ? error.message : String(error)}`);
  }
}

function updateTimeoutMs(settings: RtimeAssistantSettings): number {
  return Math.min(Math.max(settings.requestTimeoutMs, 5000), 30000);
}

function isSafeRelativeAssetPath(value: string): boolean {
  if (!value || value.startsWith("/") || value.includes("\\") || value.includes("://")) {
    return false;
  }
  return !value.split("/").includes("..");
}

function numericVersionParts(value: string): number[] {
  return value
    .split(".")
    .map((part) => Number.parseInt(part.replace(/[^0-9].*$/, ""), 10))
    .map((part) => (Number.isFinite(part) ? part : 0));
}

function stringField(value: Record<string, unknown>, field: string): string {
  const item = value[field];
  if (typeof item !== "string" || !item.trim()) {
    throw new Error(`Plugin release manifest is missing ${field}`);
  }
  return item.trim();
}

function optionalStringField(value: Record<string, unknown>, field: string): string | undefined {
  const item = value[field];
  return typeof item === "string" && item.trim() ? item.trim() : undefined;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}
