# rtime-assistant

English · [中文](README.zh-CN.md)

Personal assistant runtime for the `brain` knowledge system.

This repository is the engineering home for the assistant services that run on
orangepi and operate on the central `brain` library. It is separate from the
library data itself.

## Roles

The paths below are instance-level settings. The owner deployment uses concrete
local values; downstream deployments should set their own equivalents through
env files and setup outputs.

- Development tree: `<dev-tree>/rtime-assistant`
- Runtime checkout: `<runtime-host>:~/rtime-assistant`
- Runtime bare git remote: `<runtime-host>:~/rtime-assistant.git`
- Data/library root: `<brain-root>`
- Optional local library mount: `<local-brain-mount>`

## What Lives Here

- `apps/feishu-bridge/`: imported Feishu to Claude Code bridge source.
- `apps/obsidian-rtime-assistant/`: Obsidian side-panel assistant plugin that
  calls a configured local rtime endpoint with active-note context. It now has
  a Chinese-first icon-led sidebar UI with an English settings switch; it is a
  thin entry adapter, not the retrieval engine or memory store. Its standalone
  package emits a private `release.json` so desktop Obsidian clients can update
  from a trusted Orange Pi or server-hosted static folder.
- `apps/assistant-gateway/`: stdlib-only HTTP gateway for the Obsidian plugin
  to call `claude-kimi` against the `brain` workspace with read-only path and
  tool guardrails.
- `docs/wechat-bridge-development.zh-CN.md`: development archive for the
  planned self-owned WeChat/ClawBot bridge. It records the route for reusing
  the already configured OpenClaw Weixin account as a migration source, then
  moving the channel into a modular `rtime-assistant` bridge with existing
  Feishu runtime semantics.
- `apps/reminder-sender/`: reminder sender source copied from the live helper.
- `deploy/bin/`: local command templates for orangepi and Docker runtime
  (`claude-rtime`, `claude-kimi`, `claude-deepseek`, `claude-qwen`, `kb`).
- `deploy/nginx/`: reverse-proxy examples for services fronted by `sh-core`.
  These examples contain no credentials and must be validated on the target
  host.
- `deploy/systemd/user/`: user service templates for orangepi.
- `scripts/`: deployment, audit, and log helpers.
- `schemas/`: rebuildable data contracts, starting with DocPack JSON schemas.
- `scripts/build-docpack.py`, `scripts/validate-docpack.py`, and
  `scripts/select-docpack-samples.py`: deterministic DocPack builder,
  validator, and sample-selection seeds for the learning-material pipeline.
  The builder currently supports Markdown/text, PDF, and LibreOffice-backed
  Office-to-PDF conversion in candidate/test environments.
- `brain-docpack course-intake`: reusable course-material intake planner in
  `packages/brain-docpack`. It classifies downloaded course PDFs/Office files,
  probes PDF text-layer quality, chooses conservative Markdown conversion
  strategies, and can copy approved materials into
  `brain/knowledge/courses/<course-id>/` with intake reports. The current
  Thermal/Statistical Physics batch is an experimental draft record only, not
  a template for future course organization.
- `scripts/install-rtime-tooling.sh`: dry-run-first installer/sync helper for
  all repository-owned CLI packages, Codex skills, Claude Code skills, Codex
  plugins, marketplace entries, MCP wrappers, combined standalone MCP config
  JSON, and read-only installed-surface health checks on Mac or orangepi.
- `scripts/docker-dev-check.sh`: timeout-and-cleanup wrapper for Docker test
  targets, used instead of bare `docker compose run`.
- `scripts/docker-prod-check.sh`: production Compose wrapper for config, build,
  smoke, up, ps, logs, and down.
- `scripts/rtime`: unified thin router that forwards a verb to existing
  commands (`doctor`, `dev`, `module`, `runtime`, `gateway`, `deploy`, `mcp
  list`, `check`). It implements no logic of its own; truth sources stay the
  package `[project.scripts]` and `module-submit.json`. Run `scripts/rtime
  --help`; task-oriented navigation is in `docs/entrypoints.zh-CN.md`.
