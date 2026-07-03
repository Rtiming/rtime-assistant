# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

from test_docpack_builder import _write_minimal_pdf
from test_docpack_samples import _make_fixture_tree
from test_docpack_validator import _make_docpack


ROOT = Path(__file__).resolve().parents[1]
CLI = ROOT / "packages" / "brain-docpack" / "src" / "brain_docpack" / "cli.py"


def _load_cli():
    spec = importlib.util.spec_from_file_location("brain_docpack_cli", CLI)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules["brain_docpack_cli"] = module
    spec.loader.exec_module(module)
    return module


def test_cli_doctor_reports_repo_root(capfd):
    cli = _load_cli()

    assert cli.main(["doctor"]) == 0
    captured = capfd.readouterr()

    # doctor now emits a single JSON object (so the gateway can consume it).
    data = json.loads(captured.out)
    assert data["ok"] is True
    assert data["repo_root"] == str(ROOT)
    assert data["scripts"]["scripts/build-docpack.py"] is True
    assert data["scripts"]["schemas/docpack"] is True
    assert "pdfinfo" in data["tools"]
    assert "pdftotext" in data["tools"]


def test_cli_validate_delegates_to_validator(tmp_path, capfd):
    cli = _load_cli()
    docpack = _make_docpack(tmp_path)

    assert cli.main(["validate", str(docpack), "--json"]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["ok"] is True
    assert data["errors"] == []


def test_cli_select_samples_outputs_json(tmp_path, capfd):
    cli = _load_cli()
    _make_fixture_tree(tmp_path)

    assert cli.main(["select-samples", str(tmp_path)]) == 0
    captured = capfd.readouterr()
    data = json.loads(captured.out)

    assert data["missing_categories"] == []
    assert data["sample_count"] >= 10


def test_cli_build_delegates_to_builder(tmp_path):
    cli = _load_cli()
    source = tmp_path / "lesson.pdf"
    _write_minimal_pdf(source, "CLI package PDF smoke text.")
    out = tmp_path / "lesson.docpack"

    assert cli.main(["build", str(source), "--out", str(out), "--docpack-id", "cli-pdf"]) == 0

    manifest = json.loads((out / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["docpack_id"] == "cli-pdf"
    assert manifest["display"]["display_pdf"] == "display/display.pdf"


def _package_env() -> dict[str, str]:
    env = os.environ.copy()
    env["PYTHONPATH"] = f"{ROOT / 'packages' / 'brain-docpack' / 'src'}{os.pathsep}{env.get('PYTHONPATH', '')}"
    env["RTIME_ASSISTANT_ROOT"] = str(ROOT)
    return env


def test_cli_course_intake_accepts_approved(tmp_path):
    source = tmp_path / "downloads"
    source.mkdir()
    _write_minimal_pdf(source / "第一章-课程介绍.pdf", "course intro text layer")
    brain = tmp_path / "brain"

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "brain_docpack",
            "course-intake",
            str(source),
            "--brain-root",
            str(brain),
            "--course-id",
            "demo-course",
            "--course-title",
            "示例课程",
            "--include-all",
            "--apply",
            "--approved",
            "--json",
        ],
        cwd=ROOT,
        env=_package_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    assert (brain / "knowledge" / "courses" / "demo-course" / "materials_index.csv").exists()


def test_cli_course_index_writes_existing_course_indexes(tmp_path):
    brain = tmp_path / "brain"
    course = brain / "knowledge" / "courses" / "demo-course"
    (course / "slides").mkdir(parents=True)
    _write_minimal_pdf(course / "slides" / "demo-course_lecture-01_intro.pdf", "intro text")
    (course / "slides" / "README.md").write_text("# slides\n", encoding="utf-8")
    (course / "slides" / "text").mkdir()
    (course / "slides" / "text" / "page-001.txt").write_text("derived text", encoding="utf-8")

    completed = subprocess.run(
        [
            sys.executable,
            "-m",
            "brain_docpack",
            "course-index",
            "--brain-root",
            str(brain),
            "--course-id",
            "demo-course",
            "--course-title",
            "示例课程",
            "--json",
        ],
        cwd=ROOT,
        env=_package_env(),
        text=True,
        capture_output=True,
        check=False,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr
    data = json.loads(completed.stdout)
    assert data["summary"]["files"] == 1
    assert (course / "materials_index.csv").exists()
    assert "demo-course_lecture-01_intro.pdf" in (course / "materials_index.md").read_text(
        encoding="utf-8"
    )
