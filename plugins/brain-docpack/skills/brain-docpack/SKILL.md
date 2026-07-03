---
name: brain-docpack
description: Use when auditing, selecting samples, building, validating, or reviewing DocPack outputs for the rtime `brain` knowledge library, especially learning materials, PDFs, Office files, citation mapping, Obsidian-readable Markdown, or Mac/orangepi DocPack workflows.
---

# Brain DocPack

Use this skill for rtime learning-material processing around `brain/knowledge`.
The stable command surface is the `brain-docpack` CLI package in
`packages/brain-docpack`.

## First Reads

From `<repo-root>` on Mac or
`<repo-root>` on orangepi, read:

1. `AGENTS.md`
2. `docs/knowledge-material-docpack-plan.md`
3. `docs/tooling-packaging.md`
4. `docs/logging-and-audit.md` when a run log or audit archive is involved

For `brain`-side behavior, read `brain/CLAUDE.md` from the mounted library.

## Rules

- Do not write generated DocPacks into this repository.
- Do not modify real `brain` content unless the user explicitly confirms that
  write operation.
- For real course/material intake, prefer Kimi on the user's device or Orange
  Pi as the executor. Codex should repair tools, generate commands, audit
  evidence, and run tests unless the user explicitly asks Codex to execute.
- Course intake must start with a read-only plan or MCP call. Before any
  `--apply`, report `confirmation_questions`, `auto_apply_allowed`, file
  count, proposed course id/title, category counts, target conflicts,
  sensitive-risk filenames, OCR/Office risks, and the Obsidian visible-layer
  target. If `confirmation_questions` is non-empty, ask the user the concrete
  questions and do not apply until the user explicitly approves; then pass
  `--approved`.
- Confirmation questions must be specific and answerable. Include the file(s),
  why the assistant is uncertain, and the default safe action. Do not hide
  uncertainty inside generic "needs review" prose.
- Course filing must normalize names and sort order, not just copy files.
  Classroom decks and by-session display PDFs go under `slides/`; PPT/PPTX
  sources go under `slides/source/` and are hidden from the default Obsidian
  view; continuous text handouts go under `lectures/`; exercises, review
  questions, and Q&A go under `exercises/`; textbooks, papers, and reference
  books go under `references/`. If "lecture notes" and "courseware" are mixed,
  ask a concrete `slides-vs-handouts` question before applying.
- Every applied course batch must leave `README.md`, `materials_index.csv`,
  `materials_index.md`, and an `_intake/` plan/report mapping original names
  to normalized names. PDF batches should update `brain/_indexes/pdf-manifest.jsonl`
  when requested.
- Prefer read-only `audit`, `select-samples`, and `validate` before `build`.
- Keep source files, rendered page images, manifests, citations, chunks, and
  risk flags as evidence; do not claim visual correctness from Markdown alone.
- For PDF page counts and text-layer checks, the supported production tools are
  Poppler `pdfinfo`, `pdftotext`, and `pdftoppm`. `pypdf` is optional and must
  not be required for course-intake verification.
- Office conversion is a review-risk path until visual QA and batch logging are
  stable.
- Secrets, private identity data, runtime logs, and generated state stay out of
  git.

## Commands

Install locally when needed:

```bash
scripts/install-rtime-tooling.sh --tool brain-docpack --profile mac
python -m pip install -e packages/brain-docpack
brain-docpack doctor
```

Use `scripts/install-rtime-tooling.sh --apply` only after reviewing the dry-run
output. On orangepi, run it from `<repo-root>` with
`--profile orangepi`. The legacy `scripts/install-brain-docpack-tooling.sh`
remains available for DocPack-only compatibility. Both installers write
`~/.local/bin/brain-docpack` and `~/.local/bin/brain-docpack-mcp` wrappers that
run against the selected repository checkout through `PYTHONPATH`; treat those
wrappers as the stable user-level entrypoints on Orange Pi when pip editable
entrypoints are unavailable or stale.