- `scripts/module-submit-check.py` and `module-submit.json`: per-module
  submission map and executable checks so focused app/package/delivery changes
  can be validated independently before broader repo checks.
- `scripts/brain-zotero-webdav.sh`: dry-run-first deployment/status/verify
  helper for the Orange Pi `brain`/Zotero WebDAV stack. It owns container
  shape only; credentials remain local runtime state.
- `scripts/brain-intake/`: executable seeds for the material-intake and memory
  loop pipeline. The implemented scripts are narrow, tested helpers; unfinished
  pipeline stages remain documented contracts.
- `scripts/install-brain-docpack-tooling.sh`: compatibility installer for the
  DocPack-only surface.
- `packages/brain-docpack/`: installable CLI package exposing the current
  DocPack command contract while delegating to the tested script entrypoints.
- `packages/brain-library/`: installable read-only CLI/MCP package for
  library index diagnostics and explicit derived SQLite indexes — BM25 plus an
  optional semantic-vector hybrid (schema 4, sqlite-vec + RRF; degrades to BM25
  when no embedding model is present): Obsidian signals, Zotero clues, DocPack
  citation readiness, index build, status, and query.
- `packages/brain-citation/`: installable read-only CLI/MCP package for
  Obsidian/Zotero citation crosswalks: Markdown citekeys, BibTeX coverage,
  Zotero URI clues, wikilinks, DocPack citation anchors, and review risks.
- `packages/brain-visualmd/`: installable CLI/MCP package for visual-first
  strict transcription of source materials (PDF/PPT/DOCX/images) into
  AI-readable Markdown — full-page render, pluggable page-by-page transcribe
  backend (formulas as block LaTeX, figures described, unclear items flagged
  存疑), and machine gates. Standalone and deliberately not yet wired into the
  main intake pipeline; it does not publish into `brain` (outputs land in a
  staging dir) until proven on real materials.
- `packages/ustc-kb/`: deterministic crawl/archive/index package for USTC
  campus public materials. Crawling uses no LLM; raw HTML and files are archived
  and name-searchable for the rtime assistant to retrieve. Public materials go
  to `knowledge/`; login-gated personal data follows the `personal-data/` norm.
- `packages/rtime-agent-control/`: installable read-only CLI/MCP package for
  agent control-plane diagnostics, MCP client config rendering, module
  validation planning, context-lane routing, and runtime source snapshots.
- `packages/rtime-library-gateway/`: installable CLI/MCP gateway that puts one
  central permission gate plus metadata-only audit in front of the existing
  rtime read CLIs and the three narrow `deploy/bin` settings writers, exposing
  them as `lib.*` methods without reimplementing any read logic.
- `packages/rtime-assistant-runtime/`: installable read-only CLI/MCP
  diagnostics for bridge files, run logs, systemd templates, and Docker
  production preflight checks.
- `packages/rtime-hub-connector/`: installable read-only CLI/MCP connector for
  `rtime-hub` project, device, contact, service, task, and deployment panels.
- `packages/rtime-context/`: installable read-only CLI/MCP context
  orchestrator seed for ContextUnlockPlan, Context Pack skeleton, and
  why-context explanation.
- `packages/rtime-profile/`: installable read-only CLI/MCP assistant profile
  and policy diagnostics for persona, prompt, model, permission, output,
  memory, sensitive-data, and tooling adjustment planning.
- `packages/rtime-automation/`: installable read-only CLI/MCP automation and
  reminder diagnostics for JSONL reminder metadata, timers, notification
  wiring, workflow lanes, and proposal-only automation planning.
- `packages/rtime-review/`: installable read-only CLI/MCP review-console data
  surface for audit archives, run logs, failed runs, memory-candidate counts,
  permission summaries, and tooling health.
- `skills/brain-docpack/`: repository-owned skill source for future Codex and
  Claude Code use; install/sync separately on each client.
- `skills/brain-intake/`: repository-owned intake workflow skill for
  Obsidian/Web/Feishu/CLI files, inbox tickets, sensitive-material holds, and
  courseware batch planning.
- `skills/brain-library/`: repository-owned skill source for read-only library
  indexing, Obsidian/Zotero review, and Web console planning.
