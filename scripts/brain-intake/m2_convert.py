#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""run-02 conversion executor for advanced photonics materials."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path
from typing import Any

import intake_common as ic


COURSE_REL = Path("knowledge") / "courses" / "advanced-photonics"
MINERU_BIN = Path.home() / ".venvs" / "mineru-task01" / "bin" / "mineru"
SOFFICE = Path("/Applications/LibreOffice.app/Contents/MacOS/soffice")
TIMEOUT_SECONDS = 20 * 60
FULL_MINERU_ALLOWLIST = {"lesson1-main.pdf"}


def _pdf_pages(path: Path) -> int:
    proc = subprocess.run(["pdfinfo", str(path)], check=True, capture_output=True, text=True)
    for line in proc.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":", 1)[1].strip())
    raise RuntimeError(f"pdfinfo did not report pages: {path}")


def _md_link(path: str) -> str:
    if any(ch in path for ch in " ()[]"):
        return f"<{path}>"
    return path


def _frontmatter(data: dict[str, Any]) -> str:
    return ic.format_frontmatter(data)


def _rel_from(path: Path, start: Path) -> str:
    return path.relative_to(start).as_posix()


def _render_pdf_layers(pdf: Path, image_dir: Path, text_dir: Path) -> dict[str, Any]:
    pages = _pdf_pages(pdf)
    image_dir.mkdir(parents=True, exist_ok=True)
    text_dir.mkdir(parents=True, exist_ok=True)
    missing_images = [page for page in range(1, pages + 1) if not (image_dir / f"page-{page:02d}.png").exists()]
    if missing_images:
        bulk_prefix = image_dir / "_bulk_page"
        subprocess.run(
            ["pdftocairo", "-png", "-r", "120", str(pdf), str(bulk_prefix)],
            check=True,
            capture_output=True,
            text=True,
        )
        for generated in sorted(image_dir.glob("_bulk_page-*.png")):
            raw = generated.stem.rsplit("-", 1)[-1]
            try:
                page_no = int(raw)
            except ValueError:
                continue
            target = image_dir / f"page-{page_no:02d}.png"
            if not target.exists():
                generated.rename(target)
            else:
                generated.unlink()
    rendered = 0
    extracted = 0
    for page in range(1, pages + 1):
        image_file = image_dir / f"page-{page:02d}.png"
        rendered += int(image_file.exists())
        text_file = text_dir / f"page-{page:02d}.txt"
        if not text_file.exists():
            subprocess.run(
                ["pdftotext", "-f", str(page), "-l", str(page), "-layout", str(pdf), str(text_file)],
                check=True,
                capture_output=True,
                text=True,
            )
        extracted += int(text_file.exists())
    return {"pages": pages, "rendered_pages": rendered, "text_pages": extracted}


def _page_excerpt(text_file: Path, limit: int = 240) -> str:
    if not text_file.exists():
        return ""
    text = " ".join(line.strip() for line in ic.read_text(text_file).splitlines() if line.strip())
    return text[:limit]


