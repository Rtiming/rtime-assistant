---
name: rtime-context
description: Use when planning dynamic context unlocking, building ContextUnlockPlan or Context Pack skeletons, explaining why context should be loaded, routing requests across brain/rtime-hub/runtime evidence, or designing why-context behavior without reading sensitive data.
---

# Rtime Context

Use this skill for read-only context planning before retrieval or action. The
stable command surface is the `rtime-context` CLI package in
`packages/rtime-context`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/context-unlocking.md`
3. `docs/context-orchestrator.md`
4. `docs/prompt-layering.md`
5. `docs/tooling-packaging.md`

## Rules

- Keep context planning read-only.
- Treat the output as a plan, not proof that context has already been loaded.
- Do not read secret file bodies, identity data, addresses, session stores, or
  credentials from this tool.
- Use `rtime-context plan` before broad cross-project retrieval when the task
  could involve `brain`, `rtime-hub`, runtime logs, or sensitive context.
- Use `rtime-context explain` when the user asks why a source or memory should
  be unlocked.
- Context Pack skeletons may list sources and recommended tools, but actual
  source reads still need task relevance and normal validation.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-context --profile mac
python -m pip install -e packages/rtime-context
rtime-context doctor
```

Read-only checks:

```bash
rtime-context plan "检查 rtime-hub 项目状态和 brain 文献引用"
rtime-context pack "重构 Feishu bridge 并跑 pytest" --workspace <repo-root>
rtime-context explain "为什么要读取 runtime logs?"
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-context-mcp
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-context/src/rtime_context/*.py
PYTHONPATH=packages/rtime-context/src python -m pytest tests/test_rtime_context_cli.py tests/test_rtime_context_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-context
git diff --check
```