- `skills/brain-citation/`: repository-owned skill source for read-only
  Obsidian/Zotero citation crosswalk review on Mac and orangepi.
- `skills/brain-yuque-ingest/`: repository-owned skill source for pulling Yuque
  (语雀) organization/team/personal knowledge-base materials into `brain`.
- `skills/rtime-agent-control/`: repository-owned skill source for read-only
  agent control-plane diagnostics, MCP client config rendering, tooling
  inventory, and validation planning.
- `skills/rtime-library-gateway/`: repository-owned skill source for the single
  gated entry point to the brain library and the three narrow settings writes.
- `skills/rtime-reminder/`: repository-owned skill source for registering,
  listing, cancelling, and diagnosing rtime Feishu reminders via the JSONL
  reminder path (never Claude Code Cron).
- `skills/reminder/`: repository-owned Chinese-first skill source for the same
  rtime Feishu reminder registration path.
- `skills/rtime-assistant-runtime/`: repository-owned runtime diagnostics skill
  for Feishu/Lark bridge checks, run logs, and deployment guardrails.
- `skills/rtime-hub-connector/`: repository-owned read-only hub connector skill
  for project/device/contact panels on Mac and orangepi.
- `skills/rtime-context/`: repository-owned context planning skill for
  dynamic context unlocking and why-context workflows.
- `skills/rtime-profile/`: repository-owned assistant profile and policy
  skill for persona/model/permission/output adjustment planning on Mac and
  orangepi.
- `skills/rtime-automation/`: repository-owned automation/reminder skill for
  read-only scheduler, notification, workflow, and timer health review.
- `skills/rtime-review/`: repository-owned review-console skill for
  governance panels and audit/run-log review.
- `plugins/brain-docpack/`: repository-owned Codex plugin source bundling the
  DocPack skill, helper scripts, and read-only MCP stdio config; not installed
  automatically.
- `plugins/brain-library/`: repository-owned Codex plugin source bundling the
  library-index skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/brain-citation/`: repository-owned Codex plugin source bundling the
  citation-crosswalk skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-agent-control/`: repository-owned Codex plugin source bundling
  the agent-control skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-library-gateway/`: repository-owned Codex plugin source
  bundling the library-gateway skill and the `lib.*` MCP stdio config; not
  installed automatically.
- `plugins/rtime-reminder/`: repository-owned Codex plugin source bundling the
  reminder skill and the narrow write-capable reminder MCP stdio config; not
  installed automatically.
- `plugins/rtime-assistant-runtime/`: repository-owned Codex plugin source
  bundling runtime diagnostics skill and read-only MCP stdio config; not
  installed automatically.
- `plugins/rtime-hub-connector/`: repository-owned Codex plugin source bundling
  hub connector skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-context/`: repository-owned Codex plugin source bundling the
  context planning skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-profile/`: repository-owned Codex plugin source bundling the
  assistant profile/policy skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-automation/`: repository-owned Codex plugin source bundling
  the automation/reminder skill and read-only MCP stdio config; not installed
  automatically.
- `plugins/rtime-review/`: repository-owned Codex plugin source bundling the
  review-console skill and read-only MCP stdio config; not installed
  automatically.
- `docker/`, `compose.dev.yml`, and `compose.prod.yml`: Docker test, candidate,
  and production Compose paths for the Python bridge. The production model
  entry is `claude-rtime`, which separates Kimi/DeepSeek/Qwen Code wrappers
  from USTC chat-only models.
- `docs/`: architecture, inventory, deployment guide, and runbook.

## What Does Not Live Here

- `brain` content, notes, PDFs, personal data, and memories.
- API keys, app secrets, session stores, runtime logs, or generated state.
- `rtime-hub` project tracking docs.

## First Checks

```bash
git status --short --branch
scripts/audit-env.sh
scripts/rtime --help        # unified entry: what verb runs what (see docs/entrypoints.zh-CN.md)
scripts/rtime check         # offline commit gates: entrypoint drift + portability
```

On orangepi:

```bash
systemctl --user status lark-bridge reminder.timer
```

## Reusable Modules

