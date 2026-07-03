#!/usr/bin/env bash
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
set -uo pipefail

ROOT="${BRAIN_KNOWLEDGE_ROOT:-}"
DEEP=0

usage() {
  cat <<'EOF'
Usage: scripts/audit-knowledge-materials.sh [knowledge-root] [--deep]

Read-only audit for brain/knowledge learning materials.

Default roots:
  $HOME/OrangePi-Store/sync/brain/knowledge
  /mnt/brain/knowledge

Options:
  --deep   also try LibreOffice Office-to-PDF conversions in a temp directory
EOF
}

find_soffice() {
  if command -v soffice >/dev/null 2>&1; then
    command -v soffice
    return 0
  fi
  if [ -x /Applications/LibreOffice.app/Contents/MacOS/soffice ]; then
    printf '%s\n' /Applications/LibreOffice.app/Contents/MacOS/soffice
    return 0
  fi
  return 1
}

tool_status() {
  local name="$1"
  local path
  if [ "$name" = "soffice" ]; then
    path="$(find_soffice 2>/dev/null || true)"
  else
    path="$(command -v "$name" 2>/dev/null || true)"
  fi
  if [ -n "$path" ]; then
    printf '| `%s` | ok | `%s` |\n' "$name" "$path"
  else
    printf '| `%s` | missing |  |\n' "$name"
  fi
}

count_files() {
  local pattern="$1"
  find "$ROOT" -type f -iname "$pattern" -print 2>/dev/null | wc -l | tr -d ' '
}

while [ "$#" -gt 0 ]; do
  case "$1" in
    --deep)
      DEEP=1
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      ROOT="$1"
      ;;
  esac
  shift
done

if [ -z "$ROOT" ]; then
  if [ -d "$HOME/OrangePi-Store/sync/brain/knowledge" ]; then
    ROOT="$HOME/OrangePi-Store/sync/brain/knowledge"
  elif [ -d /mnt/brain/knowledge ]; then
    ROOT=/mnt/brain/knowledge
  fi
fi

if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
  printf 'Knowledge root not found. Pass a path explicitly.\n' >&2
  exit 2
fi

TMPDIR_AUDIT="$(mktemp -d)"
trap 'rm -rf "$TMPDIR_AUDIT"' EXIT

printf '# Knowledge Materials Audit\n\n'
printf -- '- Timestamp: `%s`\n' "$(date -u '+%Y-%m-%dT%H:%M:%SZ')"
printf -- '- Root: `%s`\n' "$ROOT"
printf -- '- Mode: `%s`\n\n' "$([ "$DEEP" -eq 1 ] && printf deep || printf shallow)"

printf '## File Types\n\n'
find "$ROOT" -type f -print \
  | awk '
      {
        n=split($0, parts, "/");
        name=parts[n];
        if (name !~ /\./) {
          ext="(none)";
        } else {
          sub(/^.*\./, "", name);
          ext=tolower(name);
        }
        counts[ext]++;
      }
      END {
        for (ext in counts) {
          printf "%7d %s\n", counts[ext], ext;
        }
      }' \
  | sort -nr
printf '\n'

printf '## Tool Availability\n\n'
printf '| Tool | Status | Path |\n'
printf '|---|---|---|\n'
for tool in pandoc soffice pdfinfo pdftotext pdftoppm pdfimages qpdf mutool tesseract ocrmypdf img2pdf file jq node npm python3; do
  tool_status "$tool"
done
printf '\n'

printf '## Python Modules\n\n'
if command -v python3 >/dev/null 2>&1; then
  python3 - <<'PY'
mods = [
    "fitz",
    "PIL",
    "pptx",
    "pdfplumber",
    "pandas",
    "openpyxl",
    "pymupdf4llm",
    "docling",
    "markitdown",
    "marker",
    "magic_pdf",
    "pypdf",
]
for mod in mods:
    try:
        __import__(mod)
        print(f"- `{mod}`: ok")
    except Exception:
        print(f"- `{mod}`: missing")
PY
else
  printf 'python3 missing\n'
fi
printf '\n'

printf '## PDF Audit\n\n'
PDF_TOTAL="$(count_files '*.pdf')"
printf -- '- PDF files: `%s`\n' "$PDF_TOTAL"

