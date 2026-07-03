---
name: rtime-automation
description: Use when inspecting, planning, or reviewing rtime assistant reminders, schedulers, notifications, workflow automation, timer health, or automation run-log surfaces without sending messages or mutating runtime state.
---

# Rtime Automation

Use this skill for read-only automation and reminder diagnostics. The stable
command surface is the `rtime-automation` CLI package in
`packages/rtime-automation`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/automation-and-reminders.md`
3. `docs/workflows.md`
4. `docs/logging-and-audit.md`
5. `docs/runbook.md`
6. `docs/deployment.md`

## Rules

- Keep this tool read-only.
- Do not send Feishu/Lark messages, push notifications, write reminders,
  deploy, restart services, edit live systemd units, or mutate workflow state.
- Do not read secrets, Feishu targets, session stores, API keys, identity data,
  personal addresses, or private reminder message bodies.
- Reminder summaries may return counts, due metadata, line numbers, and message
  character counts, but not reminder text or target values.
- Treat `rtime-automation plan` output as a proposal skeleton, not permission to
  write or send anything.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-automation --profile mac
python -m pip install -e packages/rtime-automation
rtime-automation doctor
```

Read-only checks:

```bash
rtime-automation doctor --repo-root <repo-root>
rtime-automation reminders <brain-root>/_system/reminders.jsonl --sample-limit 5
rtime-automation health <brain-root>/_system/reminders.jsonl
rtime-automation panel --repo-root <repo-root> --sample-limit 5
rtime-automation plan "规划飞书提醒和定时任务，但不要真的发送通知"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-automation-mcp
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-automation/src/rtime_automation/*.py
PYTHONPATH=packages/rtime-automation/src python -m pytest tests/test_rtime_automation_cli.py tests/test_rtime_automation_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-automation
git diff --check
```