This is one repository, not a collection of split-out projects. Some directories
are still useful as independent modules because they have their own contract,
entry command, docs, and validation checks.

Recommended reusable modules:

| Module | Path | How to use it independently | Check |
|---|---|---|---|
| Obsidian assistant plugin | `apps/obsidian-rtime-assistant/` | Build or package the side-panel plugin and point it at a local/gateway endpoint. | `scripts/module-submit-check.py --module obsidian-plugin` |
| DocPack tooling | `packages/brain-docpack/` | Install/run the CLI facade for DocPack validation, sample selection, MCP, and course intake. | `scripts/module-submit-check.py --module brain-docpack` |
| Brain library index | `packages/brain-library/` | Run read-only scans and explicit derived SQLite indexes (BM25 + optional semantic-vector hybrid) over a `brain` root. | `scripts/module-submit-check.py --module brain-library` |
| Citation crosswalk | `packages/brain-citation/` | Audit Markdown citekeys, BibTeX coverage, Zotero URI clues, and DocPack anchors. | `scripts/module-submit-check.py --module brain-citation` |
| Agent interface tools | `scripts/rtime-vault.py`, `scripts/rtime-zotero.py`, `scripts/rtime-tools-mcp.py` | Resolve vault/brain PDF paths, query Zotero/BBT read-only, and expose local stdio MCP tools for agents. | `scripts/module-submit-check.py --module agent-interface-tools` |

Reusable as templates:

| Module | Path | Template value |
|---|---|---|
| Assistant gateway | `apps/assistant-gateway/` | Minimal read-only HTTP gateway from an editor plugin to a local/remote Claude Code workspace. |
| Brain/Zotero WebDAV | `scripts/brain-zotero-webdav.sh` + `docs/brain-zotero-webdav-ops.zh-CN.md` | Dry-run-first WebDAV deployment pattern for a single canonical PDF library. |
| Docker delivery | `compose.dev.yml`, `compose.prod.yml`, `docker/` | Mac-to-Linux validation and production-candidate Compose layout. |
| Feishu bridge runtime | `apps/feishu-bridge/` | Chat bridge patterns: queueing, output policy, model wrapper routing, and run logs. |

Internal seed modules such as context planning, assistant profile diagnostics,
automation, review console, and memory loop are kept here for project evolution
and validation. They are not presented as standalone public modules yet.

## Submission Shape

The normal deliverable is a single `rtime-assistant` repository commit. Use
`module-submit.json` and `scripts/module-submit-check.py` as a validation
matrix, not as a requirement to split the work into many commits.

For the current module list and checks:

```bash
scripts/module-submit-check.py --list
scripts/module-submit-check.py --changed --dry-run
```

Focused module commits are still useful for future isolated changes, but this
reusable-module organization is intended to land as one repo-level commit. See
`docs/module-submit-workflow.md` and `docs/project-map.zh-CN.md`.

## Deployment Shape

Developers edit this repository, commit to git, and push to the configured
runtime bare repository such as `<runtime-host>:~/rtime-assistant.git`. The
runtime host pulls from that bare repository into its checkout and runs services
from the checked-out tree.

Do not use Syncthing for this repository's runtime code. The services on
orangepi should only see deliberate git updates.

## Model Runtime Shape

The Feishu bridge calls one CLI entrypoint:

```text
CLAUDE_CLI_PATH=/usr/local/bin/claude-rtime
```

`claude-rtime` keeps model routes modular:

- `kimi` and ordinary Claude model IDs delegate to `claude-kimi`, preserving
  Claude Code tools, permissions, MCP, and audit behavior.
- `deepseek-code` delegates to `claude-deepseek`, which configures DeepSeek's
  Anthropic-compatible Claude Code path.
- `qwen-code` delegates to `claude-qwen`, which configures Qwen Model Studio's
  Anthropic-compatible Claude Code path.
- `ds`, `qwen`, and `qwen-reasoner` stay on the USTC OpenAI-compatible chat
  path and do not execute Claude Code tools.

