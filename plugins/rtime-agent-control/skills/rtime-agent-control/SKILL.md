---
name: rtime-agent-control
description: Use when configuring, auditing, or debugging rtime-assistant agent tooling, MCP client snippets, tool installation status, validation plans, context-lane routing, or read-only runtime snapshots for AI-agent control surfaces.
---

# Rtime Agent Control

Use this skill for read-only agent control-plane diagnostics. The stable
command surface is the `rtime-agent-control` CLI package in
`packages/rtime-agent-control`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/agent-control-mcp.md`
3. `docs/tooling-packaging.md`
4. `docs/tooling-installation.md`
5. `docs/assistant-profile-policy.md`
6. `docs/logging-and-audit.md`

## Rules

- Keep agent-control operations read-only and proposal-only.
- Do not run arbitrary shell commands through this MCP surface.
- Do not write MCP config, deploy, restart services, send bridge messages, or
  mutate reminders, memories, runtime logs, hub facts, Obsidian files, or
  DocPacks from this tool.
- Do not read or return secret values, session stores, provider keys, tokens,
  or raw reminder message text.
- MCP run logs may record metadata only: run id, tool name, paths, request
  length, status, duration, and failure reason.
- Treat rendered MCP config as a draft for a human or installer workflow to
  review before applying.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-agent-control --profile mac
python -m pip install -e packages/rtime-agent-control
rtime-agent-control doctor
```

Read-only checks:

```bash
rtime-agent-control tooling --repo-root <repo-root>
rtime-agent-control config-render --repo-root <repo-root> --tool rtime-agent-control
rtime-agent-control validation-plan --repo-root <repo-root> --module agent-control
rtime-agent-control context-plan "configure agent MCP debugging" --repo-root <repo-root>
rtime-agent-control runtime-snapshot --repo-root <repo-root>
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-agent-control-mcp
```

## Validation

For package, MCP, skill, plugin, or installer changes, run:

```bash
python -m py_compile packages/rtime-agent-control/src/rtime_agent_control/*.py
PYTHONPATH=packages/rtime-agent-control/src python -m pytest tests/test_rtime_agent_control_cli.py tests/test_rtime_agent_control_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-agent-control
bash -n plugins/rtime-agent-control/scripts/rtime-agent-control-mcp.sh
bash -n scripts/install-rtime-tooling.sh
git diff --check
```