Read-only checks:

```bash
brain-docpack doctor
brain-docpack audit <brain-root>/knowledge
brain-docpack select-samples <brain-root>/knowledge
brain-docpack validate <path-to-docpack>
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | brain-docpack-mcp
```

Course intake rehearsal:

```bash
brain-docpack dialogue-audit-template --course-id <course-id> --course-title <title> --source-root <source-dir> --brain-root <brain-root> --executor kimi --out work/codex-reports/<run>.md
brain-docpack course-intake <source-dir> --brain-root <brain-root> --course-id <course-id> --course-title <title> --include-all --out work/codex-reports/<run-dry> --json
brain-docpack course-intake <source-dir> --brain-root <brain-root> --course-id <course-id> --course-title <title> --include-all --apply --approved --write-md --update-pdf-manifest --obsidian-note <vault-relative-note> --obsidian-course-dir <vault-course-dir> --out work/codex-reports/<run-apply>
brain-docpack course-index --brain-root <brain-root> --course-id <course-id> --course-title <title>
brain-docpack course-mirror-obsidian --brain-root <brain-root> --course-id <course-id> --obsidian-course-dir <vault-course-dir>
printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"docpack.course_intake_plan","arguments":{"source_root":"<source-dir>","brain_root":"<brain-root>","course_id":"<course-id>","course_title":"<title>","include_all":true}}}\n' | brain-docpack-mcp
```

Before `--apply`, the executor must report file count, proposed course id,
classification, normalized destination names, scan/OCR risks, manifest target,
index outputs, and confirmation questions.
Do not delete source directories or mutate Zotero during course intake.
When the user expects course PDFs to appear in the Obsidian sidebar, include
`--obsidian-course-dir`; it copies a vault-visible view layer such as `课件/`,
`参考资料/`, and `文稿/` while keeping `brain/knowledge/courses/<course-id>/` as
the canonical inventory.
Do not expose `slides/source/`, `_intake/`, DocPack page images, or other
derived/source-only folders in the default Obsidian course sidebar.
For the persistent Obsidian course sidebar, update
`brain-notes/80 系统/course-view-manifest.json` and run
`scripts/brain-intake/m4_link.py --plan/--apply`; materialized course views are
syncable by default, and only `sync:false` local entries belong in `.stignore`.
After apply, run `scripts/brain-intake/m4_link.py --verify` against the same
manifest. Treat a nonzero verify result as unfinished work: fix the manifest,
`.stignore`, stale files, missing source targets, or exposed derived/source-only
folders, then rerun plan/apply/verify until `ok: true`.
If the download directory has already changed, use `course-mirror-obsidian` to
rebuild that visible layer from the existing canonical course root instead of
rerunning `--include-all` against a mixed inbox.

Candidate build into a temp directory:

```bash
brain-docpack build <source-file> --out /tmp/<slug>.docpack --force
brain-docpack validate /tmp/<slug>.docpack
```

## Validation

For package or DocPack tool changes, run:

```bash
python -m py_compile packages/brain-docpack/src/brain_docpack/*.py scripts/build-docpack.py scripts/validate-docpack.py scripts/select-docpack-samples.py
PYTHONPATH=packages/brain-docpack/src python -m pytest tests/test_brain_docpack_cli.py tests/test_brain_docpack_mcp.py tests/test_brain_docpack_course_intake.py tests/test_docpack_builder.py tests/test_docpack_validator.py tests/test_docpack_samples.py -q
PYTHONPATH=packages/brain-docpack/src python -m pytest tests/test_brain_docpack_install_tooling.py -q
scripts/validate-codex-plugin.py plugins/brain-docpack
git diff --check
```

Run Docker tests when Docker can start fresh containers:

```bash
scripts/docker-dev-check.sh --service docpack-tests
scripts/docker-dev-check.sh --service docpack-office-tests
```
