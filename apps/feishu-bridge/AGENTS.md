# AGENTS.md - apps/feishu-bridge

This directory is imported upstream bridge code plus local candidate patches.
Deployment docs currently record the Docker/Python Feishu bridge as the live
orangepi path and the npm `lark-channel-bridge` as the rollback target. Verify
orangepi service state before making live claims.

## Boundaries

- Keep local patches small and documented.
- Preserve private-chat first behavior and access controls.
- Do not expose tool calls, prompts, shell commands, or hidden traces in normal
  Feishu/Lark replies.
- Do not commit session stores, Feishu credentials, Claude/Kimi keys, runtime
  logs, downloaded images, or generated state.

## Current Candidate Requirements

- Follow-up messages queue behind the active run; `/stop` is the explicit
  interrupt path.
- Ordinary text messages in the same chat may be merged by the debounce window
  before the next Claude run; slash commands, `/stop`, and images stay separate.
- Assistant text is sent as segmented user-visible messages.
- Status cards are for progress, stop controls, and option buttons.
- Runtime run logs are JSONL records outside git.

## Validation

```bash
python -m pytest tests -q
```

For Docker validation from the repository root:

```bash
docker compose -f compose.dev.yml run --rm feishu-bridge-tests
```