def _write_companion(
    brain_root: Path,
    pdf: Path,
    md: Path,
    image_dir: Path,
    text_dir: Path,
    source_pdf_link: str,
    title: str,
    generated_by: str,
    source_kind: str,
    markitdown_text: str | None = None,
) -> dict[str, Any]:
    pages = _pdf_pages(pdf)
    if md.exists():
        return {"status": "skipped", "reason": "companion exists", "path": ic.rel_to(brain_root, md)}
    sha = ic.sha256_file(pdf)
    parent = md.parent
    fm = {
        "type": "course-pdf",
        "title": title,
        "source": ic.rel_to(brain_root, pdf),
        "sha256": sha,
        "course": "advanced-photonics",
        "term": "2026-spring",
        "status": "carded",
        "created": ic.TODAY,
        "generated_by": generated_by,
        "pdf_file": source_pdf_link,
        "pdf_pages": pages,
        "page_image_dir": _rel_from(image_dir, parent),
        "raw_text_dir": _rel_from(text_dir, parent),
        "assistant_readable": "curated-summary-and-images",
        "visual_layer": "page-png",
        "text_layer_status": "raw-extracted-untrusted",
        "tags": ["course/advanced-photonics", source_kind],
    }
    lines = [_frontmatter(fm), f"# {title}", ""]
    lines.extend(
        [
            "## 阅读入口",
            "",
            f"- PDF原件：[[{source_pdf_link}|{source_pdf_link}]]",
            f"- 页图目录：`{_rel_from(image_dir, parent)}`",
            f"- 诊断文本层：`{_rel_from(text_dir, parent)}`（只用于定位页码，不作为公式/表格真值）",
            "",
            "## 页码导航",
            "",
        ]
    )
    for page in range(1, pages + 1):
        lines.append(f"- [[{source_pdf_link}#page={page}|p.{page}]]")
    lines.extend(["", "## 逐页内容", ""])
    for page in range(1, pages + 1):
        image_rel = _md_link(_rel_from(image_dir / f"page-{page:02d}.png", parent))
        excerpt = _page_excerpt(text_dir / f"page-{page:02d}.txt")
        lines.extend(
            [
                f"### p.{page}",
                "",
                f"[[{source_pdf_link}#page={page}|p.{page}]]",
                "",
                f"![]({image_rel})",
                "",
                f"- 页面文字线索：{excerpt or '（本页文本层为空或需人工查看页图）'}",
                "",
            ]
        )
    if markitdown_text:
        snippet = markitdown_text.strip()[:2000]
        lines.extend(["## Office文本抽取线索", "", snippet, ""])
    lines.extend(
        [
            "## 核心概念与公式",
            "",
            "- needs_review：本文件由页图与原始文本层生成，公式、谱线、实验参数和图表数值需以原件页图为准。",
            "",
            "## needs_review",
            "",
            "- 全文公式、图表数值、谱学术语与课堂口径需要人工复核。",
            "",
            "## 闪卡",
            "",
        ]
    )
    for page in range(1, min(pages, 12) + 1):
        lines.append(f"- p.{page}的核心问题是什么？::查看[[{source_pdf_link}#page={page}|p.{page}]]页图并结合课堂笔记回答。")
    ic.write_text(md, "\n".join(lines).rstrip() + "\n")
    return {"status": "done", "path": ic.rel_to(brain_root, md), "pages": pages}


def _manifest_upsert(brain_root: Path, pdf: Path) -> dict[str, Any]:
    entries, invalid = ic.read_manifest(brain_root)
    if invalid:
        raise RuntimeError("manifest has invalid JSON lines")
    by_path, _by_sha = ic.manifest_maps(entries)
    rel = ic.rel_to(brain_root, pdf)
    entry = ic.manifest_entry(brain_root, pdf, by_path.get(rel))
    entry["generated_by"] = "Codex run-02 office-pdf 2026-06-11"
    if rel in by_path:
        for idx, item in enumerate(entries):
            if item.get("brain_path") == rel:
                entries[idx] = entry
                break
        status = "updated"
    else:
        entries.append(entry)
        status = "added"
    ic.write_manifest(brain_root, entries)
    return {"status": status, "brain_path": rel, "sha256": entry["sha256"]}


