---
name: rtime-assistant-runtime
description: Use when inspecting or troubleshooting the rtime-assistant runtime, Feishu/Lark bridge candidate, run logs, queue/access/output modules, Docker production Compose checks, systemd templates, or Mac/orangepi runtime diagnostics without changing live services.
---

# Rtime Assistant Runtime

Use this skill for read-only runtime diagnosis around CLI + Feishu/Lark bridge
stability, including Docker production deployment checks. The stable command
surface is the `rtime-runtime` CLI package in `packages/rtime-assistant-runtime`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `apps/feishu-bridge/AGENTS.md`
3. `docs/logging-and-audit.md`
4. `docs/runtime-assets.md`
5. `docs/deployment.md` when service templates or deploy paths are involved
6. `docs/docker-production.md` and `docs/docker-workflow.md` when Docker,
   server cutover, image build, or health checks are involved

## Rules

- Keep diagnosis read-only unless the user explicitly asks for deployment or
  restart work.
- Prefer `rtime-runtime docker-prod check` or MCP
  `runtime.docker_prod_check` for Docker production review. These are static
  checks and optional env-file key/permission checks; they do not run Docker.
- Do not print or copy Feishu secrets, Claude/Kimi keys, session stores, or raw
  credentials.
- Treat `apps/feishu-bridge/` as Python candidate code; confirm the live
  systemd unit before claiming it is production.
- Do not run two Feishu/Lark bridges against the same Feishu app. A production
  cutover must stop `lark-bridge.service` before starting the Docker bridge.
- Treat `scripts/docker-prod-check.sh --config`, `--build`, and `--smoke` as
  validation operations. Treat `--up` and `--down` as deployment operations
  requiring explicit user intent and rollback notes.
- For Docker production, verify either `CLAUDE_STATE_ROOT` mounts an existing
  `.claude` directory or provider-token variables are intentionally configured.
- For Mac-to-orangepi releases, verify the whole loop: redacted env backup,
  rsync/git sync excludes state files, image rebuild after source `COPY`,
  one-shot simulation/smoke before restart, health/readiness after restart, and
  recent-log error scan.
- If a helper or simulation entrypoint is part of the image contract, rebuild
  again after changing it. A bind-mounted repo path may expose the new helper to
  tests, while the live service still runs image-baked source.
- Runtime logs live outside git, usually under
  `~/.local/state/rtime-assistant/` or
  `<runtime-state-dir>/`.
- For service changes, update templates in this repo before any live unit
  deployment.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-assistant-runtime --profile mac
python -m pip install -e packages/rtime-assistant-runtime
rtime-runtime doctor
```

Read-only checks:

```bash
rtime-runtime templates check
rtime-runtime docker-prod check
rtime-runtime docker-prod check --env-file <runtime-env-file>
rtime-runtime docker-prod check --env-file deploy/env/feishu-bridge.prod.env.example
rtime-runtime run-log summary ~/.local/state/rtime-assistant/run-log.jsonl
rtime-runtime run-log tail ~/.local/state/rtime-assistant/run-log.jsonl --limit 5
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-runtime-mcp
printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"runtime.docker_prod_check","arguments":{"repo_root":"<repo-root>"}}}\n' | rtime-runtime-mcp
```

On orangepi, use:

```bash
rtime-runtime docker-prod check --env-file <runtime-env-file>
rtime-runtime run-log summary <runtime-state-dir>/feishu-bridge-run.jsonl
systemctl --user status lark-bridge reminder.timer
```

## Validation

For runtime tool or bridge changes, run:

```bash
python -m py_compile packages/rtime-assistant-runtime/src/rtime_assistant_runtime/*.py
PYTHONPATH=packages/rtime-assistant-runtime/src python -m pytest tests/test_rtime_assistant_runtime_cli.py tests/test_rtime_assistant_runtime_mcp.py -q
cd apps/feishu-bridge && .venv/bin/python -m pytest tests -q
scripts/validate-codex-plugin.py plugins/rtime-assistant-runtime
git diff --check
```
