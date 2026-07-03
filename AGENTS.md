# AGENTS.md - rtime-assistant

This repository owns the assistant runtime around the `brain` library. Read
this file before editing.

## Boundaries

- Code and deployment docs live here.
- Knowledge data and assistant memory live in `brain`, not here.
- Project status and cross-device sync tracking live in `rtime-hub`, not here.
- Secrets and runtime state stay local to orangepi or each client machine.
- 开发客户端有 Mac 和 Windows 两台，运行时是 orangepi；三者都克隆 orangepi 裸库。
  本地工作目录边界、多客户端 git 收敛、Windows 约束与本地 agent 硬规矩见
  `docs/development-workflow.md` §8。
- 爬取 / 转写 / 分析等**数据流水线**全在 Mac/Windows 跑(算力 + 可人工审核);
  orangepi 只存 brain + 跑 7×24 在线助手(不搬走)。规范(含人审门、设备工具链现状)见
  `docs/data-pipeline-norms.zh-CN.md`。

## Read Order

**分支/提交/校验/部署的流程规范见 `docs/development-workflow.md` —— 提交或部署前必读。**

1. `README.md`
2. `docs/overview.md`
3. `docs/architecture.md`
4. `docs/project-map.zh-CN.md`
5. `docs/module-submit-workflow.md` when preparing focused module-level
   changes or deciding which checks are enough for a single module submission
6. `docs/prompt-layering.md`
7. `docs/assistant-profile-policy.md` when changing assistant persona, model,
   permission, output, memory, sensitive-data, skill, plugin, or MCP policy
8. `docs/context-fabric-modules.zh-CN.md` when changing context, memory, or
   knowledge-system direction
9. `docs/logging-and-audit.md` when changing runtime logging, development logs,
   or validation archives
9a. `docs/design/chat-archive-storage-2026-07.zh-CN.md` when changing live or
    historical chat archive, transcript, group-message storage, media archive,
    chat retention, or memory-candidate evidence plumbing
10. `docs/docker-workflow.md` when changing Docker/Compose behavior
11. `docs/docker-production.md` when changing production Compose deployment,
    image build, health checks, cutover, or rollback
12. `docs/tooling-packaging.md` when changing scripts, CLI tools, skills,
   plugins, or MCP surfaces
13. `docs/tooling-installation.md` when changing local install/sync behavior
   for Codex, Claude Code, plugins, or MCP clients
13a. `docs/agent-control-mcp.md` when changing agent control-plane tooling,
    MCP config rendering, validation planning, or agent-facing debug surfaces
14. `docs/brain-library-index.md` when changing library index, Obsidian,
    Zotero, or DocPack readiness scans
15. `docs/brain-citation.md` when changing Obsidian/Zotero citation
    crosswalks, citekey coverage, or citation review risks
16. `docs/obsidian-assistant-plugin.md` when changing the Obsidian side-panel
    assistant plugin, sidebar UI, active-note context payloads, or plugin
    install behavior
17. `docs/obsidian-composer-contract.md` when changing Obsidian composer
    templates, target modules, folder hints, or request route hints
18. `docs/brain-zotero-pdf-workflow.zh-CN.md` when changing PDF canonical
    storage, Zotero linked/stored attachment policy, WebDAV endpoints, or
    Obsidian PDF-note boundaries
18a. `docs/brain-intake-workflow.zh-CN.md` when changing material intake,
    inbox filing, conversion decisions, or assistant organization behavior
19. `docs/brain-zotero-webdav-ops.zh-CN.md` when changing WebDAV deployment
    scripts, Orange Pi rclone containers, Nginx reverse proxy examples, Zotero
    WebDAV verification, or migration runbooks
20. `docs/rtime-hub-connector.md` when changing project/device/contact hub
    connector behavior
21. `docs/context-orchestrator.md` when changing dynamic context planning,
    ContextUnlockPlan, Context Pack, or why-context behavior