Provider keys are mounted as external files on the server. They are not stored
in this repository and are not required unless the corresponding model route is
selected. On the assistant gateway path, USTC defaults to
`~/.config/rtime-assistant/ustc-api-key` when `RTIME_USTC_API_KEY_FILE` is not
set. If an Obsidian request needs local file tools but still carries a
chat-only model selection, the gateway falls back to the default tool-capable
model and returns `model_warning`.

## Documentation Map

- `docs/architecture.md`: component boundaries and data flow.
- `docs/ai-readable-markdown-standard.zh-CN.md`: quality baseline for turning
  any material into AI-readable Markdown (visual-first strict transcription
  tiers; what may feed AI explanation vs. search only).
- `docs/brain-visualmd-module.zh-CN.md`: engineering design for the strict
  visual transcription module (pluggable transcribe backend, brain integration,
  index demotion of weak draft md).
- `docs/brain-visualmd-tools.zh-CN.md`: 2026-06 survey of doc→markdown tools and
  small locally-deployable VLMs (formula→LaTeX + Chinese), with deploy notes and
  a benchmark plan for picking the transcription backend.
- `docs/data-pipeline-norms.zh-CN.md`: norm for the crawl → transcribe/analyze →
  human-review → 入库 pipeline. Compute (crawl/transcribe/analyze) runs on
  Mac/Windows; orangepi = brain storage + 24×7 assistant; staging→review→promote.
- `docs/overview.md`: project purpose and current live shape.
- `docs/project-map.zh-CN.md`: Chinese project map, module status, and standard
  development sequence.
- `docs/open-source-update-goals.zh-CN.md`: open-source readiness goal, update
  mechanism target behavior, and the owner/studentunion/downstream boundary.
- `docs/module-submit-workflow.md`: per-module submission boundaries, check
  tiers, report generation, and Docker/network speed notes.
- `docs/runtime-assets.md`: live paths, services, and what is deliberately not
  committed.
- `docs/component-deep-dive.md`: concrete component responsibilities, live
  service facts, candidate bridge capabilities, and live-template sync notes.
- `docs/context-unlocking.md`: dynamic memory and context unlocking target
  architecture.
- `docs/context-orchestrator.md`: first executable Context Orchestrator seed
  for ContextUnlockPlan, Context Pack skeleton, and why-context surfaces.
- `docs/review-console.md`: first read-only review-console data contract for
  audit, run-log, memory-candidate, permission, and tooling panels.
- `docs/context-unlocking-brief.zh-CN.md`: Chinese brief for explaining the
  dynamic context unlocking route to collaborators.
- `docs/context-fabric-modules.zh-CN.md`: modular notes from external feedback
  on local-first context, routing, indexing, memory diffs, and sensitivity.
- `docs/platform-modular-scenarios.zh-CN.md`: platform-level modularization and
  scenario analysis comparing the local system with Memexa-style personal
  memory graphs, covering personal communication memory, brain knowledge,
  public Q&A, organization knowledge bases, accounting, Obsidian, and service
  exposure boundaries.
- `docs/platform-modularization-plan.zh-CN.md`: implementation plan for turning
  the platform analysis into functional blocks, service blocks, data blocks,
  interface blocks, and staged extraction criteria without prematurely
  splitting the repository or services.
- `docs/prompt-layering.md`: global/project/task/dynamic prompt layering and
  precedence.
- `docs/assistant-profile-policy.md`: read-only assistant profile and policy
  diagnostics contract for persona, model, permission, output, memory,
  sensitive-data, and tooling adjustments.
- `docs/automation-and-reminders.md`: read-only automation/reminder contract
  for JSONL reminder metadata, timers, notification wiring, workflow lanes,
  and proposal-only planning.
- `docs/logging-and-audit.md`: runtime, service, development, and standards
  audit log policy.
- `docs/docker-workflow.md`: staged Docker/Compose workflow for Mac development
  and Linux runtime.
- `docs/docker-production.md`: production Compose deployment, validation,
  cutover, operations, and rollback.
- `docs/instance-deploy.zh-CN.md`: release-tag downstream instance layout and
  `deploy/update.sh` check/apply/rollback/status workflow.
- `docs/tooling-packaging.md`: packaging levels for scripts, CLI packages,
  Docker workers, Codex/Claude skills, plugins, and MCP servers.
