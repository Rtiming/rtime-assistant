---
name: rtime-reminder
description: Use when registering, listing, cancelling, testing, or diagnosing rtime Feishu reminders and scheduled reminder delivery. Trigger for Chinese or English reminder requests such as "提醒我", "定时提醒", "闹钟", "remind me", or "schedule a reminder"; use the rtime JSONL reminder path and never Claude Code Cron for phone push reminders.
---

# Rtime Reminder

Use the system reminder path for Feishu phone push reminders:

```text
brain/_system/reminders.jsonl
```

Do not use Claude Code `CronCreate`, `CronList`, or `CronDelete` for Feishu
phone reminders. Those tasks belong to the current Claude session and do not
feed `reminder.timer` / `reminder-sender.js`.

## Register

Parse the user's requested time as Beijing time unless they explicitly specify
another timezone. Convert it to an ISO datetime with timezone, then use:

Choose the mode explicitly:

- `notify`: send a fixed message without waking the assistant model.
- `wake`: at due time, run an independent assistant task and send its result.

Default decision rule:

- Use `notify` only when the user wants a literal, self-contained push message
  and no reasoning is useful at trigger time.
- Use `wake` when the reminder depends on the current conversation, study or
  exam state, travel timing, project status, completion state, or the user wants
  the assistant to judge, summarize, check, ask, or give advice at trigger time.

For `wake`, write a self-contained prompt for the future assistant run. Include
the original request, due time, known facts, user concern, and the expected
outbound message style. Do not rely on the current chat still being in context
when the reminder fires. If you omit `--prompt`, the register helper generates
a generic fallback prompt from `message` and `due`, but that is lower quality
than passing the relevant context yourself.

```bash
rtime-reminder-register add --mode notify --due "2026-06-11T09:30:00+08:00" --message "提醒内容"
rtime-reminder-register add --mode wake --due "2026-06-11T09:30:00+08:00" --message "唤醒标题" --prompt "到点执行的助手任务"
```

Repeat values:

```text
none hourly daily weekly
```

For repeat reminders:

```bash
rtime-reminder-register add --mode notify --due "2026-06-12T08:00:00+08:00" --repeat daily --message "提醒内容"
```

## List Or Cancel

```bash
rtime-reminder-register list --status pending
rtime-reminder-register list --status failed
rtime-reminder-register cancel --id <reminder-id>
```

The tool returns metadata only: id, due, repeat, status, message length, and
whether a target is configured. Do not expose Feishu open_id values or private
message bodies unless the user explicitly asks to inspect the raw JSONL.

If registration fails because no target is configured, use the runtime's
current `RTIME_REMINDER_DEFAULT_TARGET`; do not copy targets from old reminder
records after a Feishu app migration.

## Smoke

Use `scripts/reminder-smoke.sh` to validate the local tool with a temporary
JSONL file. It must not write to the real brain reminder store.
