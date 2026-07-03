# brain-docpack

Reusable CLI/MCP facade for DocPack tooling in `rtime-assistant`.

## Purpose

`brain-docpack` owns deterministic helpers for learning-material processing:
DocPack validation, sample selection, MCP status tools, and conservative
course-material intake planning. It may inspect or copy approved source
materials when explicitly requested, but `brain` remains the data owner.

## Entry Commands

```bash
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack doctor
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack validate <docpack-dir>
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack select-samples <knowledge-root> --limit-per-category 1
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-intake <downloads-dir> --brain-root <brain-root> --course-id <id> --course-title <title>
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-intake <downloads-dir> --brain-root <brain-root> --course-id <id> --course-title <title> --include-all --apply --approved --write-md --update-pdf-manifest --obsidian-note <vault-relative-note> --obsidian-course-dir <vault-course-dir>
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-index --brain-root <brain-root> --course-id <id> --course-title <title>
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack course-mirror-obsidian --brain-root <brain-root> --course-id <id> --obsidian-course-dir <vault-course-dir>
PYTHONPATH=packages/brain-docpack/src python -m brain_docpack dialogue-audit-template --course-id <id> --course-title <title> --source-root <downloads-dir> --brain-root <brain-root> --executor kimi --out work/codex-reports/<run>.md
printf '{"jsonrpc":"2.0","id":1,"method":"tools/list"}\n' | PYTHONPATH=packages/brain-docpack/src python -m brain_docpack.mcp_server
printf '{"jsonrpc":"2.0","id":1,"method":"tools/call","params":{"name":"docpack.course_intake_plan","arguments":{"source_root":"<downloads-dir>","brain_root":"<brain-root>","course_id":"<id>","course_title":"<title>","include_all":true}}}\n' | PYTHONPATH=packages/brain-docpack/src python -m brain_docpack.mcp_server
```

Use `--include-all` only after the source directory has been scoped to one
course batch. It disables keyword filtering and treats every supported PDF,
Office, and slide file below the directory as an intake candidate.

Applied course batches are expected to produce a normalized course root, not a
raw copy of the download tree. The canonical layout is:

- `slides/`: classroom decks and by-session display PDFs.
- `slides/source/`: PPT/PPTX source files for re-export and audit; these are
  source-only and are not mirrored into the default Obsidian sidebar.
- `lectures/`: continuous text handouts and long-form course notes.
- `exercises/`: exercises, review questions, and Q&A material.
- `exams/`: midterm/final/past exam material.
- `references/`: textbooks, papers, and reference books.

Final filenames should be stable and sortable, for example
`<course-id>_lecture-01_<topic>_<teacher>_<yyyymmdd>.pdf` or
`<course-id>_exercise-01_questions_<teacher>_<yyyymmdd>.pdf`; raw download
names such as `lesson1-main`, `科大讲稿1`, or `exercise(1)` should only remain
inside `_intake/` audit reports. Successful apply runs write `README.md`,
`materials_index.csv`, `materials_index.md`, and `_intake/course-intake-*`.
For an already-filed course, run `course-index` to rebuild only
`materials_index.csv` and `materials_index.md` from the existing course root.

`course-intake` plans now include `confirmation_questions` and
`auto_apply_allowed`. The apply path refuses to write when confirmation
questions exist unless the user has explicitly reviewed the concrete questions
and the command is rerun with `--approved`. Questions cover new course roots,
large `--include-all` batches, `misc` classification, sensitive-looking
filenames, same-target/different-hash conflicts, duplicates, and OCR/Office
conversion risks.

The MCP tool `docpack.course_intake_plan` is read-only and returns the same
planning fields without copying files. Use it before a conversational assistant
asks the user for confirmation.

Use `--obsidian-course-dir` for the vault-visible course folder when the user
expects PDFs to appear in the Obsidian file tree. The command copies view-layer
files into category folders such as `课件/`, `参考资料/`, and generated Markdown
into `文稿/`, while keeping `brain/knowledge/courses/<course-id>/` as the
canonical inventory layer.
`slides/source/`, `_intake/`, DocPack page images, and source-only conversion
folders must stay out of the default visible layer.
For the persistent Obsidian sidebar, keep `brain-notes/80 系统/course-view-manifest.json`
as the source of truth and refresh it with `scripts/brain-intake/m4_link.py`.
Materialized course views sync; only `sync:false` local entries belong in
`.stignore`.
After refreshing a course view, run `scripts/brain-intake/m4_link.py --verify`
against the same manifest. `ok: true` is the handoff gate for "visible in
Obsidian"; a nonzero verify result means the course filing is still incomplete.
If the original download directory has moved or been replaced after an intake,
use `course-mirror-obsidian` to rebuild that same view layer from the existing
canonical course root.

`doctor` reports both repository paths and the Poppler tools used by course
intake and DocPack checks: `pdfinfo`, `pdftotext`, and `pdftoppm`. `pypdf` is
not required for `course-intake`; use Poppler as the production page-count and
text-layer path on Mac and Orange Pi.

The installer writes `~/.local/bin/brain-docpack` and
`~/.local/bin/brain-docpack-mcp` wrappers in addition to attempting pip
installation. The wrappers run the selected repository checkout through
`PYTHONPATH`, so they remain valid on Orange Pi clients whose pip backend does
not generate reliable console entrypoints.

`dialogue-audit-template` writes a reusable Markdown checklist for Obsidian
sidebar rehearsals. It makes the executor explicit; for real material intake,
prefer `executor=kimi` on the user's device or Orange Pi, with Codex limited to
tooling fixes, command generation, audit, and tests unless the user explicitly
asks Codex to execute. Keep filled reports under `work/codex-reports/`; they
are local audit evidence and are not committed.

## Validation

```bash
PYTHONPATH=packages/brain-docpack/src python -m pytest tests/test_brain_docpack_cli.py tests/test_brain_docpack_mcp.py tests/test_brain_docpack_course_intake.py tests/test_docpack_builder.py tests/test_docpack_validator.py tests/test_docpack_samples.py -q
scripts/validate-codex-plugin.py plugins/brain-docpack
scripts/module-submit-check.py --module brain-docpack
```

## Boundaries

- Do not commit generated DocPacks, source PDFs, Office files, indexes, or
  course intake output from `brain`.
- Keep legacy scripts working while implementation gradually moves into this
  package.
- MCP tools stay read-only unless a later permission and rollback design says
  otherwise.
