# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""brain-visualmd CLI — render / plan / transcribe / merge / validate / scan / doctor / build.

Standalone. Outputs go to a STAGING dir (default ``./visualmd-out/<slug>``), never
into ``brain``. **Idempotent**: re-running skips already-transcribed batches and
already-finished documents (by source sha256), so long background runs on a slow
box (orangepi) and incremental analysis of a growing crawled library are cheap.
Brain publish + index demotion are deliberately deferred (see
``docs/brain-visualmd-module.zh-CN.md`` §9).
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from . import backends, render
from .layout import analyze_docpack, get_detector
from .merge import merge_docpack, pending_batches
from .models import Plan
from .plan import DEFAULT_BATCH_PAGES, make_batches
from .render import IMAGE_SUFFIXES, OFFICE_SUFFIXES, PDF_SUFFIXES, sha256_file
from .validate import is_ok, validate_docpack, write_verify

SUPPORTED_SUFFIXES = PDF_SUFFIXES | OFFICE_SUFFIXES | IMAGE_SUFFIXES


def _slug_for(source: Path, override: str | None) -> str:
    return override or source.stem


def _out_dir(out: str | None, slug: str) -> Path:
    return Path(out).resolve() if out else (Path.cwd() / "visualmd-out" / slug)


def _write_status(docpack_dir: Path, **fields) -> None:
    (docpack_dir / "transcribe.json").write_text(
        json.dumps(fields, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )


def _read_backend_id(docpack_dir: Path) -> str:
    f = docpack_dir / "transcribe.json"
    if f.exists():
        try:
            return json.loads(f.read_text("utf-8")).get("backend_id", "")
        except (ValueError, OSError):
            return ""
    return ""


def _render_and_plan(source: Path, out: Path, slug: str, dpi: int, batch_pages: int):
    out.mkdir(parents=True, exist_ok=True)
    info = render.render_source(source, out, dpi=dpi)
    plan = Plan(
        slug=slug,
        source=str(source),
        source_sha256=info["source_sha256"],
        pages=info["pages"],
        batches=make_batches(info["pages"], batch_pages),
    )
    plan.write(out)
    return info, plan


def _finished_matches(out: Path, source: Path) -> bool:
    """True if ``out`` already holds a finished transcription of this exact source."""
    if not (out / f"{out.name}.md").exists():
        return False
    try:
        plan = Plan.read(out)
    except (OSError, ValueError, KeyError):
        return False
    return bool(plan.source_sha256) and plan.source_sha256 == sha256_file(source)


def _transcribe_batches(out: Path, plan: Plan, backend, *, force: bool):
    """Run the backend over batches, skipping ones whose .md already exists."""
    batches_dir = out / "_batches"
    written = pending = skipped = 0
    for batch in plan.batches:
        if (batches_dir / f"{batch.name}.md").exists() and not force:
            skipped += 1
            continue
        result = backend.process_batch(
            backends.BatchContext(docpack_dir=out, plan=plan, batch=batch)
        )
        if result.status == "written":
            written += 1
        else:
            pending += 1
    return written, pending, skipped


def run_pipeline(
    source: Path,
    out: Path,
    slug: str,
    *,
    backend_name: str,
    batch_pages: int,
    dpi: int,
    status: str,
    title: str | None,
    force: bool,
) -> str:
    """render -> (skip-aware) transcribe -> merge -> validate. Returns a state."""
    _, plan = _render_and_plan(source, out, slug, dpi, batch_pages)
    backend = backends.get_backend(backend_name)
    _transcribe_batches(out, plan, backend, force=force)
    _write_status(out, backend_id=backend.backend_id)
    if pending_batches(out, plan):
        return "pending"
    merge_docpack(out, backend_id=backend.backend_id, status=status, title=title)
    issues = validate_docpack(out)
    write_verify(out, issues, backend_id=backend.backend_id)
    return "done" if is_ok(issues) else "invalid"


def cmd_render(args) -> int:
    source = Path(args.source).resolve()
    slug = _slug_for(source, args.slug)
    out = _out_dir(args.out, slug)
    info, plan = _render_and_plan(source, out, slug, args.dpi, args.batch_pages)
    print(f"rendered {info['pages']} page(s) [{info['kind']}] -> {out}")
    print(f"plan.json: {len(plan.batches)} batch(es)")
    return 0


def cmd_plan(args) -> int:
    out = Path(args.dir).resolve()
    plan = Plan.read(out)
    plan.batches = make_batches(plan.pages, args.batch_pages)
    plan.write(out)
    print(f"re-planned {plan.pages} page(s) into {len(plan.batches)} batch(es)")
    return 0


def cmd_analyze(args) -> int:
    out = Path(args.dir).resolve()
    plan = Plan.read(out)
    detector = get_detector(args.detector)
    layout_path = analyze_docpack(out, detector, plan.pages)
    data = json.loads(layout_path.read_text("utf-8"))
    total: dict[str, int] = {}
    for page in data["pages"]:
        for cls, n in page["counts"].items():
            total[cls] = total.get(cls, 0) + n
    summary = total or "(none — install the detector? `pip install paddleocr`)"
    print(f"layout ({detector.name}) -> {layout_path.name}; regions: {summary}")
    return 0


def cmd_enrich(args) -> int:
    from .enrich import enrich_docpack

    summary = enrich_docpack(Path(args.dir).resolve(), recognizer_name=args.recognizer)
    print(
        f"enrich ({summary['recognizer']}) -> {summary['pages_filled']}/{summary['pages']} "
        f"pages filled, {summary['formulas']} formulas; {summary['md']}"
    )
    return 0


def cmd_transcribe(args) -> int:
    out = Path(args.dir).resolve()
    plan = Plan.read(out)
    backend = backends.get_backend(args.backend)
    written, pending, skipped = _transcribe_batches(
        out, plan, backend, force=args.force
    )
    _write_status(
        out,
        backend_id=backend.backend_id,
        written=written,
        pending=pending,
        skipped=skipped,
    )
    print(
        f"backend={backend.backend_id} written={written} pending={pending} skipped={skipped}"
    )
    if pending:
        print(
            f"\n{pending} batch(es) await transcription. Dispatch each "
            f"_batches/*.task.md to an agent that writes the matching .md, then merge."
        )
    return 0


def cmd_merge(args) -> int:
    out = Path(args.dir).resolve()
    plan = Plan.read(out)
    missing = pending_batches(out, plan)
    if missing:
        print(f"cannot merge; pending batches: {', '.join(missing)}")
        return 2
    merged = merge_docpack(
        out, backend_id=_read_backend_id(out), status=args.status, title=args.title
    )
    print(f"merged -> {merged}")
    return 0


def _print_issues(issues) -> None:
    for i in issues:
        loc = f"p{i.page}" if i.page else "-"
        print(f"  [{i.severity}] {loc} {i.code}: {i.message}")


def cmd_validate(args) -> int:
    out = Path(args.dir).resolve()
    issues = validate_docpack(out)
    write_verify(out, issues, backend_id=_read_backend_id(out))
    _print_issues(issues)
    ok = is_ok(issues)
    print(f"validate: {'OK' if ok else 'FAIL'} (verify.json written)")
    return 0 if ok else 1


def cmd_doctor(args) -> int:
    print("toolchain:")
    for tool, ok in render.doctor().items():
        print(f"  {tool}: {'ok' if ok else 'MISSING'}")
    print(f"backends: {', '.join(backends.available())}")
    return 0


def cmd_build(args) -> int:
    source = Path(args.source).resolve()
    slug = _slug_for(source, args.slug)
    out = _out_dir(args.out, slug)
    state = run_pipeline(
        source,
        out,
        slug,
        backend_name=args.backend,
        batch_pages=args.batch_pages,
        dpi=args.dpi,
        status=args.status,
        title=args.title,
        force=args.force,
    )
    print(f"{slug}: {state} -> {out}")
    if state == "pending":
        print(
            "transcription pending (agent backend). Fill _batches/*.task.md, then: merge + validate"
        )
        return 0
    return 0 if state == "done" else 1


def cmd_scan(args) -> int:
    """Incrementally analyze a folder of materials; skip already-finished sources."""
    root = Path(args.dir).resolve()
    out_root = Path(args.out).resolve() if args.out else (Path.cwd() / "visualmd-out")
    sources = sorted(
        p
        for p in root.rglob("*")
        if p.is_file() and p.suffix.lower() in SUPPORTED_SUFFIXES
    )
    counts = {"done": 0, "skipped": 0, "pending": 0, "invalid": 0, "failed": 0}
    for source in sources:
        slug = source.stem
        out = out_root / slug
        if not args.force and _finished_matches(out, source):
            counts["skipped"] += 1
            print(f"  skip (done): {slug}")
            continue
        try:
            state = run_pipeline(
                source,
                out,
                slug,
                backend_name=args.backend,
                batch_pages=args.batch_pages,
                dpi=args.dpi,
                status="draft",
                title=None,
                force=args.force,
            )
        except Exception as exc:  # one bad file must not kill the batch run
            counts["failed"] += 1
            print(f"  FAIL: {slug}: {exc}")
            continue
        counts[state] = counts.get(state, 0) + 1
        print(f"  {state}: {slug}")
    print(
        f"\nscan: {len(sources)} source(s) | "
        + " ".join(f"{k}={v}" for k, v in counts.items())
    )
    return 0 if counts["failed"] == 0 and counts["invalid"] == 0 else 1


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="brain-visualmd", description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_out(sp):
        sp.add_argument(
            "--out", help="staging docpack dir (default ./visualmd-out/<slug>)"
        )
        sp.add_argument("--slug", help="override slug (default = source stem)")
        sp.add_argument("--dpi", type=int, default=150)
        sp.add_argument("--batch-pages", type=int, default=DEFAULT_BATCH_PAGES)

    sp = sub.add_parser("render", help="source -> page PNGs + plan.json")
    sp.add_argument("source")
    add_out(sp)
    sp.set_defaults(func=cmd_render)

    sp = sub.add_parser("plan", help="re-batch an existing docpack dir")
    sp.add_argument("dir")
    sp.add_argument("--batch-pages", type=int, default=DEFAULT_BATCH_PAGES)
    sp.set_defaults(func=cmd_plan)

    sp = sub.add_parser(
        "analyze", help="pre-analysis: detect layout regions -> layout.json"
    )
    sp.add_argument("dir")
    sp.add_argument("--detector", default="paddle", choices=("paddle", "none"))
    sp.set_defaults(func=cmd_analyze)

    sp = sub.add_parser(
        "enrich", help="fill 公式 sections of an existing docpack via a formula recognizer"
    )
    sp.add_argument("dir")
    sp.add_argument(
        "--recognizer",
        default="pix2text",
        choices=("pix2text", "rapid", "none"),
        help="formula recognizer (pix2text: Chinese-aware, self-detecting)",
    )
    sp.set_defaults(func=cmd_enrich)

    sp = sub.add_parser(
        "transcribe", help="run a backend over all batches (skips done batches)"
    )
    sp.add_argument("dir")
    sp.add_argument("--backend", default="agent", choices=backends.available())
    sp.add_argument(
        "--force", action="store_true", help="redo batches even if .md exists"
    )
    sp.set_defaults(func=cmd_transcribe)

    sp = sub.add_parser("merge", help="batches -> single <slug>.md")
    sp.add_argument("dir")
    sp.add_argument("--status", default="draft", choices=("draft", "verified"))
    sp.add_argument("--title", default=None)
    sp.set_defaults(func=cmd_merge)

    sp = sub.add_parser("validate", help="machine acceptance gates")
    sp.add_argument("dir")
    sp.set_defaults(func=cmd_validate)

    sp = sub.add_parser("doctor", help="report toolchain + backends")
    sp.set_defaults(func=cmd_doctor)

    sp = sub.add_parser(
        "build", help="render -> transcribe -> merge -> validate (one source)"
    )
    sp.add_argument("source")
    add_out(sp)
    sp.add_argument("--backend", default="agent", choices=backends.available())
    sp.add_argument("--status", default="draft", choices=("draft", "verified"))
    sp.add_argument("--title", default=None)
    sp.add_argument("--force", action="store_true")
    sp.set_defaults(func=cmd_build)

    sp = sub.add_parser(
        "scan", help="incrementally analyze a folder; skip finished sources"
    )
    sp.add_argument("dir")
    sp.add_argument("--out", help="output root (default ./visualmd-out)")
    sp.add_argument("--backend", default="agent", choices=backends.available())
    sp.add_argument("--dpi", type=int, default=150)
    sp.add_argument("--batch-pages", type=int, default=DEFAULT_BATCH_PAGES)
    sp.add_argument(
        "--force", action="store_true", help="re-analyze even finished sources"
    )
    sp.set_defaults(func=cmd_scan)

    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
