# brain-visualmd

Visual-first **strict transcription** of source materials (PDF / PPT / DOCX /
images) into AI-readable Markdown. Each page is rendered to a full-page PNG, a
pluggable backend transcribes it page-by-page (formulas as block LaTeX, figures
described, unclear items flagged 存疑), and machine gates check the result.

- Quality baseline (what a product must meet): `docs/ai-readable-markdown-standard.zh-CN.md`
- Design / architecture: `docs/brain-visualmd-module.zh-CN.md`

## Status (2026-06-22)

Phase 1 scaffold. **Standalone and deliberately NOT wired into the main intake
pipeline; it does NOT publish into `brain`.** Outputs land in a staging dir
(default `./visualmd-out/<slug>`). Brain publish + index demotion come later,
once the module is proven on real materials.

## Install

Part of the uv workspace (auto-included via `packages/*`):

```bash
uv sync --all-packages
```

Or run from a checkout without installing:

```bash
PYTHONPATH=packages/brain-visualmd/src python -m brain_visualmd doctor
```

## Pipeline

```
source ──render──> images/p-NNN.png + plan.json
       ──analyze─> layout.json   (optional pre-analysis: formula/table/figure regions)
       ──plan────> batch ranges (default 22 pages/batch)
       ──transcribe──> _batches/<range>.md   (backend produces md)
                        or _batches/<range>.task.md (agent backend: a task to fill)
       ──merge───> <slug>.md  (frontmatter + per-page blocks)
       ──validate─> verify.json (machine gates; non-zero exit on errors)
```

`analyze` (optional, needs `pip install paddleocr`) runs PP-DocLayout on each page
→ `layout.json` with `{cls, bbox, score, order}` regions. It makes `escalate`
**layout-aware** (a page with a detected formula/table region goes to the strong
model — a pixel-level signal, not a guess from the output). `--detector none` = skip.

**Per-region formula refine** (optional): the `doc` backend fills the 公式 section
with clean per-formula LaTeX instead of pointing at the inline text. Two recognizers:
- `VISUALMD_FORMULA_RECOGNIZER=pix2text` (`pip install pix2text`) — **Chinese-aware**,
  **self-detects** formulas on the page (its own MFD), so it needs **no `analyze`/
  layout.json**. Validated on real solid-state pages (Madelung etc.); single-symbol
  detections are filtered out. The recommended default for Chinese STEM slides.
- `VISUALMD_FORMULA_RECOGNIZER=rapid` (`pip install rapid_latex_ocr`) — RapidLaTeXOCR
  (CDM ~0.97) but **no Chinese**; crop-only, so it needs `analyze` (layout.json) to
  know where the formulas are.

## Usage

Default backend = `agent` (emits task specs for a Claude/Codex agent or Workflow
to fill — the proven seed approach). Use `stub` for a dry, no-model run.

Agent flow (real quality):

```bash
brain-visualmd render "<source>.pdf" --out ./visualmd-out/mydoc
brain-visualmd transcribe ./visualmd-out/mydoc --backend agent
# dispatch each ./visualmd-out/mydoc/_batches/*.task.md to an agent that reads the
# listed page PNGs and writes the matching _batches/<range>.md per the spec
brain-visualmd merge ./visualmd-out/mydoc
brain-visualmd validate ./visualmd-out/mydoc
```

Dry end-to-end (no model, for testing the harness):

```bash
brain-visualmd build "<source>.pdf" --backend stub --out ./visualmd-out/mydoc
```

`doctor` reports the toolchain (`pdfinfo`/`pdftoppm` required; `soffice` for
Office inputs; `tesseract` for optional draft OCR) and the available backends.

## Backends (pluggable)

A backend turns one batch of page PNGs into per-page Markdown. The contract lives
in `src/brain_visualmd/backends/base.py`:

- **synchronous** (api / local VLM): subclass `SyncPageBackend`, implement
  `transcribe_page(req) -> PageResult`. The base writes `_batches/<range>.md`.
- **task-emitting** (agent / remote / human): subclass `TranscribeBackend`,
  implement `process_batch(ctx)` to write a `_batches/<range>.task.md` spec.

Request/result JSON shapes (language-neutral, for out-of-process backends) are in
`src/brain_visualmd/models.py` and mirror the standard §5.2.

