---
name: rtime-review
description: Use when building, inspecting, or planning review-console surfaces for rtime-assistant, especially memory candidates, failed runs, run-log summaries, permission audit, standards audit archives, tooling health, context-plan review, or future Web console governance panels.
---

# Rtime Review

Use this skill for read-only review-console data surfaces. The stable command
surface is the `rtime-review` CLI package in `packages/rtime-review`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/review-console.md`
3. `docs/logging-and-audit.md`
4. `docs/context-orchestrator.md`
5. `docs/tooling-packaging.md`

## Rules

- Keep review surfaces read-only.
- Treat review panels as summaries, not approvals.
- Do not approve memory candidates, write memories, edit files, deploy,
  restart services, or mutate logs from this tool.
- Keep runtime logs, context logs, standards archives, and review packets out
  of git.
- Redaction is a second guardrail; do not put secrets into logs in the first
  place.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-review --profile mac
python -m pip install -e packages/rtime-review
rtime-review doctor
```

Read-only checks:

```bash
rtime-review panel --repo-root <repo-root>
rtime-review audits --repo-root <repo-root>
rtime-review tooling --repo-root <repo-root>
rtime-review run-logs ~/.local/state/rtime-assistant/run-log.jsonl
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-review-mcp
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-review/src/rtime_review/*.py
PYTHONPATH=packages/rtime-review/src python -m pytest tests/test_rtime_review_cli.py tests/test_rtime_review_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-review
git diff --check
```
