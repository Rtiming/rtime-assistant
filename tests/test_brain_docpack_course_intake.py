# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import json
from pathlib import Path

from test_docpack_builder import _write_minimal_pdf

from brain_docpack.course_intake import (
    build_obsidian_view_entries,
    build_plan,
    index_existing_course,
    main,
    mirror_existing_course_to_obsidian,
    write_materials_indexes,
)
from brain_docpack.dialogue_audit import render_template


def test_course_intake_plans_course_materials(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _write_minimal_pdf(
        downloads / "第二章-热力学第二定律.pdf",
        "Second law lecture text with enough native PDF text to pass the intake threshold. "
        "This fixture represents a digital lecture handout with searchable text.",
    )
    _write_minimal_pdf(downloads / "2024.pdf", "")
    (downloads / "MidTermExcLec.pptx").write_bytes(b"pptx placeholder")
    (downloads / "2017.docx").write_bytes(b"docx placeholder")

    brain = tmp_path / "brain"
    plan = build_plan(
        downloads,
        brain,
        "thermal-statistical-physics",
        "热力学与统计物理",
        keywords=["热力学", "tsa"],
    )

    assert plan.summary["files"] == 4
    assert plan.summary["confirmation_questions"] >= 1
    assert plan.auto_apply_allowed is False
    assert {question.id for question in plan.confirmation_questions} >= {"new-course-root"}
    by_name = {Path(item.source_path).name: item for item in plan.files}

    assert by_name["第二章-热力学第二定律.pdf"].category == "lectures"
    assert by_name["第二章-热力学第二定律.pdf"].md_strategy.startswith("docpack-content-md")
    assert "ch02_" in Path(by_name["第二章-热力学第二定律.pdf"].destination_path).name

    assert by_name["2024.pdf"].category == "exams"
    assert by_name["2024.pdf"].md_strategy == "pdf-original-plus-page-images-ocr-later"

    assert by_name["MidTermExcLec.pptx"].category == "slides"
    assert Path(by_name["MidTermExcLec.pptx"].destination_path).parent.name == "source"
    assert by_name["MidTermExcLec.pptx"].md_strategy == "docpack-via-libreoffice-needs-review"

    assert by_name["2017.docx"].category == "exams"


def test_course_intake_apply_copies_files_and_writes_reports(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _write_minimal_pdf(downloads / "第一章-引言&热力学第一定律.pdf", "First law lecture text.")
    _write_minimal_pdf(downloads / "25Spring_TSA_mid.pdf", "Midterm exam text.")

    brain = tmp_path / "brain"
    assert (
        main(
            [
                str(downloads),
                "--brain-root",
                str(brain),
                "--course-id",
                "thermal-statistical-physics",
                "--course-title",
                "热力学与统计物理",
                "--apply",
                "--approved",
                "--write-md",
                "--update-pdf-manifest",
                "--json",
            ]
        )
        == 0
    )

    course = brain / "knowledge" / "courses" / "thermal-statistical-physics"
    assert (course / "lectures" / "ch01_引言-热力学第一定律.pdf").exists()
    assert (course / "exams" / "2025-spring_thermal-statistical-physics_midterm.pdf").exists()
    assert (course / "md" / "lectures" / "ch01_引言-热力学第一定律.md").exists()
    assert (course / "materials_index.csv").exists()
    assert (course / "materials_index.md").exists()
    assert (course / "md" / "exams" / "2025-spring_thermal-statistical-physics_midterm.md").exists()
    assert (course / "README.md").exists()
    assert (course / "_intake" / "course-intake-report.md").exists()
    lecture_md = (course / "md" / "lectures" / "ch01_引言-热力学第一定律.md").read_text(
        encoding="utf-8"
    )
    assert "<details>" in lecture_md
    assert "## 助手可读性" in lecture_md

    data = json.loads((course / "_intake" / "course-intake-plan.json").read_text(encoding="utf-8"))
    assert data["course_id"] == "thermal-statistical-physics"
    assert data["summary"]["files"] == 2

    manifest_lines = (brain / "_indexes" / "pdf-manifest.jsonl").read_text(encoding="utf-8").splitlines()
    manifest_entries = [json.loads(line) for line in manifest_lines]
    assert {entry["kind"] for entry in manifest_entries} == {"course-pdf"}
    assert {entry["attachment_mode"] for entry in manifest_entries} == {"brain-only"}
    assert {entry["obsidian_note"] for entry in manifest_entries} == {"课程/热力学与统计物理.md"}


def test_course_intake_plasma_diagnostics_include_all_and_classifies_references(tmp_path):
    downloads = tmp_path / "wechat"
    downloads.mkdir()
    _write_minimal_pdf(downloads / "等离子体 诊断导论1 - 01  概述 2026.pdf", "")
    _write_minimal_pdf(downloads / "等离子体诊断导论2-05，06，07，08 磁探针 2026.pdf", "")
    _write_minimal_pdf(
        downloads / "1832_1_online.pdf",
        "The invalidity of a Mach probe model. Physics of Plasmas commentary with searchable text.",
    )
    _write_minimal_pdf(downloads / "Principles_of_plasma_diagnostics(1).pdf", "")
    _write_minimal_pdf(
        downloads / "高温等离子体诊断技术 上册 (项志遴，俞昌旋) (z-library.sk, 1lib.sk, z-lib.sk).pdf",
        "",
    )

    brain = tmp_path / "brain"
    plan = build_plan(
        downloads,
        brain,
        "plasma-diagnostics-introduction",
        "等离子体诊断导论",
        keywords=[],
        include_all=True,
    )

    assert plan.summary["files"] == 5
    by_name = {Path(item.source_path).name: item for item in plan.files}

    overview = by_name["等离子体 诊断导论1 - 01  概述 2026.pdf"]
    assert overview.category == "slides"
    assert overview.material_type == "lecture-pdf"

    mach = by_name["1832_1_online.pdf"]
    assert mach.category == "references"
    assert mach.material_type == "paper"
    assert Path(mach.destination_path).name == "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf"
    assert mach.md_path is not None

    principles = by_name["Principles_of_plasma_diagnostics(1).pdf"]
    assert principles.category == "references"
    assert principles.material_type == "textbook"
    assert Path(principles.destination_path).name == "reference_principles-of-plasma-diagnostics.pdf"

    high_temp = by_name[
        "高温等离子体诊断技术 上册 (项志遴，俞昌旋) (z-library.sk, 1lib.sk, z-lib.sk).pdf"
    ]
    assert high_temp.category == "references"
    assert Path(high_temp.destination_path).name == "reference_高温等离子体诊断技术-上册-项志遴-俞昌旋.pdf"
    assert plan.summary["category_slides"] == 2
    assert plan.summary["category_references"] == 3


def test_course_intake_include_all_cli_applies_plasma_batch_and_writes_generic_readme(tmp_path):
    downloads = tmp_path / "wechat"
    downloads.mkdir()
    _write_minimal_pdf(downloads / "等离子体诊断导论3-05，06 2026.pdf", "propagation text layer")
    _write_minimal_pdf(downloads / "1832_1_online.pdf", "Mach probe text layer")

    brain = tmp_path / "brain"
    obsidian_course = tmp_path / "brain-notes" / "10 课程" / "2026春" / "等离子体诊断导论"
    assert (
        main(
            [
                str(downloads),
                "--brain-root",
                str(brain),
                "--course-id",
                "plasma-diagnostics-introduction",
                "--course-title",
                "等离子体诊断导论",
                "--include-all",
                "--apply",
                "--approved",
                "--write-md",
                "--update-pdf-manifest",
                "--obsidian-note",
                "10 课程/2026春/等离子体诊断导论/等离子体诊断导论.md",
                "--obsidian-course-dir",
                str(obsidian_course),
                "--json",
            ]
        )
        == 0
    )

    course = brain / "knowledge" / "courses" / "plasma-diagnostics-introduction"
    assert (course / "slides" / "等离子体诊断导论3-05，06-2026.pdf").exists()
    assert (course / "references" / "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf").exists()
    assert (obsidian_course / "课件" / "等离子体诊断导论3-05，06-2026.pdf").exists()
    assert (obsidian_course / "参考资料" / "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf").exists()
    assert (obsidian_course / "文稿" / "课件" / "等离子体诊断导论3-05，06-2026.md").exists()
    assert (
        obsidian_course / "文稿" / "参考资料" / "paper_hutchinson-2002_invalidity-of-mach-probe-model.md"
    ).exists()
    readme = (course / "README.md").read_text(encoding="utf-8")
    assert "等离子体诊断导论" in readme
    assert "热力学与统计物理课程资料正本目录" not in readme

    manifest_entries = [
        json.loads(line)
        for line in (brain / "_indexes" / "pdf-manifest.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert {entry["obsidian_note"] for entry in manifest_entries} == {
        "10 课程/2026春/等离子体诊断导论/等离子体诊断导论.md"
    }
    assert {entry["course"] for entry in manifest_entries} == {"plasma-diagnostics-introduction"}

    data = json.loads((course / "_intake" / "course-intake-plan.json").read_text(encoding="utf-8"))
    assert data["summary"]["obsidian_mirror_files"] == 4
    assert data["summary"]["obsidian_mirror_changed"] == 4
    assert data["summary"]["confirmation_questions"] >= 1
    assert any(question["id"] == "new-course-root" for question in data["confirmation_questions"])


def test_course_intake_apply_requires_approval_when_questions_exist(tmp_path, capsys):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _write_minimal_pdf(downloads / "第一章-课程介绍.pdf", "intro text layer")
    brain = tmp_path / "brain"

    rc = main(
        [
            str(downloads),
            "--brain-root",
            str(brain),
            "--course-id",
            "new-course",
            "--course-title",
            "新课程",
            "--include-all",
            "--apply",
            "--json",
        ]
    )

    captured = capsys.readouterr()
    assert rc == 2
    assert "confirmation_questions" in captured.out
    assert "Refusing --apply" in captured.err
    assert not (brain / "knowledge" / "courses" / "new-course").exists()


def test_course_intake_detects_sensitive_and_target_conflict(tmp_path):
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    _write_minimal_pdf(downloads / "报名表-名单.pdf", "new sensitive course file")
    brain = tmp_path / "brain"
    existing = brain / "knowledge" / "courses" / "demo-course" / "misc" / "报名表-名单.pdf"
    existing.parent.mkdir(parents=True)
    _write_minimal_pdf(existing, "different existing content")

    plan = build_plan(
        downloads,
        brain,
        "demo-course",
        "示例课程",
        keywords=[],
        include_all=True,
    )

    item = plan.files[0]
    assert "possible_personal_or_sensitive_filename" in item.risks
    assert "target_exists_different_content" in item.risks
    ids = {question.id for question in plan.confirmation_questions}
    assert {"possible-sensitive-files", "target-path-conflicts"} <= ids


def test_course_mirror_obsidian_rebuilds_visible_layer_from_existing_course(tmp_path):
    course = tmp_path / "brain" / "knowledge" / "courses" / "plasma-diagnostics-introduction"
    (course / "slides").mkdir(parents=True)
    (course / "references").mkdir(parents=True)
    _write_minimal_pdf(course / "slides" / "等离子体诊断导论3-05，06-2026.pdf", "lecture text")
    _write_minimal_pdf(
        course / "references" / "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf",
        "Mach probe text",
    )
    lecture_md = course / "md" / "slides" / "等离子体诊断导论3-05，06-2026.md"
    lecture_md.parent.mkdir(parents=True, exist_ok=True)
    lecture_md.write_text("# lecture\n", encoding="utf-8")

    obsidian_course = tmp_path / "brain-notes" / "10 课程" / "2026春" / "等离子体诊断导论"
    summary = mirror_existing_course_to_obsidian(course, obsidian_course)

    assert summary == {"obsidian_mirror_files": 3, "obsidian_mirror_changed": 3}
    assert (obsidian_course / "课件" / "等离子体诊断导论3-05，06-2026.pdf").exists()
    assert (obsidian_course / "参考资料" / "paper_hutchinson-2002_invalidity-of-mach-probe-model.pdf").exists()
    assert (obsidian_course / "文稿" / "课件" / "等离子体诊断导论3-05，06-2026.md").exists()


def test_dialogue_audit_template_records_reusable_observation_points(tmp_path):
    rendered = render_template(
        course_id="plasma-diagnostics-introduction",
        course_title="等离子体诊断导论",
        source_root=tmp_path / "source",
        brain_root=tmp_path / "brain",
        entry="obsidian",
        executor="kimi",
        created_at="2026-06-16T05:00:00",
    )

    assert "Obsidian 对话式入库审计" in rendered
    assert "对话轮次" in rendered
    assert "1832_1_online.pdf" in rendered
    assert "executor: `kimi`" in rendered
    assert "Codex 负责工具修复、剧本生成、审计和测试" in rendered
    assert "聊天附件和 `_inbox` 只代表收件" in rendered
    assert "brain_docpack course-intake" in rendered
    assert "--obsidian-course-dir" in rendered
    assert "confirmation_questions" in rendered
    assert "--approved" in rendered
    assert "docpack.course_intake_plan" in rendered
    assert "m4_link.py --plan" in rendered


def _make_course_with_legacy_homework(tmp_path):
    """A course root that has both the new exercises/ and a legacy homework/ dir."""
    course = tmp_path / "brain" / "knowledge" / "courses" / "controlled-fusion-introduction"
    (course / "exercises").mkdir(parents=True)
    (course / "homework").mkdir(parents=True)
    _write_minimal_pdf(course / "exercises" / "磁约束作业（2026-05-12）.pdf", "new exercise text")
    _write_minimal_pdf(course / "homework" / "聚变作业总.pdf", "legacy homework text")
    _write_minimal_pdf(
        course / "homework" / "Intro_CTF_MCF_homework_含答案.pdf", "legacy homework with answers"
    )
    return course


def test_index_existing_course_indexes_legacy_homework_as_exercises(tmp_path):
    course = _make_course_with_legacy_homework(tmp_path)

    plan = index_existing_course(
        course,
        brain_root=tmp_path / "brain",
        course_id="controlled-fusion-introduction",
        course_title="受控热核聚变导论",
    )

    by_path = {item.relative_source: item for item in plan.files}
    # Legacy homework files are now indexed, tagged with the logical exercises category,
    # but their physical path stays under homework/ (no migration).
    assert "homework/聚变作业总.pdf" in by_path
    assert "homework/Intro_CTF_MCF_homework_含答案.pdf" in by_path
    assert by_path["homework/聚变作业总.pdf"].category == "exercises"
    assert by_path["exercises/磁约束作业（2026-05-12）.pdf"].category == "exercises"
    assert plan.summary["category_exercises"] == 3

    write_materials_indexes(plan)
    index_md = (course / "materials_index.md").read_text(encoding="utf-8")
    assert "homework/聚变作业总.pdf" in index_md
    assert "homework/Intro_CTF_MCF_homework_含答案.pdf" in index_md


def test_obsidian_view_entries_merge_homework_into_exercises(tmp_path):
    course = _make_course_with_legacy_homework(tmp_path)
    plan = index_existing_course(
        course,
        brain_root=tmp_path / "brain",
        course_id="controlled-fusion-introduction",
        course_title="受控热核聚变导论",
    )

    entries = build_obsidian_view_entries(plan, term="2026春")
    exercise_entries = [e for e in entries if e["vault_rel"].endswith("/习题")]
    assert len(exercise_entries) == 1
    entry = exercise_entries[0]
    # The single 习题 vault view materializes from both exercises/ and the legacy homework/.
    assert "brain_rel" not in entry
    assert entry["brain_rels"] == [
        "knowledge/courses/controlled-fusion-introduction/exercises",
        "knowledge/courses/controlled-fusion-introduction/homework",
    ]
    assert entry["mode"] == "materialize"


def test_obsidian_view_entries_keep_single_brain_rel_without_homework(tmp_path):
    course = tmp_path / "brain" / "knowledge" / "courses" / "demo-course"
    (course / "exercises").mkdir(parents=True)
    _write_minimal_pdf(course / "exercises" / "作业一.pdf", "exercise text")
    plan = index_existing_course(
        course,
        brain_root=tmp_path / "brain",
        course_id="demo-course",
        course_title="示例课程",
    )

    entries = build_obsidian_view_entries(plan, term="2026春")
    exercise_entries = [e for e in entries if e["vault_rel"].endswith("/习题")]
    assert len(exercise_entries) == 1
    # No legacy homework/ -> stay on the simple single-source brain_rel shape.
    assert "brain_rels" not in exercise_entries[0]
    assert exercise_entries[0]["brain_rel"] == "knowledge/courses/demo-course/exercises"


def test_mirror_obsidian_merges_legacy_homework_into_xiti(tmp_path):
    course = _make_course_with_legacy_homework(tmp_path)
    obsidian_course = tmp_path / "brain-notes" / "10 课程" / "2026春" / "受控热核聚变导论"

    summary = mirror_existing_course_to_obsidian(course, obsidian_course)

    assert summary["obsidian_mirror_files"] == 3
    # Both the new exercise and the legacy homework land in the same 习题 visible folder.
    assert (obsidian_course / "习题" / "磁约束作业（2026-05-12）.pdf").exists()
    assert (obsidian_course / "习题" / "聚变作业总.pdf").exists()
    assert (obsidian_course / "习题" / "Intro_CTF_MCF_homework_含答案.pdf").exists()
