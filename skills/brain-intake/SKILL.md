---
name: brain-intake
description: Use when the user asks to file, organize, ingest, upload, or batch-process PDFs, courseware, images, web clips, Feishu attachments, Obsidian attachments, or any material into the rtime brain library.
---

# Brain Intake

Use this skill for material intake into the rtime `brain` library. It turns
incoming files into auditable inbox tickets and then runs the repository's
brain-intake pipeline. It must not bypass the intake contract.

## Required Reading

Before acting, read:

1. `docs/brain-intake-workflow.zh-CN.md`
2. `docs/tasks/pipeline/RUN.md`
3. The relevant `docs/tasks/pipeline/M*.md` module for the step you will run.

If the file is a PDF that may become a Zotero item, also read
`docs/brain-zotero-pdf-workflow.zh-CN.md`.

规范真相源在 `brain/_meta`：动手前先读相关规则（本机 `brain-library meta --name organize-rules`，
经网关 `lib.meta`）。写入范式（操作走网关闸、规范留 `_meta`）见
`docs/brain-write-gateway-paradigm.md`——agent 不直接 finalize 进 `knowledge/`，由 owner 批准。

## Entry Rule

All durable intake starts at `brain/_inbox/`.

Obsidian, Web/WebDAV, Feishu, and CLI are entry adapters only. They may create
an intake ticket and copy source files into `_inbox/<source>/<date>/`; they
must not write final `knowledge/`, Zotero, memory, or `brain-notes` content.

Use:

```bash
python scripts/brain-intake/intake_ticket.py plan \
  --source obsidian \
  --file /path/to/file.pdf \
  --run-dir work/pipeline/run-12

python scripts/brain-intake/intake_ticket.py run \
  --plan work/pipeline/run-12/intake-plan.json \
  --approved-plan \
  --report work/pipeline/run-12/总报告.md
```

Only pass `--approved-plan` when the user's instruction or the EXECUTE run
preauthorization covers the action.

## Confirm And File Loop

Sensitive (`privacy_hint=personal`) or `hold-*` tickets MUST go through user
confirmation before filing. Full lifecycle (see workflow doc §12):

```bash
# 1. after plan: push the confirm message to the user's Feishu
python scripts/brain-intake/intake_ticket.py notify --plan <plan.json> --kind confirm

# 2. user confirms (Feishu reply or direct instruction) → copy into _inbox
python scripts/brain-intake/intake_ticket.py run --plan <plan.json> --approved-plan

# 3. move to the confirmed final directory (must stay inside brain, never _inbox)
python scripts/brain-intake/intake_ticket.py finalize \
  --ticket <file>.intake.json --dest-dir <brain>/personal-data/... --approved

# 4. completion message with final paths
python scripts/brain-intake/intake_ticket.py notify --plan <plan.json> --kind done
```

`notify` sends via `rtime-reminder-register --mode notify` (next timer tick
delivers); use `--dry-run` to print the message instead. Messages carry file
names, classes, and destinations only — never file bodies.

## Daily Notes

Quick notes ("随记/记一下") append to `brain/notes/daily/YYYY-MM-DD.md`
(orangepi: `<brain-root>/notes/daily/`), section `## 随记`, one
line `- HH:MM 内容（来源：feishu|cli|...）`. Create the file with H1
`# YYYY-MM-DD 周X` plus `## 随记` and `## 待办` sections when missing. Never
invent alternative filenames or write vault paths — Obsidian's "01 每日" is a
symlink to this directory.

## Classification Defaults

- Papers with DOI/arXiv/citation use the Zotero linked-attachment workflow.
  Classify by the research-paper taxonomy `brain/_meta/研究论文分类体系.md`:
  pick `knowledge/research/<area>/<topic>/papers/`, read the PDF to write a
  one-line abstract + 2-4 subject tags + the topic (do not leave a
  `needs_review` stub when the PDF is readable), set `citekey` to the
  Better BibTeX `auth+year` form (the numeric `001…` keys from run-04 are
  legacy debt, not the rule), and surface the note under `21 论文/论文地图.md`.
  If no existing topic fits, propose ONE clear new topic to the user — never
  dump into a catch-all bucket.
- Course slides/PPT/PDF go to a course batch plan first; if course id or term
  is unknown, hold rather than creating a new top-level folder.
- Slides and exported PPT PDFs usually get companion Markdown plus page anchors.
- Long textbook-style PDFs may use MinerU after a sample quality check.
- Images and scans get OCR companion notes with `needs_review` when confidence
  is low.
- Site/domain/compliance documents are operations material. If they contain
  names, identity, account, approval, or filing data, default to
  `privacy_hint=personal` and hold for `personal-data/...` candidate filing.

## Reporting

Every run writes `work/pipeline/<run-id>/总报告.md` with:

- input source and ticket ids;
- file list, sha256, size, MIME, privacy hint, and classification;
- planned or applied inbox path;
- final filing actions if later pipeline modules run;
- holds and reasons;
- validation commands and results;
- rollback notes.

Sensitive reports must not include identity numbers, secrets, approval codes,
chat message bodies, or attachment full text.

## 索引更新（入库归位后）

新文件归位 `brain/` 后要让它能被全文检索到（见 `docs/brain-search-quickstart.md`）：

- **批量入库收尾**：跑一次 `scripts/rebuild-brain-index.sh`，让 BM25 索引及所有消费者（含 MCP `library.index_query` 的对外 agent）都拿到新内容。
- **交互查询**：`scripts/brain-search` 会自动检测索引过期（brain 有更新的 md/txt/bib）并重建，无需手动；故"加新文件后下次查询自动跟上"。
- 索引是派生缓存，在 `~/.local/state/...`（**不入 brain、不入 git、Obsidian 看不到**）；多机各建各的。
- 仅文本(md/txt/bib)入索引；PDF/图片型课件需先做 DocPack/OCR 产出 md 才会被纳入（产出后重建即可）。

## Hard Boundaries

- Never delete or overwrite originals.
- Never write Zotero stored attachments.
- Never auto-write uploaded files or sensitive selections into long-term memory.
- Never copy generated state, model caches, runtime logs, or secrets into this
  repository.
- For destructive moves, produce a plan and wait for approval unless the active
  EXECUTE run explicitly preauthorizes the action.