22. `docs/review-console.md` when changing review surfaces, audit panels,
    failed-run panels, memory-candidate summaries, or tooling-health review
23. `docs/automation-and-reminders.md` when changing reminders, schedulers,
    notification wiring, workflow automation, timer checks, or automation MCP
24. `docs/runtime-assets.md`
25. `docs/deployment.md`
26. `docs/bridge-requirements.md` when changing the bridge surface
27. `docs/wechat-bridge-development.zh-CN.md` when changing the WeChat/ClawBot
    bridge, OpenClaw Weixin migration, iLink protocol client, WeChat media
    send/receive, or WeChat reminder delivery
28. `docs/workflows.md`
29. `docs/ui-guide.md`
30. `docs/runbook.md`
31. `docs/troubleshooting.md`
32. `docs/archive/inventory-2026-06-08.md` when checking historical state
    (archived snapshot; see `docs/archive/README.md`)
33. `docs/ai-readable-markdown-standard.zh-CN.md`,
    `docs/brain-visualmd-module.zh-CN.md`, and
    `docs/brain-visualmd-tools.zh-CN.md` when changing the material →
    AI-readable Markdown conversion standard (quality baseline / tiers), the
    visual transcription module (`packages/brain-visualmd/`) and its pluggable
    transcribe backend, or the conversion-tool / local-VLM selection
34. `docs/data-pipeline-norms.zh-CN.md` when changing where/how data is crawled,
    transcribed, analyzed, human-reviewed, or promoted into `brain` (compute on
    Mac/Windows; orangepi = storage + 24×7 assistant; staging → review → 入库)

For library behavior, read `<brain-root>/CLAUDE.md` on the runtime host or
`<local-brain-mount>/CLAUDE.md` on the development machine.

## Edit Rules

- Do not commit `.env`, tokens, API keys, session stores, logs, or generated
  state.
- Do not move or rewrite `brain` data from this repository.
- Do not edit systemd live units directly as the only source of truth; update
  templates here and deploy intentionally.
- Do not copy Docker databases, model caches, device-tool credentials, Lark
  sessions, or Kimi keys into this repository.
- Do not commit `work/standards-audit/`, runtime run logs, generated DocPacks,
  generated indexes, or local Docker state.
- Avoid cross-surface drift: when one fact lives on many surfaces (a tool's
  name/version across `pyproject.toml` + `plugin.json` + `.mcp.json` + wrapper; a
  category list; a model id; a port), prefer one source of truth and derive the
  rest; where you cannot, mark the coupling with a greppable
  `KEEP IN SYNC: <target>` comment at the edit site. Canonical
  `skills/<n>/SKILL.md` is the source and `plugins/<n>/skills/<n>/SKILL.md` is its
  derived copy. `scripts/check-entrypoint-drift.py` runs in the commit gate and
  guards the mechanical couplings. See `docs/maintainability-standards.zh-CN.md`.
- Treat `schemas/docpack/` and `scripts/validate-docpack.py` as the current
  contract for generated DocPack metadata.
- Treat `packages/brain-docpack/` as the stable CLI contract for DocPack
  automation. Keep legacy scripts working until all users migrate.
- Treat `packages/brain-library/` as the CLI/MCP contract for library index
  diagnostics and explicit derived SQLite/BM25 indexes. It may inspect `brain`;
  writes require an explicit `--out`, MCP stays read-only, and it must not
  modify Obsidian files or sync Zotero data.
- Treat `packages/brain-citation/` as the CLI/MCP contract for Obsidian/Zotero
  citation crosswalks. It may inspect Markdown citekeys, BibTeX exports,
  Zotero URI clues, wikilinks, and DocPack citation anchors, but it must not
  write Obsidian files, sync Zotero data, mutate DocPacks, or read secrets.
- Treat `packages/rtime-hub-connector/` as the CLI/MCP contract for consuming
  `rtime-hub` project, device, contact, service, task, and deployment panels.
  It must remain read-only and must not duplicate hub facts into this repo.
