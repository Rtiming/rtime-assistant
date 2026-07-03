// SPDX-License-Identifier: AGPL-3.0-only
// Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
const fs = require("fs");
const https = require("https");
const path = require("path");
const { spawnSync } = require("child_process");

const REM = process.env.RTIME_REMINDERS_PATH || "/mnt/brain/_system/reminders.jsonl";
const FEISHU_CONFIG =
  process.env.RTIME_ASSISTANT_FEISHU_CONFIG ||
  path.join(process.env.HOME || "", ".config/rtime-assistant/feishu.json");
const DRY_RUN = process.argv.includes("--dry-run") || process.env.RTIME_REMINDER_DRY_RUN === "1";
const HOME = process.env.HOME || "";
const WAKE_RUNNER =
  process.env.RTIME_REMINDER_WAKE_RUNNER ||
  path.join(HOME, ".local/bin/rtime-reminder-wake-runner");
const WAKE_TIMEOUT_SECONDS = Number(process.env.RTIME_REMINDER_WAKE_TIMEOUT || "300");
const BEIJING_OFFSET_MS = 8 * 60 * 60 * 1000;

function pad(value, width = 2) {
  return String(value).padStart(width, "0");
}

function parseReminderDue(rawDue) {
  const text = String(rawDue || "").trim();
  if (!text) return new Date(NaN);
  const hasZone = /(?:Z|[+-]\d{2}:\d{2})$/i.test(text);
  return new Date(hasZone ? text : `${text}+08:00`);
}

function formatBeijingIso(date) {
  const local = new Date(date.getTime() + BEIJING_OFFSET_MS);
  return (
    `${local.getUTCFullYear()}-${pad(local.getUTCMonth() + 1)}-${pad(local.getUTCDate())}` +
    `T${pad(local.getUTCHours())}:${pad(local.getUTCMinutes())}:${pad(local.getUTCSeconds())}+08:00`
  );
}

function nowBeijingIso() {
  return formatBeijingIso(new Date());
}

function advanceDueBeijing(rawDue, repeat) {
  const due = parseReminderDue(rawDue);
  if (Number.isNaN(due.getTime())) return rawDue;
  if (repeat === "hourly") {
    return formatBeijingIso(new Date(due.getTime() + 60 * 60 * 1000));
  }
  const local = new Date(due.getTime() + BEIJING_OFFSET_MS);
  if (repeat === "daily") local.setUTCDate(local.getUTCDate() + 1);
  else if (repeat === "weekly") local.setUTCDate(local.getUTCDate() + 7);
  else return formatBeijingIso(due);
  return formatBeijingIso(new Date(local.getTime() - BEIJING_OFFSET_MS));
}

function loadFeishuConfig() {
  const appId = process.env.FEISHU_APP_ID;
  const appSecret = process.env.FEISHU_APP_SECRET;
  if (appId && appSecret) return { appId, appSecret };

  if (!fs.existsSync(FEISHU_CONFIG)) {
    throw new Error(
      `missing Feishu config: set FEISHU_APP_ID/FEISHU_APP_SECRET or create ${FEISHU_CONFIG}`,
    );
  }

  const cfg = JSON.parse(fs.readFileSync(FEISHU_CONFIG, "utf8"));
  const fileAppId = cfg.appId || cfg.app_id;
  const fileAppSecret = cfg.appSecret || cfg.app_secret;
  if (!fileAppId || !fileAppSecret) {
    throw new Error(`invalid Feishu config: ${FEISHU_CONFIG}`);
  }
  return { appId: fileAppId, appSecret: fileAppSecret };
}

function post(path, body, headers = {}) {
  return new Promise((resolve, reject) => {
    const data = JSON.stringify(body);
    const req = https.request(
      {
        host: "open.feishu.cn",
        path,
        method: "POST",
        headers: {
          "content-type": "application/json",
          "content-length": Buffer.byteLength(data),
          ...headers,
        },
      },
      (res) => {
        let raw = "";
        res.on("data", (chunk) => {
          raw += chunk;
        });
        res.on("end", () => {
          try {
            resolve(JSON.parse(raw));
          } catch {
            resolve({ raw });
          }
        });
      },
    );
    req.on("error", reject);
    req.write(data);
    req.end();
  });
}

function loadReminders() {
  if (!fs.existsSync(REM)) return [];
  return fs
    .readFileSync(REM, "utf8")
    .split("\n")
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter(Boolean);
}

function saveReminders(items) {
  fs.writeFileSync(
    REM,
    items.map((item) => JSON.stringify(item)).join("\n") + (items.length ? "\n" : ""),
  );
}

function reminderMeta(item) {
  return {
    id: item.id || "(missing)",
    due: item.due || "(missing)",
    repeat: item.repeat || "none",
    mode: item.mode || "notify",
    status: item.status || "(missing)",
    message_chars: String(item.message || "").length,
    prompt_chars: String(item.prompt || "").length,
    target_set: Boolean(item.target),
  };
}

function responseMeta(responseOrMessage) {
  if (typeof responseOrMessage === "string") {
    return { msg: responseOrMessage };
  }
  const response = responseOrMessage || {};
  return {
    code: response && Object.prototype.hasOwnProperty.call(response, "code") ? response.code : null,
    msg: response.msg || response.error || "",
  };
}