To add a backend: implement it under `backends/<name>/`, register it in
`backends/__init__.py`, satisfy the constraints (no silent formula/number edits;
unclear → 存疑; keep PNG refs; block `$$`), pass `validate`, and document it in
`docs/brain-visualmd-module.zh-CN.md` §5.

Built-in:
- `doc` — **fast dedicated document model** (GLM-OCR 0.9B / PaddleOCR-VL).
  Direct transcription, **no "thinking" → ~10× faster** (~28s/page on M4 vs
  ~4-5 min for a reasoning VLM), strong on Chinese formulas. Transcribes but does
  not judge (no 存疑/figure reasoning). **Recommended default for speed.**
- `escalate` — **fast `doc` base on every page, thinking model only on hard pages**
  (gate failures + formula pages) for judgment. Best for a whole library: speed
  on the bulk, judgment where it matters. Config: `VISUALMD_ESCALATE_BASE_MODEL`
  (GLM-OCR), `VISUALMD_ESCALATE_STRONG_MODEL` (qwen3-vl:8b).
- `vision` — OpenAI-compatible vision client for a reasoning VLM (qwen3-vl) or a
  commercial API. Slow but does figure/anomaly judgment. Env: `VISUALMD_VISION_*`
  (`BASE_URL`, `MODEL`, `API_KEY`, `MAX_TOKENS` 8192, `TIMEOUT` 900s,
  `MAX_IMAGE_PX`).
- `agent` — emits task specs for a Claude/Codex agent / Workflow.
- `stub` — no model, spec-conformant placeholder, for tests/dry runs.

```bash
# Fast path — dedicated doc model (GLM-OCR 0.9B), ~28s/page:
OLLAMA_CONTEXT_LENGTH=16384 ollama serve
ollama pull hf.co/ggml-org/GLM-OCR-GGUF:Q8_0
export VISUALMD_VISION_BASE_URL=http://localhost:11434/v1
export VISUALMD_VISION_MODEL=hf.co/ggml-org/GLM-OCR-GGUF:Q8_0
brain-visualmd build "<source>.pdf" --backend doc --out ./visualmd-out/mydoc

# Speed + judgment — doc base + thinking model on formula pages:
ollama pull qwen3-vl:8b
brain-visualmd build "<source>.pdf" --backend escalate --out ./visualmd-out/mydoc
```

> **Local-model gotchas (Ollama), all handled in the backend defaults:**
> - Context: run Ollama with `OLLAMA_CONTEXT_LENGTH=16384` — the default 4096 is
>   eaten by the page image and truncates output.
> - Thinking models (Qwen3-VL) need `VISUALMD_VISION_MAX_TOKENS ≥ 8192` (default)
>   so reasoning finishes before the answer; with that they describe figures and
>   flag suspicious formulas (matches/beats GPT). Qwen2.5-VL-7B (non-thinking) is
>   faster but transcription-only (figures/存疑 often empty).
> - Speed bottleneck is image encoding (~70s/page for a full slide), not the
>   driver — the GPU is used. Set `VISUALMD_VISION_MAX_IMAGE_PX` (or render at a
>   lower `--dpi`) to cut it.

Deployment decision (orangepi background, Mac-first, modular) and model
candidates are in `docs/brain-visualmd-tools.zh-CN.md` §1.

## Background / incremental runs

Idempotent — safe to re-run; finished work is skipped:

```bash
brain-visualmd scan <materials-dir> --backend vision   # analyze a folder, skip done
brain-visualmd transcribe <docpack>                    # resumes; skips done batches
brain-visualmd build ... --force                       # redo from scratch
```

`scan` walks a directory, processes each supported file, and skips any whose
`source_sha256` is unchanged — built for unattended background analysis of a
growing library (e.g. on orangepi). Per-page retry (`vision`) re-calls the model
when a page fails the machine gate, trading compute for quality.

**One-command deploy on a new device** (installs Ollama, starts a persistent
big-context service, pulls the model, then runs): see `deploy/` —
`bash deploy/bootstrap.sh` then `bash deploy/run-scan.sh <dir>`. Same scripts
work on Mac, Linux, and orangepi (the backend is just a `base_url`).

## Tests

```bash
PYTHONPATH=packages/brain-visualmd/src python -m pytest packages/brain-visualmd/tests -q
```