- `docs/tooling-installation.md`: Mac/orangepi installation and sync workflow
  for DocPack, library-index, citation, runtime, hub-connector, context,
  profile, automation, and review CLI/skill/plugin/MCP surfaces.
- `docs/brain-library-module.md`: **top-level index for the brain knowledge-library
  module** — which open-access door is live vs planned, editing/authoring standards,
  authoritative-vs-derived code map, the controlled course-view vocabulary, and
  cross-device/Windows notes. Start here for the brain library.
- `docs/brain-docpack-mcp.md`: read-only MCP contract and implementation notes
  for DocPack tooling.
- `docs/brain-library-index.md`: library index diagnostics and derived
  SQLite contract (BM25 + semantic-vector hybrid, schema 4) for Obsidian,
  Zotero, DocPack, and future review-console modules.
- `docs/brain-citation.md`: read-only Obsidian/Zotero citation crosswalk
  contract for citekeys, BibTeX coverage, Zotero URI clues, wikilinks, and
  DocPack citation anchors.
- `docs/obsidian-assistant-plugin.md`: Obsidian side-panel assistant plugin
  contract, bilingual UI direction, HTTP payload shape, data policy, and
  upgrade path.
- `apps/assistant-gateway/README.md`: read-only HTTP gateway contract for the
  Obsidian plugin to call the `brain` workspace through `claude-kimi`.
- `docs/brain-zotero-pdf-workflow.zh-CN.md`: single-PDF canonical workflow for
  `brain`, Zotero linked attachments, Zotero WebDAV cache exceptions, WebDAV
  upload endpoints, and Obsidian note/index boundaries.
- `docs/brain-intake-workflow.zh-CN.md`: executable intake and organization
  contract for AI assistants: Obsidian/Web/Feishu/CLI entry adapters,
  `_inbox` ticket flow, eight-step filing, conversion decisions, sensitive
  material handling, and index/memory hookup.
- `docs/memory-loop.zh-CN.md`: adaptive memory-loop design contract and phased
  implementation plan; current executable seeds live under `scripts/brain-intake/`.
- `docs/tasks/`: self-contained Codex subtask prompts for the intake/memory
  rollout, with a shared route brief and per-task acceptance criteria.
- `docs/brain-zotero-webdav-ops.zh-CN.md`: deploy, verify, configure, and
  troubleshoot the Orange Pi WebDAV services that support the PDF workflow.
- `docs/brain-zotero-webdav-deployment-2026-06-10.md`: dated deployment and
  existing-Zotero migration evidence from the first WebDAV rollout.
- `docs/rtime-hub-connector.md`: read-only connector contract for consuming
  `rtime-hub` project, device, contact, service, task, and deployment panels.
- `docs/refactor-roadmap.md`: phased implementation order for modular runtime,
  knowledge tooling, review surfaces, and automation.
- `docs/knowledge-material-docpack-plan.md`: implementation plan for learning
  material DocPack conversion tooling; outputs stay in `brain`.
- `docs/memory-references.md`: papers, open-source systems, and evaluation
  notes for long-lived assistant memory.
- `docs/memory-loop.zh-CN.md`: self-adapting memory loop design (cards,
  five loops, hypothesis modeling, vector plan) benchmarked against OpenClaw
  and Hermes.
- `docs/vision-and-landscape.zh-CN.md`: project positioning, competitive
  landscape survey, and near/mid/long-term outlook.
- `docs/obsidian-vault-layout.zh-CN.md`: vault/Zotero hierarchy design:
  stock-vs-work layer separation, semester-based course folders, curated
  symlink entries, growth and link rules.
- `docs/bridge-requirements.md`: launch requirements for the future Python
  Feishu/Lark bridge migration.
- `docs/wechat-bridge-development.zh-CN.md`: modular development plan and
  current evidence archive for the future WeChat/ClawBot bridge.
- `docs/workflows.md`: normal development, deployment, and service update flows.
- `docs/ui-guide.md`: Feishu, CLI, and systemd interfaces.
- `docs/troubleshooting.md`: focused failure diagnosis.
- `docs/runbook.md`: daily operational commands.