function markFailed(item, responseOrMessage) {
  item.status = "failed";
  item.failed_at = nowBeijingIso();
  item.last_error = responseMeta(responseOrMessage);
}

function modeOf(item) {
  return item.mode || "notify";
}

function buildWakePayload(item) {
  const payload = {
    id: item.id,
    due: item.due || "",
    prompt: item.prompt || item.message || "",
    message: item.message || "",
  };
  for (const key of ["cwd", "model", "permission_mode"]) {
    if (item[key]) payload[key] = item[key];
  }
  return payload;
}

function runWake(item) {
  const payload = buildWakePayload(item);
  if (!String(payload.prompt || "").trim()) {
    return { ok: false, error: "wake prompt is required" };
  }
  const result = spawnSync(WAKE_RUNNER, [], {
    input: JSON.stringify(payload),
    encoding: "utf8",
    // the runner enforces WAKE_TIMEOUT itself (plus docker exec overhead);
    // give it slack so its own timeout JSON wins over a hard kill here
    timeout: (WAKE_TIMEOUT_SECONDS + 60) * 1000,
    maxBuffer: 1024 * 1024,
    env: process.env,
  });
  if (result.error) {
    return {
      ok: false,
      error: result.error.code === "ETIMEDOUT" ? "wake runner timed out" : result.error.message,
    };
  }
  if (result.status !== 0) {
    try {
      const parsed = JSON.parse(result.stdout || "{}");
      return { ok: false, ...parsed, returncode: result.status };
    } catch {
      return {
        ok: false,
        error: "wake runner failed",
        returncode: result.status,
        stderr_chars: String(result.stderr || "").length,
        stdout_chars: String(result.stdout || "").length,
      };
    }
  }
  try {
    return JSON.parse(result.stdout || "{}");
  } catch {
    return { ok: false, error: "wake runner returned non-JSON output" };
  }
}

async function sendReminderText(token, item, text) {
  return await post(
    "/open-apis/im/v1/messages?receive_id_type=open_id",
    {
      receive_id: item.target,
      msg_type: "text",
      content: JSON.stringify({ text }),
    },
    { authorization: `Bearer ${token}` },
  );
}

function advanceOrComplete(item) {
  delete item.last_error;
  delete item.failed_at;
  item.sent_at = nowBeijingIso();
  if (item.repeat && item.repeat !== "none") {
    item.due = advanceDueBeijing(item.due, item.repeat);
  } else {
    item.status = "done";
  }
}

async function main() {
  const items = loadReminders();
  const now = Date.now();
  const due = items.filter(
    (item) => item.status === "pending" && parseReminderDue(item.due).getTime() <= now,
  );
  if (!due.length) {
    if (DRY_RUN) console.log("dry-run: no due reminders");
    return;
  }

  if (DRY_RUN) {
    console.log(`dry-run: ${due.length} due reminder(s) in ${REM}`);
    for (const item of due) {
      console.log("dry-run: reminder", JSON.stringify(reminderMeta(item)));
    }
    return;
  }

  const { appId, appSecret } = loadFeishuConfig();

  const tokenResponse = await post("/open-apis/auth/v3/tenant_access_token/internal", {
    app_id: appId,
    app_secret: appSecret,
  });
  const token = tokenResponse.tenant_access_token;
  if (!token) {
    console.error("no token", responseMeta(tokenResponse));
    process.exit(1);
  }

  let failed = false;

  for (const item of due) {
    // persist each item's outcome immediately (finally also runs on continue),
    // so an interrupted batch can never replay an already-sent reminder
    try {
      if (!item.target) {
        console.error("missing reminder target", reminderMeta(item));
        markFailed(item, "missing target");
        failed = true;
        continue;
      }

      let outboundText = `⏰ 提醒：${item.message}`;
      if (modeOf(item) === "wake") {
        const wake = runWake(item);
        console.log("wake runner", reminderMeta(item), "=>", {
          ok: Boolean(wake.ok),
          output_chars: String(wake.text || "").length,
          error_set: Boolean(wake.error),
        });
        if (!wake.ok) {
          markFailed(item, wake);
          failed = true;
          continue;
        }
        outboundText = String(wake.text || "").trim() || String(item.message || "").trim();
        if (!outboundText) {
          markFailed(item, "wake runner returned empty text");
          failed = true;
          continue;
        }
      } else if (modeOf(item) !== "notify") {
        markFailed(item, `unknown reminder mode: ${modeOf(item)}`);
        failed = true;
        continue;
      }

      const response = await sendReminderText(token, item, outboundText);
      console.log("sent reminder", reminderMeta(item), "=>", responseMeta(response));
      if (response.code !== 0) {
        markFailed(item, response);
        failed = true;
        continue;
      }

      advanceOrComplete(item);
    } finally {
      saveReminders(items);
    }
  }
  if (failed) process.exitCode = 1;
}

if (require.main === module) {
  main().catch((err) => {
    console.error(err && err.message ? err.message : String(err));
    process.exitCode = 1;
  });
}

module.exports = {
  advanceDueBeijing,
  formatBeijingIso,
  parseReminderDue,
  reminderMeta,
};
