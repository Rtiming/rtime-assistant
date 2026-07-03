#!/usr/bin/env python3
# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""M10 material relation graph builder.

Builds a derived ``brain/_indexes/relations.jsonl`` edge table and, on apply,
refreshes generated "相关材料" sections in companion Markdown notes. The
script is deterministic and uses only local, non-secret brain content.
"""

from __future__ import annotations

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
import re
import sys
from pathlib import Path
from typing import Any

import intake_common as ic


DEFAULT_RUN_DIR = Path("work/pipeline/run-17")
DEFAULT_STATE_DIR = Path.home() / ".local/state/rtime-assistant/relations"
REL_TYPES = {"wikilink", "manifest-sibling", "same-course", "bm25-topic", "citekey"}
WIKILINK = re.compile(r"\[\[([^\]|#]+)(?:#[^\]|]+)?(?:\|[^\]]+)?\]\]")
CITEKEY = re.compile(r"(?<![\w@])@([A-Za-z0-9_:\-]+)")
RELATED_HEADING = "## 相关材料"


def _rel(brain_root: Path, path: Path) -> str:
    return ic.rel_to(brain_root, path)


def _sha_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _read_md(brain_root: Path) -> dict[str, dict[str, Any]]:
    docs: dict[str, dict[str, Any]] = {}
    for md in ic.iter_files(brain_root / "knowledge", (".md",)):
        text = ic.read_text(md)
        fm, body, _ = ic.parse_frontmatter(text)
        rel = _rel(brain_root, md)
        docs[rel] = {
            "path": md,
            "rel": rel,
            "text": text,
            "body": body,
            "frontmatter": fm,
            "title": fm.get("title") or ic.title_from_markdown(md, body),
        }
    return docs


def _doc_lookup(docs: dict[str, dict[str, Any]]) -> dict[str, list[str]]:
    lookup: dict[str, list[str]] = defaultdict(list)
    for rel in docs:
        path = Path(rel)
        lookup[path.name.casefold()].append(rel)
        lookup[path.stem.casefold()].append(rel)
        lookup[path.with_suffix("").as_posix().casefold()].append(rel)
        lookup[rel.casefold()].append(rel)
    return lookup


def _resolve_wikilink(target: str, src_rel: str, docs: dict[str, dict[str, Any]], lookup: dict[str, list[str]]) -> str | None:
    target = target.strip().strip("/")
    if not target:
        return None
    src_parent = Path(src_rel).parent
    candidates = [target, f"{target}.md"] if not Path(target).suffix else [target]
    for candidate in candidates:
        rel = (src_parent / candidate).as_posix()
        if rel in docs:
            return rel
        direct = lookup.get(candidate.casefold()) or []
        if len(direct) == 1:
            return direct[0]
    stem = Path(target).stem.casefold()
    matches = lookup.get(stem) or []
    return matches[0] if len(matches) == 1 else None


def _add_edge(edges: dict[tuple[str, str, str, str], dict[str, Any]], src: str, dst: str, rel: str, evidence: str, score: float) -> None:
    if not src or not dst or src == dst or rel not in REL_TYPES:
        return
    key = (src, dst, rel, evidence)
    item = {
        "src": src,
        "dst": dst,
        "rel": rel,
        "evidence": evidence[:240],
        "score": round(max(0.0, min(1.0, score)), 3),
    }
    old = edges.get(key)
    if old is None or item["score"] > old["score"]:
        edges[key] = item


def _manifest_edges(brain_root: Path, docs: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    entries, _invalid = ic.read_manifest(brain_root)
    for item in entries:
        raw = item.get("brain_path") or item.get("canonical_brain_path") or item.get("path")
        if not raw:
            continue
        pdf_rel = str(raw)
        pdf_path = brain_root / pdf_rel
        md_rel = item.get("md_path")
        if not md_rel:
            md_path = pdf_path.with_suffix(".md")
            if md_path.exists():
                md_rel = _rel(brain_root, md_path)
        if md_rel and str(md_rel) in docs:
            _add_edge(edges, pdf_rel, str(md_rel), "manifest-sibling", "pdf-manifest companion", 0.98)
            _add_edge(edges, str(md_rel), pdf_rel, "manifest-sibling", "pdf-manifest companion", 0.98)
        citekey = str(item.get("citekey") or "").strip()
        if citekey and md_rel and str(md_rel) in docs:
            docs[str(md_rel)].setdefault("citekeys", set()).add(citekey)


def _wikilink_edges(docs: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    lookup = _doc_lookup(docs)
    for src, doc in docs.items():
        for match in WIKILINK.finditer(doc["text"]):
            target = _resolve_wikilink(match.group(1), src, docs, lookup)
            if target:
                _add_edge(edges, src, target, "wikilink", match.group(0), 0.9)


def _citekey_edges(docs: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    by_key: dict[str, list[str]] = defaultdict(list)
    for rel, doc in docs.items():
        keys = set(doc.get("citekeys") or set())
        fm_key = str(doc["frontmatter"].get("citekey") or "").strip()
        if fm_key:
            keys.add(fm_key)
        keys.update(CITEKEY.findall(doc["text"]))
        doc["citekeys"] = keys
        for key in sorted(keys):
            by_key[key].append(rel)
    for key, rels in by_key.items():
        if len(rels) < 2:
            continue
        for src in sorted(rels):
            for dst in sorted(rels):
                if src != dst:
                    _add_edge(edges, src, dst, "citekey", f"shared citekey @{key}", 0.86)


def _same_course_edges(docs: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    by_parent: dict[str, list[str]] = defaultdict(list)
    for rel in docs:
        path = Path(rel)
        if len(path.parts) >= 4 and path.parts[:2] == ("knowledge", "courses"):
            by_parent[path.parent.as_posix()].append(rel)
    for parent, rels in by_parent.items():
        if len(rels) < 2:
            continue
        for src in sorted(rels):
            siblings = [item for item in sorted(rels) if item != src][:8]
            for dst in siblings:
                _add_edge(edges, src, dst, "same-course", f"same directory {parent}", 0.55)


def _terms(text: str) -> list[str]:
    lowered = text.lower()
    terms = re.findall(r"[a-z0-9_.\-]{3,}", lowered)
    for run in re.findall(r"[\u4e00-\u9fff]+", lowered):
        terms.extend(run[i : i + 2] for i in range(max(0, len(run) - 1)))
    stop = {"用户", "当前", "相关", "材料", "来源", "笔记", "正文"}
    return [term for term in terms if term not in stop]


def _bm25_topic_edges(docs: dict[str, dict[str, Any]], edges: dict[tuple[str, str, str, str], dict[str, Any]]) -> None:
    rels = sorted(docs)
    if len(rels) < 2:
        return
    counters: dict[str, Counter] = {}
    lengths: dict[str, int] = {}
    df: Counter = Counter()
    for rel in rels:
        doc = docs[rel]
        text = f"{doc.get('title', '')}\n{doc['body'][:5000]}"
        counts = Counter(_terms(text))
        counters[rel] = counts
        lengths[rel] = sum(counts.values()) or 1
        df.update(counts.keys())
    avgdl = sum(lengths.values()) / len(lengths)
    total = len(rels)
    for src in rels:
        query = counters[src]
        if not query:
            continue
        scored: list[tuple[float, str]] = []
        for dst in rels:
            if src == dst:
                continue
            score = 0.0
            doc_terms = counters[dst]
            for term, qf in query.items():
                tf = doc_terms.get(term, 0)
                if tf <= 0:
                    continue
                idf = math.log(1 + (total - df[term] + 0.5) / (df[term] + 0.5))
                denom = tf + 1.4 * (1 - 0.75 + 0.75 * lengths[dst] / avgdl)
                score += idf * (tf * 2.4 / denom) * min(qf, 3)
            if score > 0:
                scored.append((score, dst))
        scored.sort(reverse=True)
        for score, dst in scored[:3]:
            normalized = min(0.82, 0.35 + score / 12)
            if normalized >= 0.35:
                _add_edge(edges, src, dst, "bm25-topic", "lightweight local text overlap", normalized)


def build_edges(brain_root: Path) -> list[dict[str, Any]]:
    docs = _read_md(brain_root)
    edges: dict[tuple[str, str, str, str], dict[str, Any]] = {}
    _manifest_edges(brain_root, docs, edges)
    _wikilink_edges(docs, edges)
    _citekey_edges(docs, edges)
    _same_course_edges(docs, edges)
    _bm25_topic_edges(docs, edges)
    return sorted(edges.values(), key=lambda item: (item["src"], item["rel"], -item["score"], item["dst"]))


def top_related(edges: list[dict[str, Any]], src: str, limit: int = 5) -> list[dict[str, Any]]:
    rows = [edge for edge in edges if edge.get("src") == src]
    rows.sort(key=lambda edge: (-float(edge.get("score", 0)), edge.get("rel", ""), edge.get("dst", "")))
    out: list[dict[str, Any]] = []
    seen: set[str] = set()
    for edge in rows:
        dst = str(edge.get("dst") or "")
        if not dst or dst in seen:
            continue
        seen.add(dst)
        out.append(edge)
        if len(out) >= limit:
            break
    return out


def _wikilink_for(dst: str) -> str:
    label = Path(dst).stem or Path(dst).name
    return f"[[{dst}|{label}]]"


def related_section(edges: list[dict[str, Any]]) -> str:
    lines = [
        RELATED_HEADING,
        "",
        "<!-- rtime-relations:start -->",
        "<!-- generated by m10_relations.py; edit source links/metadata instead of this block -->",
    ]
    if not edges:
        lines.append("- 暂无自动关联材料。")
    for edge in edges:
        needs_review = " needs_review" if edge["rel"] in {"same-course", "bm25-topic"} or float(edge["score"]) < 0.75 else ""
        lines.append(
            f"- {_wikilink_for(edge['dst'])} - {edge['rel']} score={edge['score']:.3f}{needs_review}; evidence={edge['evidence']}"
        )
    lines.extend(["<!-- rtime-relations:end -->", ""])
    return "\n".join(lines)


def replace_related_section(text: str, section: str) -> str:
    pattern = re.compile(r"(?ms)(^|\n)## 相关材料\n.*?(?=\n## |\Z)")
    match = pattern.search(text)
    if match:
        prefix = "\n" if match.group(1) else ""
        tail = text[match.end() :]
        replacement = prefix + section.rstrip()
        if not tail and text.endswith("\n"):
            replacement += "\n"
        return text[: match.start()] + replacement + tail
    return text.rstrip() + "\n\n" + section


def build_plan(brain_root: Path, run_dir: Path, state_dir: Path, limit: int = 5) -> dict[str, Any]:
    edges = build_edges(brain_root)
    relations_text = "".join(json.dumps(edge, ensure_ascii=False, sort_keys=True) + "\n" for edge in edges)
    updates = []
    for md in sorted({edge["src"] for edge in edges if str(edge.get("src", "")).endswith(".md")}):
        rows = top_related(edges, md, limit=limit)
        if rows:
            updates.append(
                {
                    "action": "update_related_section",
                    "path": md,
                    "related": rows,
                    "section_sha256": _sha_text(related_section(rows)),
                }
            )
    actions = [
        {
            "action": "write_relations_index",
            "path": "_indexes/relations.jsonl",
            "edge_count": len(edges),
            "sha256": _sha_text(relations_text),
            "edges": edges,
        },
        *updates,
    ]
    summary = {
        "edge_count": len(edges),
        "related_section_updates": len(updates),
        "by_rel": {rel: sum(1 for edge in edges if edge["rel"] == rel) for rel in sorted(REL_TYPES)},
    }
    return {
        "schema": "rtime-relations-plan-v1",
        "run_id": ic.run_id_from_dir(run_dir),
        "generated_at": ic.utc_now(),
        "brain_root": str(brain_root),
        "state_dir": str(state_dir),
        "actions": actions,
        "summary": summary,
    }


def apply_plan(plan: dict[str, Any], approved_plan: Path) -> dict[str, Any]:
    brain_root = ic.resolve_path(Path(plan["brain_root"]))
    state_dir = ic.resolve_path(Path(plan["state_dir"]))
    applied: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    audit_log = state_dir / "relations-audit.jsonl"
    state_dir.mkdir(parents=True, exist_ok=True)
    for action in plan.get("actions", []):
        kind = action.get("action")
        if kind == "write_relations_index":
            dest = brain_root / action["path"]
            ic.ensure_inside(brain_root / "_indexes", dest)
            text = "".join(json.dumps(edge, ensure_ascii=False, sort_keys=True) + "\n" for edge in action.get("edges", []))
            ic.write_text(dest, text)
            applied.append({"action": kind, "path": action["path"], "edge_count": action.get("edge_count", 0)})
        elif kind == "update_related_section":
            md = brain_root / action["path"]
            try:
                ic.ensure_inside(brain_root / "knowledge", md)
            except ValueError:
                skipped.append({"action": kind, "path": action.get("path"), "reason": "outside knowledge"})
                continue
            if not md.is_file():
                skipped.append({"action": kind, "path": action.get("path"), "reason": "missing md"})
                continue
            old = ic.read_text(md)
            new = replace_related_section(old, related_section(action.get("related", [])))
            if new != old:
                backup = Path(approved_plan).parent / "backups" / "relations" / Path(action["path"] + ".bak")
                if not backup.exists():
                    ic.write_text(backup, old)
                ic.write_text(md, new)
                applied.append({"action": kind, "path": action["path"], "related_count": len(action.get("related", []))})
            else:
                skipped.append({"action": kind, "path": action["path"], "reason": "unchanged"})
        else:
            skipped.append({"action": str(kind), "reason": "unknown"})
    with audit_log.open("a", encoding="utf-8") as fh:
        fh.write(
            json.dumps(
                {
                    "ts": ic.utc_now(),
                    "approved_plan": str(approved_plan),
                    "applied": len(applied),
                    "skipped": len(skipped),
                    "summary": plan.get("summary", {}),
                },
                ensure_ascii=False,
                sort_keys=True,
            )
            + "\n"
        )
    return {"ok": True, "applied": applied, "skipped": skipped, "audit_log": str(audit_log)}


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--plan", action="store_true")
    group.add_argument("--apply", action="store_true")
    parser.add_argument("--approved-plan", type=Path)
    parser.add_argument("--brain-root", type=Path, default=ic.DEFAULT_BRAIN_ROOT)
    parser.add_argument("--run-dir", type=Path, default=DEFAULT_RUN_DIR)
    parser.add_argument("--state-dir", type=Path, default=DEFAULT_STATE_DIR)
    parser.add_argument("--limit-related", type=int, default=5)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv or sys.argv[1:])
    brain_root = ic.resolve_path(args.brain_root)
    run_dir = ic.resolve_path(args.run_dir)
    state_dir = ic.resolve_path(args.state_dir)
    ic.ensure_run_dir(run_dir)
    plan_path = run_dir / "m10-relations-plan.json"
    if args.plan:
        plan = build_plan(brain_root, run_dir, state_dir, limit=max(1, args.limit_related))
        ic.write_json(plan_path, plan)
        ic.write_json(run_dir / "M10-relations-log.json", {"ok": True, "mode": "plan", "summary": plan["summary"]})
        ic.markdown_report(
            run_dir / "M10-relations-report.md",
            "M10 relations plan report",
            [
                ("Summary", [f"{key}: {value}" for key, value in plan["summary"].items()]),
                ("Next", [f"Review and apply {plan_path} to write derived relations and related sections."]),
            ],
        )
        print(json.dumps({"ok": True, "plan": str(plan_path), "summary": plan["summary"]}, ensure_ascii=False))
        return 0
    if not args.approved_plan:
        print(json.dumps({"ok": False, "errors": ["--apply requires --approved-plan"]}, ensure_ascii=False))
        return 2
    plan = ic.read_json(args.approved_plan)
    result = apply_plan(plan, args.approved_plan)
    ic.write_json(run_dir / "M10-relations-apply-log.json", result)
    ic.markdown_report(
        run_dir / "M10-relations-report.md",
        "M10 relations apply report",
        [
            ("Applied", [f"{item['action']}: {item.get('path')}" for item in result["applied"]]),
            ("Skipped", [f"{item.get('path', item.get('action'))}: {item.get('reason')}" for item in result["skipped"]]),
        ],
    )
    print(json.dumps({"ok": True, "applied": len(result["applied"]), "skipped": len(result["skipped"])}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