PDF_OK=0
PDF_FAIL=0
PDF_PAGES=0
ZERO_TEXT=0
PDF_RENDER_FAIL=0
PDF_FAIL_LIST="$TMPDIR_AUDIT/pdf-failures.txt"
ZERO_TEXT_LIST="$TMPDIR_AUDIT/pdf-zero-text.txt"
: > "$PDF_FAIL_LIST"
: > "$ZERO_TEXT_LIST"

if command -v pdfinfo >/dev/null 2>&1; then
  while IFS= read -r -d '' pdf; do
    info="$(pdfinfo "$pdf" 2>&1)"
    status=$?
    if [ "$status" -eq 0 ]; then
      pages="$(printf '%s\n' "$info" | awk '/^Pages:/ {print $2; exit}')"
      pages="${pages:-0}"
      PDF_OK=$((PDF_OK + 1))
      PDF_PAGES=$((PDF_PAGES + pages))
      if command -v pdftotext >/dev/null 2>&1; then
        chars="$(pdftotext -f 1 -l 1 "$pdf" - 2>/dev/null | tr -d '[:space:]' | wc -m | tr -d ' ')"
        if [ "${chars:-0}" -eq 0 ]; then
          ZERO_TEXT=$((ZERO_TEXT + 1))
          printf '%s\n' "$pdf" >> "$ZERO_TEXT_LIST"
        fi
      fi
    else
      PDF_FAIL=$((PDF_FAIL + 1))
      printf '%s\n' "$pdf" >> "$PDF_FAIL_LIST"
      if command -v pdftoppm >/dev/null 2>&1; then
        if ! pdftoppm -f 1 -singlefile -png "$pdf" "$TMPDIR_AUDIT/render-test" >/dev/null 2>&1; then
          PDF_RENDER_FAIL=$((PDF_RENDER_FAIL + 1))
        fi
      fi
    fi
  done < <(find "$ROOT" -type f -iname '*.pdf' -print0)
  printf -- '- `pdfinfo` ok: `%s`\n' "$PDF_OK"
  printf -- '- `pdfinfo` failed: `%s`\n' "$PDF_FAIL"
  printf -- '- Total readable pages: `%s`\n' "$PDF_PAGES"
  printf -- '- First-page zero-text PDFs: `%s`\n' "$ZERO_TEXT"
  printf -- '- Render failures among failed PDFs: `%s`\n' "$PDF_RENDER_FAIL"
  if [ -s "$PDF_FAIL_LIST" ]; then
    printf '\nFailed PDFs:\n\n'
    sed 's/^/- `/' "$PDF_FAIL_LIST" | sed 's/$/`/'
  fi
  if [ -s "$ZERO_TEXT_LIST" ]; then
    printf '\nFirst-page zero-text PDFs:\n\n'
    sed 's/^/- `/' "$ZERO_TEXT_LIST" | sed 's/$/`/'
  fi
else
  printf -- '- Skipped: `pdfinfo` missing\n'
fi
printf '\n'

printf '## Office Audit\n\n'
DOC_COUNT="$(count_files '*.doc')"
DOCX_COUNT="$(count_files '*.docx')"
PPT_COUNT="$(count_files '*.ppt')"
PPTX_COUNT="$(count_files '*.pptx')"
XLSX_COUNT="$(count_files '*.xlsx')"
printf -- '- doc: `%s`\n' "$DOC_COUNT"
printf -- '- docx: `%s`\n' "$DOCX_COUNT"
printf -- '- ppt: `%s`\n' "$PPT_COUNT"
printf -- '- pptx: `%s`\n' "$PPTX_COUNT"
printf -- '- xlsx: `%s`\n' "$XLSX_COUNT"