def _run_mineru(action: dict[str, Any], brain_root: Path, run_dir: Path) -> dict[str, Any]:
    pdf = brain_root / action["source"]
    out_dir = brain_root / action["output_dir"]
    if (out_dir / "content.md").exists() and (out_dir / "content_list.json").exists():
        return {"action": "mineru_lecture", "status": "skipped", "source": action["source"], "reason": "output exists"}
    staging = run_dir / "mineru-staging" / pdf.stem
    staging_parent = staging.parent
    staging_parent.mkdir(parents=True, exist_ok=True)
    env = os.environ.copy()
    env["MINERU_PROCESSING_WINDOW_SIZE"] = "16"
    proc = subprocess.run(
        [
            str(MINERU_BIN),
            "-p",
            str(pdf),
            "-o",
            str(staging_parent),
            "-b",
            "pipeline",
            "-m",
            "auto",
            "-l",
            "ch",
            "-f",
            "true",
            "-t",
            "true",
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
        env=env,
    )
    log_path = run_dir / "mineru-staging" / f"{pdf.stem}.log"
    ic.write_text(log_path, proc.stdout + "\n--- stderr ---\n" + proc.stderr)
    if proc.returncode != 0:
        return {"action": "mineru_lecture", "status": "held", "source": action["source"], "reason": f"mineru returncode {proc.returncode}", "log": str(log_path)}
    auto_dirs = sorted(staging_parent.glob(f"{pdf.stem}/auto"))
    if not auto_dirs:
        return {"action": "mineru_lecture", "status": "held", "source": action["source"], "reason": "mineru auto dir missing", "log": str(log_path)}
    auto = auto_dirs[0]
    md_src = auto / f"{pdf.stem}.md"
    content_list = auto / f"{pdf.stem}_content_list.json"
    if not md_src.exists() or not content_list.exists():
        return {"action": "mineru_lecture", "status": "held", "source": action["source"], "reason": "mineru required outputs missing", "log": str(log_path)}
    out_dir.mkdir(parents=True, exist_ok=True)
    if (auto / "images").exists():
        if (out_dir / "images").exists():
            shutil.rmtree(out_dir / "images")
        shutil.copytree(auto / "images", out_dir / "images")
    shutil.copy2(content_list, out_dir / "content_list.json")
    fm = {
        "type": "textbook-md",
        "title": pdf.stem,
        "source": action["source"],
        "sha256": ic.sha256_file(pdf),
        "course": "advanced-photonics",
        "term": "2026-spring",
        "status": "converted",
        "created": ic.TODAY,
        "generated_by": ic.generated_by(run_dir, "mineru-conversion"),
        "tags": ["course/advanced-photonics", "lecture-text"],
    }
    body = ic.read_text(md_src)
    ic.write_text(out_dir / "content.md", _frontmatter(fm) + body)
    return {"action": "mineru_lecture", "status": "done", "source": action["source"], "output_dir": action["output_dir"], "log": str(log_path)}


def _convert_office_to_pdf(source: Path, target_pdf: Path, run_dir: Path) -> dict[str, Any]:
    target_pdf.parent.mkdir(parents=True, exist_ok=True)
    if target_pdf.exists():
        return {"status": "skipped", "reason": "pdf exists", "pdf": str(target_pdf)}
    staging = run_dir / "office-pdf"
    staging.mkdir(parents=True, exist_ok=True)
    proc = subprocess.run(
        [str(SOFFICE), "--headless", "--convert-to", "pdf", "--outdir", str(staging), str(source)],
        check=False,
        capture_output=True,
        text=True,
        timeout=TIMEOUT_SECONDS,
    )
    if proc.returncode != 0:
        return {"status": "held", "reason": f"soffice returncode {proc.returncode}", "stderr": proc.stderr[-1000:]}
    produced = staging / (source.stem + ".pdf")
    if not produced.exists():
        matches = sorted(staging.glob("*.pdf"), key=lambda p: p.stat().st_mtime, reverse=True)
        produced = matches[0] if matches else produced
    if not produced.exists():
        return {"status": "held", "reason": "soffice produced no pdf"}
    shutil.copy2(produced, target_pdf)
    return {"status": "done", "pdf": str(target_pdf)}


def _markitdown_text(source: Path, out: Path) -> str:
    out.parent.mkdir(parents=True, exist_ok=True)
    if not out.exists():
        proc = subprocess.run(["python", "-m", "markitdown", str(source), "-o", str(out)], check=False, capture_output=True, text=True, timeout=TIMEOUT_SECONDS)
        if proc.returncode != 0:
            ic.write_text(out.with_suffix(out.suffix + ".error.log"), proc.stdout + proc.stderr)
            return ""
    return ic.read_text(out) if out.exists() else ""


def _run_render_companion(action: dict[str, Any], brain_root: Path, run_dir: Path) -> dict[str, Any]:
    pdf = brain_root / action["pdf"]
    image_dir = brain_root / action["image_dir"]
    text_dir = brain_root / action["text_dir"]
    md = brain_root / action["companion_md"]
    layer = _render_pdf_layers(pdf, image_dir, text_dir)
    companion = _write_companion(
        brain_root,
        pdf,
        md,
        image_dir,
        text_dir,
        action["source_pdf_link"],
        action["title"],
        ic.generated_by(run_dir, "companion-md"),
        action["source_kind"],
        action.get("markitdown_text"),
    )
    return {"action": action["action"], "status": "done", "pdf": action["pdf"], "layer": layer, "companion": companion}


def _run_office(action: dict[str, Any], brain_root: Path, run_dir: Path) -> dict[str, Any]:
    source = brain_root / action["source"]
    pdf = brain_root / action["pdf"]
    conversion = _convert_office_to_pdf(source, pdf, run_dir)
    if conversion["status"] == "held":
        return {"action": "office_to_companion", "status": "held", "source": action["source"], "conversion": conversion}
    manifest = _manifest_upsert(brain_root, pdf)
    mark_text = _markitdown_text(source, run_dir / "markitdown" / f"{source.stem}.md")
    next_action = dict(action)
    next_action["markitdown_text"] = mark_text
    companion = _run_render_companion(next_action, brain_root, run_dir)
    return {"action": "office_to_companion", "status": "done", "source": action["source"], "pdf": action["pdf"], "manifest": manifest, "companion": companion}


def build_plan(brain_root: Path, run_dir: Path) -> dict[str, Any]:
    course = brain_root / COURSE_REL
    actions: list[dict[str, Any]] = []
    for pdf in sorted((course / "lectures").glob("*.pdf")):
        output_dir = pdf.parent / "md" / pdf.stem
        if pdf.name in FULL_MINERU_ALLOWLIST or (output_dir / "content.md").exists():
            actions.append(
                {
                    "action": "mineru_lecture",
                    "source": ic.rel_to(brain_root, pdf),
                    "output_dir": ic.rel_to(brain_root, output_dir),
                    "timeout_seconds": TIMEOUT_SECONDS,
                }
            )
        else:
            actions.append(
                {
                    "action": "lecture_to_companion",
                    "source_kind": "lecture-resource-fallback",
                    "pdf": ic.rel_to(brain_root, pdf),
                    "source_pdf_link": pdf.name,
                    "title": pdf.stem,
                    "image_dir": ic.rel_to(brain_root, pdf.parent / "images" / pdf.stem),
                    "text_dir": ic.rel_to(brain_root, pdf.parent / "text" / pdf.stem),
                    "companion_md": ic.rel_to(brain_root, pdf.with_suffix(".md")),
                    "resource_note": "MinerU full conversion skipped for run-02 resource protection; page images and companion md preserve reading surface.",
                }
            )
    for pdf in sorted((course / "exercises").glob("*.pdf")):
        actions.append(
            {
                "action": "pdf_to_companion",
                "source_kind": "exercise",
                "pdf": ic.rel_to(brain_root, pdf),
                "source_pdf_link": pdf.name,
                "title": pdf.stem,
                "image_dir": ic.rel_to(brain_root, pdf.parent / "images" / pdf.stem),
                "text_dir": ic.rel_to(brain_root, pdf.parent / "text" / pdf.stem),
                "companion_md": ic.rel_to(brain_root, pdf.with_suffix(".md")),
            }
        )
    for src in sorted((course / "slides").glob("*.ppt*")):
        pdf = src.parent / "pdf" / f"{src.stem}.pdf"
        actions.append(
            {
                "action": "office_to_companion",
                "source_kind": "slides",
                "source": ic.rel_to(brain_root, src),
                "pdf": ic.rel_to(brain_root, pdf),
                "source_pdf_link": f"pdf/{pdf.name}",
                "title": src.stem,
                "image_dir": ic.rel_to(brain_root, src.parent / "images" / src.stem),
                "text_dir": ic.rel_to(brain_root, src.parent / "text" / src.stem),
                "companion_md": ic.rel_to(brain_root, src.with_suffix(".md")),
                "timeout_seconds": TIMEOUT_SECONDS,
            }
        )
    return {
        "run_id": ic.run_id_from_dir(run_dir),
        "generated_at": ic.utc_now(),
        "course": "advanced-photonics",
        "actions": actions,
        "summary": {kind: sum(1 for a in actions if a["action"] == kind) for kind in sorted({a["action"] for a in actions})},
    }


def apply_plan(brain_root: Path, run_dir: Path, plan: dict[str, Any]) -> dict[str, Any]:
    results = []
    for action in plan.get("actions", []):
        try:
            if action["action"] == "mineru_lecture":
                results.append(_run_mineru(action, brain_root, run_dir))
            elif action["action"] in {"pdf_to_companion", "lecture_to_companion"}:
                results.append(_run_render_companion(action, brain_root, run_dir))
            elif action["action"] == "office_to_companion":
                results.append(_run_office(action, brain_root, run_dir))
            else:
                results.append({"action": action.get("action"), "status": "held", "reason": "unknown action"})
        except subprocess.TimeoutExpired:
            results.append({"action": action.get("action"), "status": "held", "source": action.get("source") or action.get("pdf"), "reason": "timeout"})
        except Exception as exc:
            results.append({"action": action.get("action"), "status": "held", "source": action.get("source") or action.get("pdf"), "reason": f"{type(exc).__name__}: {exc}"})
    summary = {status: sum(1 for item in results if item.get("status") == status) for status in sorted({item.get("status") for item in results})}
    payload = {"ok": not any(item.get("status") == "failed" for item in results), "run_id": ic.run_id_from_dir(run_dir), "generated_at": ic.utc_now(), "summary": summary, "actions": results}
    ic.write_json(run_dir / "M2-log.json", payload)
    companion_payload = {"ok": True, "run_id": ic.run_id_from_dir(run_dir), "actions": [r for r in results if r.get("action") in {"pdf_to_companion", "lecture_to_companion", "office_to_companion"}]}
    ic.write_json(run_dir / "M3-companion-log.json", companion_payload)
    _write_reports(run_dir, payload)
    return payload


def _write_reports(run_dir: Path, payload: dict[str, Any]) -> None:
    summary = payload["summary"]
    held = [item for item in payload["actions"] if item.get("status") == "held"]
    ic.markdown_report(
        run_dir / "M2-报告.md",
        "M2 转换报告",
        [
            ("做了什么", [f"{k}: {v}" for k, v in summary.items()]),
            ("跳过什么", [json.dumps(item, ensure_ascii=False) for item in payload["actions"] if item.get("status") == "skipped"][:20]),
            ("异常", [json.dumps(item, ensure_ascii=False) for item in held[:20]]),
        ],
    )
    companion_count = sum(1 for item in payload["actions"] if item.get("action") in {"pdf_to_companion", "lecture_to_companion", "office_to_companion"})
    ic.markdown_report(
        run_dir / "M3-报告.md",
        "M3 伴生md报告",
        [
            ("做了什么", [f"companion actions: {companion_count}", "所有伴生md均保留needs_review，不伪造公式、数值或课堂结论。"]),
            ("跳过什么", []),
            ("异常", [json.dumps(item, ensure_ascii=False) for item in held if item.get("action") in {"pdf_to_companion", "office_to_companion"}][:20]),
        ],
    )


def main() -> int:
    p = ic.parser("M2 conversion executor")
    ic.add_plan_apply(p)
    args = p.parse_args()
    brain_root = ic.resolve_path(args.brain_root)
    run_dir = args.run_dir
    ic.ensure_run_dir(run_dir)
    if args.apply:
        plan = ic.read_json(ic.require_approved_plan(args, "convert-plan.json"))
        payload = apply_plan(brain_root, run_dir, plan)
        print(json.dumps(payload["summary"], ensure_ascii=False, sort_keys=True))
        return 0
    plan = build_plan(brain_root, run_dir)
    ic.write_json(run_dir / "convert-plan.json", plan)
    ic.write_json(run_dir / "M2-log.json", {"ok": True, "mode": "plan", "run_id": ic.run_id_from_dir(run_dir), "summary": plan["summary"]})
    ic.markdown_report(
        run_dir / "M2-报告.md",
        "M2 转换计划报告",
        [("做了什么", [f"{k}: {v}" for k, v in plan["summary"].items()]), ("跳过什么", []), ("异常", [])],
    )
    print(json.dumps(plan["summary"], ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
