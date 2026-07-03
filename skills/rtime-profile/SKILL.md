---
name: rtime-profile
description: Use when reviewing, planning, or packaging rtime assistant profile and policy adjustments, especially persona, prompt layering, model policy, permission policy, output style, memory behavior, sensitive-data rules, Codex/Claude skills, plugins, or MCP exposure on Mac and orangepi.
---

# Rtime Profile

Use this skill for read-only assistant profile and policy review. The stable
command surface is the `rtime-profile` CLI package in `packages/rtime-profile`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/assistant-profile-policy.md`
3. `docs/prompt-layering.md`
4. `docs/context-unlocking.md`
5. `docs/bridge-requirements.md`
6. `docs/logging-and-audit.md`
7. `docs/tooling-packaging.md` when package, skill, plugin, or MCP surfaces
   are involved

For global assistant identity, read `brain/CLAUDE.md` from the mounted library
only when the task needs profile evidence.

## Rules

- Keep `rtime-profile` scans and MCP tools read-only.
- Draft a proposal before changing assistant persona, prompt layers, model
  routing, permissions, output behavior, or memory policy.
- Do not edit `brain/CLAUDE.md`, runtime config, permission gates, or prompt
  files without explicit user confirmation and focused validation.
- Do not read API keys, tokens, identity documents, addresses, session stores,
  or high-sensitivity notes unless the task gives a precise scope.
- MCP run logs may record paths, request length, status, duration, and failure
  reason, but not the raw request body, source bodies, secrets, or profile
  text.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-profile --profile mac
python -m pip install -e packages/rtime-profile
rtime-profile doctor
```

Read-only checks on Mac:

```bash
rtime-profile scan --repo-root <repo-root> --brain-root <brain-root>
rtime-profile panel --repo-root <repo-root> --brain-root <brain-root>
rtime-profile plan "调整助手人格和模型策略，但不要读取 API key"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-profile-mcp
```

Read-only checks on orangepi:

```bash
rtime-profile scan --repo-root <repo-root> --brain-root <brain-root>
rtime-profile panel --repo-root <repo-root> --brain-root <brain-root>
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-profile/src/rtime_profile/*.py
PYTHONPATH=packages/rtime-profile/src python -m pytest tests/test_rtime_profile_cli.py tests/test_rtime_profile_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-profile
git diff --check
```