SOFFICE_BIN="$(find_soffice 2>/dev/null || true)"
if [ "$DEEP" -eq 1 ]; then
  if [ -n "$SOFFICE_BIN" ]; then
    OFFICE_OK=0
    OFFICE_FAIL=0
    OFFICE_FAIL_LIST="$TMPDIR_AUDIT/office-failures.txt"
    : > "$OFFICE_FAIL_LIST"
    OFFICE_OUT="$TMPDIR_AUDIT/office-pdf"
    mkdir -p "$OFFICE_OUT"
    while IFS= read -r -d '' office; do
      rm -f "$OFFICE_OUT"/*.pdf
      if "$SOFFICE_BIN" --headless --convert-to pdf --outdir "$OFFICE_OUT" "$office" >/dev/null 2>&1; then
        if find "$OFFICE_OUT" -maxdepth 1 -type f -name '*.pdf' | grep -q .; then
          OFFICE_OK=$((OFFICE_OK + 1))
        else
          OFFICE_FAIL=$((OFFICE_FAIL + 1))
          printf '%s\n' "$office" >> "$OFFICE_FAIL_LIST"
        fi
      else
        OFFICE_FAIL=$((OFFICE_FAIL + 1))
        printf '%s\n' "$office" >> "$OFFICE_FAIL_LIST"
      fi
    done < <(find "$ROOT" -type f \( -iname '*.doc' -o -iname '*.docx' -o -iname '*.ppt' -o -iname '*.pptx' \) -print0)
    printf -- '- LibreOffice conversion ok: `%s`\n' "$OFFICE_OK"
    printf -- '- LibreOffice conversion failed: `%s`\n' "$OFFICE_FAIL"
    if [ -s "$OFFICE_FAIL_LIST" ]; then
      printf '\nFailed Office files:\n\n'
      sed 's/^/- `/' "$OFFICE_FAIL_LIST" | sed 's/$/`/'
    fi
  else
    printf -- '- Deep conversion skipped: `soffice` missing\n'
  fi
else
  printf -- '- LibreOffice conversion skipped. Re-run with `--deep` for conversion tests.\n'
fi

if command -v python3 >/dev/null 2>&1; then
  printf '\nXLSX read test:\n\n'
  python3 - "$ROOT" <<'PY'
import sys
from pathlib import Path

root = Path(sys.argv[1])
paths = sorted(root.rglob("*.xlsx"))
try:
    import openpyxl
except Exception:
    print("- skipped: `openpyxl` missing")
    raise SystemExit(0)

ok = 0
failed = []
for path in paths:
    try:
        wb = openpyxl.load_workbook(path, read_only=True, data_only=True)
        sheet_count = len(wb.sheetnames)
        wb.close()
        ok += 1
        print(f"- ok `{path}` ({sheet_count} sheets)")
    except Exception as exc:
        failed.append((path, exc))
print(f"- summary: {ok} ok, {len(failed)} failed")
for path, exc in failed:
    print(f"- failed `{path}`: {exc}")
PY
fi
printf '\n'

printf '## Image Audit\n\n'
IMG_TOTAL=$(( $(count_files '*.png') + $(count_files '*.jpg') + $(count_files '*.jpeg') + $(count_files '*.gif') ))
printf -- '- Image/GIF files: `%s`\n' "$IMG_TOTAL"
if command -v sips >/dev/null 2>&1; then
  IMG_OK=0
  IMG_FAIL=0
  while IFS= read -r -d '' image; do
    if sips -g pixelWidth -g pixelHeight "$image" >/dev/null 2>&1; then
      IMG_OK=$((IMG_OK + 1))
    else
      IMG_FAIL=$((IMG_FAIL + 1))
      printf -- '- dimension read failed: `%s`\n' "$image"
    fi
  done < <(find "$ROOT" -type f \( -iname '*.png' -o -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.gif' \) -print0)
  printf -- '- Dimension read ok: `%s`\n' "$IMG_OK"
  printf -- '- Dimension read failed: `%s`\n' "$IMG_FAIL"
else
  printf -- '- Dimension read skipped: `sips` missing\n'
fi

printf '\n## Interpretation Notes\n\n'
printf -- '- `md`, `json`, `csv`, and `bib` are structurally readable.\n'
printf -- '- `pdf` is safe only after page-count and render checks pass.\n'
printf -- '- `ppt`, `pptx`, `doc`, and `docx` need a canonical renderer and visual QA.\n'
printf -- '- `png`, `jpg`, and `gif` are displayable, but semantic extraction needs OCR/caption review.\n'
printf -- '- A successful Markdown conversion is not a display guarantee; keep source, rendered pages, and provenance.\n'
