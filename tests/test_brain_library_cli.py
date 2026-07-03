# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "packages" / "brain-library" / "src" / "brain_library" / "cli.py"
SRC = ROOT / "packages" / "brain-library" / "src"


def _load_cli():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("brain_library.cli")


def _make_brain_fixture(root: Path) -> Path:
    brain = root / "brain"
    (brain / ".obsidian").mkdir(parents=True)
    (brain / "_indexes").mkdir(parents=True)
    (brain / "knowledge" / "papers").mkdir(parents=True)
    (brain / "knowledge" / "courses" / "solid-state-physics" / "slides").mkdir(parents=True)
    (brain / "indexes").mkdir(parents=True)
    (brain / "CLAUDE.md").write_text("brain guidance\n", encoding="utf-8")
    (brain / "index.md").write_text("library index\n", encoding="utf-8")
    (brain / "knowledge" / "note.md").write_text(
        "# Fusion Note\n[[Concept]] ![[figure.png]] #lit @smith2024 "
        "zotero://select/items/ABC stellarator coil plasma",
        encoding="utf-8",
    )
    (brain / "knowledge" / "papers" / "coil.md").write_text(
        "# Coil Paper\nstellarator winding pack and coil optimization",
        encoding="utf-8",
    )
    (brain / "knowledge" / "papers" / "refs.bib").write_text(
        "@article{smith2024,title={Example}}\n",
        encoding="utf-8",
    )
    (brain / "knowledge" / "courses" / "solid-state-physics" / "slides" / "15自由电子论.md").write_text(
        "# 自由电子论\n自由电子论把金属中的价电子近似看作自由电子气，用于解释电导和热导。",
        encoding="utf-8",
    )
    (brain / "knowledge" / "courses" / "solid-state-physics" / "slides" / "16声子热容.md").write_text(
        "# 声子热容\n声子热容来自晶格振动的能量统计，是低温固体热容的重要来源。",
        encoding="utf-8",
    )
    (brain / "knowledge" / "courses" / "solid-state-physics" / "slides" / "17布里渊区.md").write_text(
        "# 布里渊区\n布里渊区是倒易空间中的基本区域，用于描述能带和晶格周期性。",
        encoding="utf-8",
    )
    (brain / "knowledge" / "papers" / "paper.pdf").write_bytes(b"%PDF-1.4\n")
    (brain / "_indexes" / "pdf-manifest.jsonl").write_text(
        json.dumps(
            {
                "schema_version": "pdf-manifest-v1",
                "sha256": "abc123",
                "canonical": True,
                "brain_path": "knowledge/papers/paper.pdf",
                "attachment_mode": "canonical-linked",
                "mobile_cache": False,
                "zotero_item_key": "ITEM123",
                "zotero_linked_attachment_key": "ATTACH123",
                "citekey": "smith2024",
                "obsidian_note": "论文/smith2024.md",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (brain / "indexes" / "library.sqlite").write_bytes(b"sqlite")
    (brain / "indexes" / "bm25-index.json").write_text("{}", encoding="utf-8")

    docpack = brain / "knowledge" / "lesson.docpack"
    docpack.mkdir()
    (docpack / "manifest.json").write_text(
        json.dumps(
            {
                "docpack_id": "lesson",
                "display": {"page_count": 2},
                "risks": [],
            }
        ),
        encoding="utf-8",
    )
    (docpack / "verify.json").write_text(
        json.dumps({"status": "ok", "pages": [{"page": 1}, {"page": 2}], "risks": []}),
        encoding="utf-8",
    )
    (docpack / "citations.json").write_text(
        json.dumps({"anchors": [{"anchor_id": "a1"}, {"anchor_id": "a2"}]}),
        encoding="utf-8",
    )
    broken = brain / "knowledge" / "broken.docpack"
    broken.mkdir()
    (broken / "verify.json").write_text(json.dumps({"status": "needs_review"}), encoding="utf-8")
    return brain


def test_doctor_reports_repo_and_brain_root(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["--repo-root", str(ROOT), "doctor", str(brain)]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["root"] == str(brain)
    assert data["checks"]["repo_package"] == "ok"
    assert data["checks"]["repo_skill"] == "ok"
    assert data["checks"]["obsidian_config"] == "ok"


def test_scan_reports_obsidian_zotero_docpack_and_index_signals(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["scan", str(brain), "--sample-limit", "5"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["guidance"]["claude_md"] is True
    assert data["obsidian"]["vault_config_exists"] is True
    assert data["obsidian"]["wikilinks"] == 1
    assert data["obsidian"]["embeds"] == 1
    assert data["obsidian"]["tags"] == 1
    assert data["zotero"]["bib_files"] == 1
    assert data["zotero"]["citekey_occurrences"] == 1
    assert data["zotero"]["zotero_links"] == 1
    assert data["docpacks"]["count"] == 2
    assert data["docpacks"]["status_counts"]["ok"] == 1
    assert data["docpacks"]["missing_manifest"] == 1
    assert data["docpacks"]["citation_anchor_count"] == 2
    assert data["pdf_manifest"]["exists"] is True
    assert data["pdf_manifest"]["valid_entries"] == 1
    assert data["pdf_manifest"]["canonical_count"] == 1
    assert data["pdf_manifest"]["by_attachment_mode"] == {"canonical-linked": 1}
    assert data["pdf_manifest"]["samples"][0]["zotero_item_key"] == "ITEM123"
    assert data["files"]["sqlite_files"] == ["indexes/library.sqlite"]
    assert data["files"]["bm25_candidates"] == ["indexes/bm25-index.json"]


def test_docpacks_command_outputs_docpack_summary(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["docpacks", str(brain), "--sample-limit", "1"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["count"] == 2
    assert len(data["samples"]) == 1


def test_scan_rejects_missing_root(tmp_path, capfd):
    cli = _load_cli()

    assert cli.main(["scan", str(tmp_path / "missing")]) == 1
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is False
    assert data["errors"] == ["root is not a directory"]


def test_index_build_status_and_query(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"

    # --no-embed keeps this a deterministic schema-3 BM25 index regardless of whether
    # an embedding model happens to be installed on the host (the vector layer has its
    # own tests that skip when no model is present).
    assert cli.main(["index", "build", str(brain), "--out", str(out), "--no-embed"]) == 0
    captured = capfd.readouterr()
    build = json.loads(captured.out)

    assert build["ok"] is True
    assert build["schema_version"] == 3
    assert build["tokenizer"] == "jieba.cut_for_search+unicode61"
    assert build["documents_indexed"] >= 2
    assert out.is_file()

    assert cli.main(["index", "status", str(out)]) == 0
    status = json.loads(capfd.readouterr().out)

    assert status["ok"] is True
    assert status["schema_version"] == 3
    assert status["tokenizer"] == "jieba.cut_for_search+unicode61"
    assert status["document_count"] == build["documents_indexed"]
    assert status["fts_count"] == build["documents_indexed"]

    assert cli.main(["index", "query", str(out), "stellarator coil", "--limit", "3"]) == 0
    query = json.loads(capfd.readouterr().out)

    assert query["ok"] is True
    assert query["result_count"] >= 1
    assert query["results"][0]["path"].endswith(".md")
    assert "stellarator" in query["results"][0]["snippet"].lower()


def _load_indexer():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("brain_library.indexer")


def test_index_metadata_columns_and_courses(tmp_path):
    indexer = _load_indexer()
    brain = tmp_path / "brain"
    (brain / "academics").mkdir(parents=True)
    (brain / "academics" / "prog.md").write_text(
        "---\n"
        "type: ustc-program\n"
        "dept: 203物理学院\n"
        "grade: 2023\n"
        "publish_date: 2026-06-20\n"
        "source: https://jw.ustc.edu.cn/for-std/program-search/info/2609\n"
        "---\n\n"
        "# 应用物理学（2023级 · 203物理学院）\n\n"
        "| 模块 | 编号 | 课程 | 学分 | 学时 | 必修 | 建议学期 | 开课院系 |\n"
        "|---|---|---|---|---|---|---|---|\n"
        "| 数学通修 | MATH1009 | 线性代数B1 | 4.0 | 80 | 必 | 1春 | 数学科学学院 |\n"
        "| 专业核心 | PHYS2001 | 量子力学 | 4.0 | 80 | 必 | 3秋 | 物理学院 |\n",
        encoding="utf-8",
    )
    (brain / "academics" / "notice.md").write_text(
        "---\n"
        "type: ustc-notice\n"
        "dept: 教务处\n"
        "category: 规章制度\n"
        "publish_date: 2021-05-07\n"
        "source: https://www.teach.ustc.edu.cn/notice/notice-teaching/13139.html\n"
        "---\n\n"
        "# 大创经费管理办法\n每个项目1-2万元。\n",
        encoding="utf-8",
    )
    out = tmp_path / "idx.sqlite"
    # embed=False: this test targets the schema-3 BM25 metadata/courses layer and must
    # behave identically whether or not an embedding model is installed on the host.
    build = indexer.build_index(brain, out, force=True, embed=False)
    assert build["ok"] and build["schema_version"] == 3

    # metadata filter: doc_type narrows to the program note, carrying source_url/dept
    progs = indexer.query_index(out, "应用物理", doc_type="ustc-program")
    assert progs["ok"] and progs["filters"]["metadata_available"] is True
    assert progs["result_count"] == 1
    assert progs["results"][0]["dept"] == "203物理学院"
    assert progs["results"][0]["source_url"].endswith("info/2609")

    # date-range filter selects the dated notice
    notice = indexer.query_index(out, "大创", date_from="2020-01-01", date_to="2022-01-01")
    assert notice["result_count"] == 1
    assert notice["results"][0]["publish_date"] == "2021-05-07"

    # structured course queries over the parsed 培养方案 table
    by_code = indexer.query_courses(out, code="MATH1009")
    assert by_code["ok"] and by_code["count"] == 1
    assert by_code["courses"][0]["program_name"].startswith("应用物理学")
    assert by_code["courses"][0]["required"] is True
    heavy = indexer.query_courses(out, dept="203物理学院", min_credits=4.0)
    assert heavy["count"] == 2


def test_strip_frontmatter_unit():
    """剥离函数的边界：剥首块、容忍 BOM、无 frontmatter 不动、正文中的 --- 分隔不误伤、
    纯 frontmatter → 空正文，且剥掉的区间与 _parse_frontmatter 解析的恰好一致(锁死)。"""
    indexer = _load_indexer()
    s = indexer._strip_frontmatter
    # 首块(连同其后空行)被剥，正文保留
    assert s('---\ntype: x\ntitle: "T"\n---\n\n# 标题\n正文。') == "# 标题\n正文。"
    # 文件首的 BOM 被容忍
    assert s("﻿---\ntype: x\n---\n正文") == "正文"
    # 无 frontmatter → 原样返回
    assert s("# 标题\n正文。") == "# 标题\n正文。"
    # 正文中部的 --- 分隔线(非首块)不被剥
    body = "# 标题\n第一段\n\n---\n\n第二段"
    assert s(body) == body
    # 纯 frontmatter 文件 → 空正文
    assert s("---\ntype: x\n---\n") == ""
    # 锁死：剥掉的恰是被解析进列的那块(键/值都不再留在正文里)
    doc = "---\ntype: ustc-notice\ndept: 教务处\n---\n# 大创\n经费说明。"
    assert "ustc-notice" not in s(doc) and "教务处" not in s(doc)
    assert indexer._parse_frontmatter(doc)["type"] == "ustc-notice"
    # 嵌入输入现在以正文起头，而非千篇一律的档案头
    rec = s('---\ntype: course-pdf-raw-text\ntitle: "p18"\nsource: "a.pdf"\npage: 18\n---\n金属中的自由电子参与导电。')
    et = indexer._embed_text("p18", rec)
    assert et.startswith("p18 金属")
    assert "course-pdf-raw-text" not in et and "source" not in et


def test_index_strips_frontmatter_from_body_keeps_metadata(tmp_path):
    """建库级回归：frontmatter 不再进 body/FTS/snippet，但元数据列/课程表/标题照常，
    正文仍可检索。"""
    indexer = _load_indexer()
    brain = tmp_path / "brain"
    (brain / "k").mkdir(parents=True)
    (brain / "k" / "page.md").write_text(
        "---\n"
        "type: course-pdf-raw-text\n"
        'title: "15自由电子论 第18页 原始文本层"\n'
        "source: knowledge/courses/solid-state-physics/slides/15自由电子论.pdf\n"
        "page: 18\n"
        "---\n\n"
        "# 15自由电子论 第18页\n金属中的所有自由电子都参与了导电过程。\n",
        encoding="utf-8",
    )
    out = tmp_path / "idx.sqlite"
    assert indexer.build_index(brain, out, force=True, embed=False)["ok"]

    import sqlite3
    con = sqlite3.connect(out)
    body, doc_type, src = con.execute(
        "SELECT body, doc_type, source_url FROM documents WHERE path=?", ("k/page.md",)
    ).fetchone()
    fts_body = con.execute(
        "SELECT f.body_index FROM documents_fts f JOIN documents d ON d.id=f.rowid WHERE d.path=?",
        ("k/page.md",),
    ).fetchone()[0]
    con.close()
    # body 不再带 frontmatter 块，但正文完好
    assert not body.startswith("---")
    assert "type: course-pdf-raw-text" not in body and "page: 18" not in body
    assert "金属中的所有自由电子" in body
    # frontmatter 仍被提进可查询的列
    assert doc_type == "course-pdf-raw-text"
    assert src.endswith("15自由电子论.pdf")
    # FTS 正文索引不再含 frontmatter 键
    assert "course-pdf-raw-text" not in fts_body
    # 正文仍可检索，且 snippet 以内容起头、不以 frontmatter 起头
    q = indexer.query_index(out, "自由电子 导电")
    assert q["result_count"] >= 1 and q["results"][0]["path"] == "k/page.md"
    assert not q["results"][0]["snippet"].lstrip().startswith("---")


def test_index_query_falls_back_to_or_for_nl_queries(tmp_path):
    indexer = _load_indexer()
    brain = tmp_path / "brain"
    (brain / "k").mkdir(parents=True)
    (brain / "k" / "n.md").write_text("# 选课通知\n本科生选课时间安排说明。\n", encoding="utf-8")
    out = tmp_path / "i.sqlite"
    assert indexer.build_index(brain, out, force=True)["ok"]

    # "什么时候选课" tokenizes to 什么/时候/选课; implicit-AND requires all three in one
    # doc (none) → the OR fallback recalls the 选课通知 note instead of returning empty.
    nl = indexer.query_index(out, "什么时候选课")
    assert nl["result_count"] >= 1
    assert nl["filters"]["matched_operator"] == "OR"
    # a single-token query keeps the precise implicit-AND path (no needless OR widening)
    precise = indexer.query_index(out, "选课")
    assert precise["result_count"] >= 1
    assert precise["filters"]["matched_operator"] == "AND"


def test_index_query_supports_chinese_course_terms(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"

    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    expected_paths = {
        "自由电子论": "knowledge/courses/solid-state-physics/slides/15自由电子论.md",
        "声子热容": "knowledge/courses/solid-state-physics/slides/16声子热容.md",
        "布里渊区": "knowledge/courses/solid-state-physics/slides/17布里渊区.md",
    }
    for term, expected_path in expected_paths.items():
        assert cli.main(["index", "query", str(out), term, "--limit", "5"]) == 0
        query = json.loads(capfd.readouterr().out)

        assert query["ok"] is True
        assert query["result_count"] >= 1
        assert any(result["path"] == expected_path for result in query["results"])


def test_index_build_requires_force_and_rejects_root_output(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "brain-library.sqlite"

    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 1
    exists_error = json.loads(capfd.readouterr().out)
    assert exists_error["errors"] == ["output already exists; pass --force or --incremental"]

    root_out = brain / "indexes" / "derived.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(root_out)]) == 1
    root_error = json.loads(capfd.readouterr().out)
    assert root_error["errors"] == [
        "refusing to write index under brain root without allow_root_output"
    ]


# --- new read-side commands: read / tree / stat / index recent|freshness / meta --query ---


def test_read_command_windows_lines_and_blocks_personal_data(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    (brain / "knowledge" / "lines.md").write_text(
        "\n".join(f"line{i}" for i in range(1, 11)), encoding="utf-8"
    )

    # offset is 1-based: offset=2 starts at the 2nd line
    assert cli.main(["read", str(brain), "--path", "knowledge/lines.md", "--offset", "2", "--limit", "3"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    assert data["total_lines"] == 10
    assert data["text"].splitlines() == ["line2", "line3", "line4"]
    assert data["truncated"] is True

    # offset=1 (and offset=0) both start at the first line
    assert cli.main(["read", str(brain), "--path", "knowledge/lines.md", "--offset", "1", "--limit", "2"]) == 0
    first = json.loads(capfd.readouterr().out)
    assert first["text"].splitlines() == ["line1", "line2"]

    # personal-data is refused defense-in-depth even at the CLI layer
    (brain / "personal-data").mkdir(exist_ok=True)
    (brain / "personal-data" / "secret.md").write_text("secret", encoding="utf-8")
    assert cli.main(["read", str(brain), "--path", "personal-data/secret.md"]) == 1
    blocked = json.loads(capfd.readouterr().out)
    assert blocked["ok"] is False
    assert "personal-data" in blocked["errors"][0]


def test_tree_command_lists_one_level_and_flags_docpacks(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)

    assert cli.main(["tree", str(brain), "--path", "knowledge"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    names = {e["name"] for e in data["entries"]}
    assert {"papers", "note.md"} <= names
    assert any(e["docpack"] for e in data["entries"] if e["name"].endswith(".docpack"))

    (brain / "personal-data").mkdir(exist_ok=True)
    assert cli.main(["tree", str(brain), "--path", "personal-data"]) == 1
    blocked = json.loads(capfd.readouterr().out)
    assert blocked["ok"] is False


def test_stat_command_reports_metadata_and_index_membership(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    assert cli.main(["stat", str(brain), "--path", "knowledge/note.md", "--index", str(out)]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    assert data["kind"] == "file"
    assert data["suffix"] == "md"
    assert data["indexed"] is True

    # a directory is never an indexed document
    assert cli.main(["stat", str(brain), "--path", "knowledge/papers", "--index", str(out)]) == 0
    d = json.loads(capfd.readouterr().out)
    assert d["ok"] is True
    assert d["kind"] == "dir"
    assert d["indexed"] is False


def test_index_recent_orders_by_mtime_and_filters(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    assert cli.main(["index", "recent", str(out), "--limit", "3"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    assert 1 <= len(data["documents"]) <= 3
    mtimes = [doc["mtime"] for doc in data["documents"]]
    assert mtimes == sorted(mtimes, reverse=True)

    assert cli.main(["index", "recent", str(out), "--suffix", "bib"]) == 0
    bib = json.loads(capfd.readouterr().out)
    assert bib["ok"] is True
    assert all(doc["suffix"] == "bib" for doc in bib["documents"])


def test_index_freshness_fresh_then_stale(tmp_path, capfd):
    import os as _os

    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    assert cli.main(["index", "freshness", str(out), "--brain-root", str(brain)]) == 0
    fresh = json.loads(capfd.readouterr().out)
    assert fresh["ok"] is True
    assert fresh["fresh"] is True
    assert fresh["document_count"] >= 2

    # a knowledge file newer than the index makes the verdict stale
    newer = brain / "knowledge" / "newer.md"
    newer.write_text("new content", encoding="utf-8")
    future = out.stat().st_mtime + 1000
    _os.utime(newer, (future, future))
    assert cli.main(["index", "freshness", str(out), "--brain-root", str(brain)]) == 0
    stale = json.loads(capfd.readouterr().out)
    assert stale["fresh"] is False
    assert stale["lag_seconds"] >= 0


def test_index_query_filters_narrow_results(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    assert cli.main(["index", "query", str(out), "coil", "--suffix", "md"]) == 0
    md = json.loads(capfd.readouterr().out)
    assert md["ok"] is True
    assert md["filters"]["suffix"] == "md"
    assert all(r["suffix"] == "md" for r in md["results"])

    assert cli.main(["index", "query", str(out), "coil", "--path-prefix", "knowledge/papers"]) == 0
    pref = json.loads(capfd.readouterr().out)
    assert all(r["path"].startswith("knowledge/papers") for r in pref["results"])

    assert cli.main(["index", "query", str(out), "Coil", "--title-only"]) == 0
    title = json.loads(capfd.readouterr().out)
    assert title["ok"] is True
    assert "title_index" in title["fts_query"]
    assert all("coil" in r["title"].lower() for r in title["results"])


def test_meta_query_searches_rule_bodies(tmp_path, capfd):
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    meta = brain / "_meta"
    meta.mkdir()
    (meta / "organize-rules.md").write_text(
        "# Organize\nput course slides under courses/<id>/slides\n", encoding="utf-8"
    )
    (meta / "naming.md").write_text(
        "# Naming\nuse english-lowercase-hyphen ids\n", encoding="utf-8"
    )

    assert cli.main(["meta", str(brain), "--query", "slides"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    assert data["match_count"] == 1
    assert data["matches"][0]["name"] == "organize-rules.md"
    assert any("slides" in line for line in data["matches"][0]["lines"])

    assert cli.main(["meta", str(brain)]) == 0
    listing = json.loads(capfd.readouterr().out)
    assert listing["ok"] is True
    assert listing["count"] == 2


def test_index_prefix_filters_reject_personal_data(tmp_path, capfd):
    # Defense-in-depth: the CLI rejects a path_prefix that could match a
    # personal-data subtree even though the index never contains personal-data.
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    out = tmp_path / "derived" / "brain-library.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    for bad in ("personal-data", "personal", "Personal-Data", "pe"):
        assert cli.main(["index", "recent", str(out), "--path-prefix", bad]) == 1
        d = json.loads(capfd.readouterr().out)
        assert d["ok"] is False
        assert "personal-data" in d["errors"][0]

        assert cli.main(["index", "query", str(out), "x", "--path-prefix", bad]) == 1
        d2 = json.loads(capfd.readouterr().out)
        assert d2["ok"] is False


def test_index_query_path_prefix_treats_like_wildcards_literally(tmp_path, capfd):
    # A path_prefix containing '_' must match it literally, not as the LIKE
    # single-char wildcard (which would also match a sibling like 'axb').
    cli = _load_cli()
    brain = _make_brain_fixture(tmp_path)
    (brain / "knowledge" / "a_b").mkdir()
    (brain / "knowledge" / "a_b" / "x.md").write_text("# x\nstellarator coil here", encoding="utf-8")
    (brain / "knowledge" / "axb").mkdir()
    (brain / "knowledge" / "axb" / "y.md").write_text("# y\nstellarator coil here", encoding="utf-8")
    out = tmp_path / "derived" / "idx.sqlite"
    assert cli.main(["index", "build", str(brain), "--out", str(out)]) == 0
    capfd.readouterr()

    assert cli.main(["index", "query", str(out), "coil", "--path-prefix", "knowledge/a_b"]) == 0
    data = json.loads(capfd.readouterr().out)
    assert data["ok"] is True
    paths = [r["path"] for r in data["results"]]
    assert any(p.startswith("knowledge/a_b/") for p in paths)
    assert all(not p.startswith("knowledge/axb/") for p in paths)


def _load_indexer():
    if str(SRC) not in sys.path:
        sys.path.insert(0, str(SRC))
    import importlib

    return importlib.import_module("brain_library.indexer")


def test_walk_skips_raw_distill_intermediate_dirs(tmp_path):
    """NN_raw* 原始蒸馏中间块目录不进索引(避免 persona.NNN/work.NNN 污染知识检索);
    精炼/分析产物(02_refined/03_analysis)与普通知识照常索引。"""
    indexer = _load_indexer()
    brain = tmp_path / "brain"
    raw = brain / "personal-data/lifelog/chat-logs/exp/distill_final/01_raw_chat_work"
    raw_persona = brain / "personal-data/lifelog/chat-logs/exp/distill_final/00_raw_chat_persona"
    refined = brain / "personal-data/lifelog/chat-logs/exp/distill_final/02_refined_chat"
    know = brain / "knowledge/institutions/ustc/procedures"
    for d in (raw, raw_persona, refined, know):
        d.mkdir(parents=True)
    (raw / "work.001.md").write_text("原始聊天块 电磁流体力学", encoding="utf-8")
    (raw_persona / "persona.003.md").write_text("原始人设块 无犯罪 软著 杂项", encoding="utf-8")
    (refined / "r.md").write_text("精炼后的内容", encoding="utf-8")
    (know / "baowei_无犯罪.md").write_text("# 无犯罪记录证明流程", encoding="utf-8")

    files, _ = indexer._walk_index_files(brain, max_files=100)
    rel = {str(p.relative_to(brain)) for p in files}
    # 原始中间块被跳过
    assert not any("01_raw_chat_work" in r or "00_raw_chat_persona" in r for r in rel)
    # 精炼产物 + 知识 照常索引
    assert any(r.endswith("02_refined_chat/r.md") for r in rel)
    assert any("baowei_无犯罪.md" in r for r in rel)


def test_skip_dir_re_matches_raw_stages():
    indexer = _load_indexer()
    assert indexer.SKIP_DIR_RE.match("00_raw_chat_persona")
    assert indexer.SKIP_DIR_RE.match("01_raw_chat_work")
    assert not indexer.SKIP_DIR_RE.match("02_refined_chat")
    assert not indexer.SKIP_DIR_RE.match("03_analysis")
    assert not indexer.SKIP_DIR_RE.match("knowledge")


def test_walk_skips_meta_dirs_and_guidance_files(tmp_path):
    """配置/暂存/归档目录 + AI 指引文件(AGENTS.md/CLAUDE.md)不进索引;真知识照进。"""
    indexer = _load_indexer()
    brain = tmp_path / "brain"
    for d in ("_inbox", "_archive", "_indexes", ".claude", ".stfolder",
              "knowledge/x"):
        (brain / d).mkdir(parents=True)
    (brain / "_inbox" / "draft.md").write_text("暂存草稿", encoding="utf-8")
    (brain / "_archive" / "old.md").write_text("归档", encoding="utf-8")
    (brain / ".claude" / "c.md").write_text("配置", encoding="utf-8")
    (brain / "AGENTS.md").write_text("AI 助手指引", encoding="utf-8")
    (brain / "CLAUDE.md").write_text("brain guidance", encoding="utf-8")
    (brain / "knowledge/x/real.md").write_text("# 真知识", encoding="utf-8")
    (brain / "knowledge/x/README.md").write_text("# 章节索引(保留)", encoding="utf-8")

    files, _ = indexer._walk_index_files(brain, max_files=100)
    rel = {str(p.relative_to(brain)) for p in files}
    assert not any(r.startswith(("_inbox/", "_archive/", "_indexes/", ".claude/")) for r in rel)
    assert "AGENTS.md" not in rel and "CLAUDE.md" not in rel
    # 真知识 + README(导航,保留) 照进
    assert "knowledge/x/real.md" in rel
    assert "knowledge/x/README.md" in rel