- Treat `packages/rtime-context/` as the CLI/MCP contract for dynamic context
  planning. It may produce ContextUnlockPlan and Context Pack skeletons, but it
  must not read secret bodies, write memories, deploy, restart, or perform
  action-state changes.
- Treat `packages/rtime-profile/` as the CLI/MCP contract for assistant
  profile and policy diagnostics. It may inspect source metadata and plan
  persona, prompt, model, permission, output, memory, sensitive-data, and
  tooling adjustments, but it must not edit `brain/CLAUDE.md`, runtime config,
  prompts, permissions, or secrets.
- Treat `packages/rtime-automation/` as the CLI/MCP contract for automation
  and reminder diagnostics. It may inspect reminder metadata, timer templates,
  notification wiring, and workflow lanes, but it must not send messages,
  write reminders, deploy, restart services, edit live units, read secrets, or
  return reminder message text or target values.
- Treat `packages/rtime-review/` as the CLI/MCP contract for review-console
  data surfaces. It may summarize audits, run logs, failures, memory candidate
  counts, permissions, and tooling health, but it must not approve candidates,
  mutate logs, write memory, deploy, or restart services.
- Treat `packages/rtime-agent-control/` as the CLI/MCP contract for
  agent-facing tool inventory, MCP config rendering, validation planning,
  context-lane planning, and runtime source snapshots. It must stay read-only:
  no arbitrary shell, config writes, deploy/restart, message sends, secret
  reads, memory writes, reminder writes, hub writes, or Obsidian/DocPack
  mutations.
- Treat `apps/obsidian-rtime-assistant/` as the Obsidian entry adapter. It may
  render side-panel UI, collect active-note context, call a configured local
  rtime endpoint, and explicitly insert the last answer at the cursor. It must
  not store provider keys, start model runtimes, auto-rewrite notes, mutate
  Zotero, write DocPacks, or become the retrieval/index engine.
- Treat `skills/` as versioned skill source, not proof that a skill is
  installed in Codex, Claude Code, or orangepi runtime.
- Treat `plugins/` as versioned plugin source, not proof that a plugin is
  installed in any local marketplace.
- Keep plugin `.mcp.json` files tied to implemented, validated servers only.
  Read `docs/brain-docpack-mcp.md` before changing DocPack MCP behavior.
- Treat `apps/feishu-bridge/` as imported upstream code. Keep local patches
  small and documented.
- For service changes, update `docs/deployment.md` or `docs/runbook.md` in the
  same change.
- For Docker changes, keep dev/test Compose separate from production cutover.
  The Docker/Python Feishu bridge is the documented 2026-06-13 live path, but
  agents must still verify orangepi service state before making live claims.
  The npm bridge remains a rollback target and must not run against the same
  Feishu app at the same time.

## Validation

Before handoff, run the smallest relevant checks:

```bash
git status --short
git diff --check
scripts/module-submit-check.py --changed --dry-run
scripts/audit-env.sh
```

For Feishu bridge changes, run tests from `apps/feishu-bridge/` when Python
dependencies are available.

For Docker candidate changes, also run:

```bash
docker compose -f compose.dev.yml config
scripts/docker-dev-check.sh --service feishu-bridge-tests
scripts/docker-dev-check.sh --service docpack-tests
```

For Docker production changes, also run:

```bash
bash -n scripts/docker-prod-check.sh
docker compose --env-file deploy/env/feishu-bridge.prod.env.example -f compose.prod.yml config
scripts/docker-prod-check.sh --env-file deploy/env/feishu-bridge.prod.env.example --config --dry-run
```

For DocPack CLI changes, also run:

```bash
PYTHONPATH=packages/brain-docpack/src python -m pytest tests/test_brain_docpack_cli.py tests/test_brain_docpack_mcp.py -q
```

For tooling installation changes, also run:

```bash
bash -n scripts/install-rtime-tooling.sh
bash -n scripts/install-brain-docpack-tooling.sh
PYTHONPATH=packages/brain-library/src:packages/brain-citation/src:packages/brain-docpack/src:packages/rtime-assistant-runtime/src:packages/rtime-hub-connector/src:packages/rtime-context/src:packages/rtime-profile/src:packages/rtime-automation/src:packages/rtime-review/src:packages/rtime-agent-control/src python -m pytest tests/test_install_rtime_tooling.py -q
PYTHONPATH=packages/brain-docpack/src python -m pytest tests/test_brain_docpack_install_tooling.py -q
scripts/install-rtime-tooling.sh --skip-cli --profile mac --check-installed --skip-codex-skill --skip-claude-skill --skip-codex-plugin --no-mcp-snippets
```

For library index tooling changes, also run:

```bash
python -m py_compile packages/brain-library/src/brain_library/*.py
PYTHONPATH=packages/brain-library/src python -m pytest tests/test_brain_library_cli.py tests/test_brain_library_mcp.py -q
scripts/validate-codex-plugin.py plugins/brain-library
```

For citation crosswalk tooling changes, also run:

```bash
python -m py_compile packages/brain-citation/src/brain_citation/*.py
PYTHONPATH=packages/brain-citation/src python -m pytest tests/test_brain_citation_cli.py tests/test_brain_citation_mcp.py -q
scripts/validate-codex-plugin.py plugins/brain-citation
```

For brain/Zotero WebDAV deployment-script changes, also run:

```bash
bash -n scripts/brain-zotero-webdav.sh
scripts/brain-zotero-webdav.sh plan
CREDENTIAL_ENV=~/Zotero/rtime-webdav-credentials.env scripts/brain-zotero-webdav.sh verify
```

For Obsidian assistant plugin changes, also run:

```bash
cd apps/obsidian-rtime-assistant && npm run build
```

For hub connector tooling changes, also run:

```bash
python -m py_compile packages/rtime-hub-connector/src/rtime_hub_connector/*.py
PYTHONPATH=packages/rtime-hub-connector/src python -m pytest tests/test_rtime_hub_connector_cli.py tests/test_rtime_hub_connector_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-hub-connector
```

For context planner tooling changes, also run:

```bash
python -m py_compile packages/rtime-context/src/rtime_context/*.py
PYTHONPATH=packages/rtime-context/src python -m pytest tests/test_rtime_context_cli.py tests/test_rtime_context_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-context
```

For assistant profile policy tooling changes, also run:

```bash
python -m py_compile packages/rtime-profile/src/rtime_profile/*.py
PYTHONPATH=packages/rtime-profile/src python -m pytest tests/test_rtime_profile_cli.py tests/test_rtime_profile_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-profile
```

For review-console tooling changes, also run:

```bash
python -m py_compile packages/rtime-review/src/rtime_review/*.py
PYTHONPATH=packages/rtime-review/src python -m pytest tests/test_rtime_review_cli.py tests/test_rtime_review_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-review
```

For agent control-plane tooling changes, also run:

```bash
python -m py_compile packages/rtime-agent-control/src/rtime_agent_control/*.py
PYTHONPATH=packages/rtime-agent-control/src python -m pytest tests/test_rtime_agent_control_cli.py tests/test_rtime_agent_control_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-agent-control
```

For automation/reminder tooling changes, also run:

```bash
python -m py_compile packages/rtime-automation/src/rtime_automation/*.py
PYTHONPATH=packages/rtime-automation/src python -m pytest tests/test_rtime_automation_cli.py tests/test_rtime_automation_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-automation
```

For runtime diagnostics tooling changes, also run:

```bash
python -m py_compile packages/rtime-assistant-runtime/src/rtime_assistant_runtime/*.py
PYTHONPATH=packages/rtime-assistant-runtime/src python -m pytest tests/test_rtime_assistant_runtime_cli.py tests/test_rtime_assistant_runtime_mcp.py -q
scripts/validate-codex-plugin.py plugins/rtime-assistant-runtime
```
