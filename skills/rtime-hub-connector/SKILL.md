---
name: rtime-hub-connector
description: Use when reading, auditing, or planning project status, device status, contact-directory panels, service/task/deployment context, or Mac/orangepi rtime-hub workflows without modifying the rtime-hub fact store.
---

# Rtime Hub Connector

Use this skill for read-only project workspace context from `rtime-hub`.
The stable command surface is the `rtime-hub-connector` CLI package in
`packages/rtime-hub-connector`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/rtime-hub-connector.md`
3. `docs/tooling-packaging.md`
4. `docs/tooling-installation.md`
5. `docs/logging-and-audit.md` when MCP run logs or audit archives are involved

For hub-side behavior, read `rtime-hub/AGENTS.md` and `rtime-hub/状态.md`
from the local hub root before any task that may depend on live project or
device context.

## Rules

- Keep connector scans and MCP tools read-only.
- Do not copy hub file bodies into ordinary logs or generated summaries.
- Treat `rtime-hub` as the source of project, device, contact, service, task,
  deployment, and handoff facts. Do not duplicate those facts in
  `rtime-assistant`.
- Follow `rtime-hub/AGENTS.md` before any future write operation. This skill and
  its MCP server do not write, commit, push, sync, deploy, or restart services.
- API keys, addresses, identity fields, credentials, session stores, and secret
  files must stay out of logs, git, and normal summaries.
- On Mac, prefer `<rtime-hub-root>` or
  `<rtime-hub-root>`. On orangepi, prefer
  `<rtime-hub-root>`.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-hub-connector --profile mac
python -m pip install -e packages/rtime-hub-connector
rtime-hub-connector doctor <rtime-hub-root>
```

Read-only checks on Mac:

```bash
rtime-hub-connector scan <rtime-hub-root> --sample-limit 20
rtime-hub-connector panel <rtime-hub-root> --sample-limit 20
rtime-hub-connector contacts <rtime-hub-root> --sample-limit 20
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-hub-mcp
```

Read-only checks on orangepi:

```bash
rtime-hub-connector doctor <rtime-hub-root>
rtime-hub-connector panel <rtime-hub-root> --sample-limit 20
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-hub-connector/src/rtime_hub_connector/*.py
PYTHONPATH=packages/rtime-hub-connector/src python -m pytest tests/test_rtime_hub_connector_cli.py tests/test_rtime_hub_connector_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-hub-connector
git diff --check
```
