---
name: rtime-library-gateway
description: Use when an assistant needs one gated entry point to the rtime brain library and assistant data — searching/indexing/auditing the brain, hub, profile, review, automation, runtime read surfaces, or making one of the three narrow settings writes (context sources, memory candidates, reminders) — behind a single central permission gate (personal-data gating and output redaction are policy switches, both OFF in the current single-owner deployment), and metadata-only audit.
---

# Rtime Library Gateway

A thin orchestration MCP that gives every assistant (Claude Code, Claude Desktop,
Codex, Kimi, Gemini, OpenCode) one door to the rtime library. It subprocesses the
existing read CLIs and the three `deploy/bin` narrow-write tools behind one
central permission gate plus a metadata-only audit. The stable command surface is
the `rtime-library-gateway` CLI package in `packages/rtime-library-gateway`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/rtime-library-gateway.md`
3. `packages/rtime-library-gateway/policy/library-gateway-policy.json`
4. `apps/assistant-gateway/gateway.py` (source of the gate primitives)

## Method namespace

Read (`tier=read`, subprocess a read CLI, output redacted):

- `lib.doctor` — in-process self-check (roots, policy, CLI importability); no brain read.
- `lib.status` — aggregate each read surface's doctor through the gate.
- `lib.search` / `lib.get` — brain-library `index query` / `index status`.
- `lib.list` — brain-library `docpacks` / `scan`.
- `lib.meta` — brain-library `meta`: read the authoritative `_meta` rule corpus. No `name` → rule catalogue; `name=<file>` (e.g. `organize-rules`) → full rule text. **Read the relevant rule before any write.**
- `lib.docpack` — brain-docpack `audit` / `select-samples` / `validate` / `doctor`.
- `lib.citation` — brain-citation `scan` / `panel` / `doctor`.
- `lib.hub` — rtime-hub-connector `panel` / `scan` / `contacts` / `doctor` (`contacts` forces redaction).
- `lib.context` — rtime-context `doctor` / `plan` / `pack` / `explain`.
- `lib.profile` — rtime-profile `panel` / `scan` / `plan` / `doctor`.
- `lib.review` — rtime-review `panel` / `audits` / `run-logs` / `tooling` / `doctor`.
- `lib.automation` — rtime-automation `panel` / `reminders` / `health` / `doctor` (read side only).
- `lib.runtime` — rtime-runtime `doctor` / `run-log summary`.

Write (`tier=write`, narrow audited writers — each maps to one `deploy/bin` tool):

- `lib.settings.context_source_{list,check,add,deactivate}` — `deploy/bin/rtime-context-source`.
- `lib.settings.memory_candidate_add` — `deploy/bin/rtime-memory-candidate` (claim via stdin; entry forced to `library-gateway`).
- `lib.settings.reminder_{register,list,cancel}` — `deploy/bin/rtime-reminder-register`.
- `lib.contribute` (op `plan`/`stage`) — `deploy/bin/rtime-contribute`: stage an agent note into `brain/_inbox/agent`. Never finalizes.
- `lib.finalize` (op `plan`/`apply`) — `deploy/bin/rtime-finalize`: promote an `_inbox` item (file / `.zip` / dir) into `knowledge/`. `apply` refuses without an owner-issued approval (`rtime-finalize approve <plan_sha>`, owner-only, NOT a gateway method) — agents can never self-approve. `ocr`/`docpack` opt-in for scanned PDFs.
- `lib.course-intake` (op `plan`/`apply`) — `deploy/bin/rtime-course-intake`: ingest a course folder from `_inbox` into `knowledge/courses/<id>` with auto slides/lectures/exams classify. Same owner gate (`rtime-course-intake approve <plan_sha>`).

## Write paradigm

操作走网关闸，规范留 `_meta`。完整范式见 `docs/brain-write-gateway-paradigm.md`。要点：

- 任何"整理/加库/改库" agent（含外部 MCP agent、本地 Kimi）：**先 `lib.meta` 读规矩，再经网关写工具落地**，不直接写 brain 文件。
- 写工具契约 `plan(dry-run → confirmation_questions+校验) → 你确认 → apply → validate`；`blocker` 级问题必须人工，`confirm` 级按 policy 自动放行。
- `lib.contribute` 只 stage 到 `_inbox`，**永不 finalize**；`finalize`（`_inbox→knowledge`）由 owner 批准。新写工具（finalize/course-intake/organize）同款挂闸。
- 入库后该显示的进 Obsidian：课程合 `course-view-manifest.json` + `m4_link --verify ok:true`。

## Rules

- There is no general write path yet. Only the three `lib.settings.*` tool families write,
  and each one maps to a single `deploy/bin` executable. New writes follow the paradigm above
  (gate + `plan→confirm→apply→validate`); never add an ungated generic file write.
- `personal-data/**` exclusion is a policy switch (gate default `EXCLUDED_TOP_DIRS`), **OFF in the
  deployed single-owner policy** (`excluded_top_dirs: []` as of 2026-06-19) — personal-data is
  readable/searchable. Every path/index/root/source_path is still validated against the brain root
  (escape denial) before any subprocess runs; exclusion is re-enablable via policy, no code change.
- Output redaction is a policy switch (`redact_sensitive`), **OFF as of 2026-06-19** (`false`), so
  output is not redacted by default. When on, lines matching the sensitive pattern (open_id,
  app_secret, token, 验证码, `ou_...`, …) become `[redacted sensitive line]`; `contacts` forces
  redaction on regardless.
- The audit log is metadata-only: audit_id, ts, client_id, method, tier, decision,
  exit_code, duration_ms, input_path_basenames, redacted_line_count. Never argument
  bodies, claim text, reminder messages, or targets.
- Permission is policy-data driven (`library-gateway-policy.json`): flip a method's
  `enabled`, set `default_write: deny`, or fill `clients.<name>.deny` to tighten —
  no gate code change required.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool rtime-library-gateway --profile mac
python -m pip install -e packages/rtime-library-gateway
rtime-library-gateway doctor
```

Read-only checks:

```bash
rtime-library-gateway doctor
rtime-library-gateway policy-show
rtime-library-gateway call lib.status
rtime-library-gateway call lib.search --args-json '{"index": "/path/to/index.sqlite", "query": "热力学", "limit": 5}'
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | rtime-library-gateway-mcp
```

## Validation

For package, MCP, skill, or plugin changes, run:

```bash
python -m py_compile packages/rtime-library-gateway/src/rtime_library_gateway/*.py
PYTHONPATH=packages/rtime-library-gateway/src python -m pytest tests/test_rtime_library_gateway_cli.py tests/test_rtime_library_gateway_mcp.py tests/test_rtime_library_gateway_gate.py -q
scripts/validate-codex-plugin.py plugins/rtime-library-gateway
git diff --check
```
