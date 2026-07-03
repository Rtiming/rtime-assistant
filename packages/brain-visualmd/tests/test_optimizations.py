# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from brain_visualmd.backends import BatchContext, get_backend
from brain_visualmd.backends.base import SyncPageBackend
from brain_visualmd.cli import main
from brain_visualmd.models import PageResult, Plan
from brain_visualmd.plan import make_batches
from brain_visualmd.validate import is_ok, validate_page_block


def _docpack(tmp_path, pages=2, name="dp"):
    d = tmp_path / name
    (d / "images").mkdir(parents=True)
    for i in range(1, pages + 1):
        (d / "images" / f"p-{i:03d}.png").write_bytes(b"\x89PNG")
    Plan(
        slug=name,
        source="",
        source_sha256="abc",
        pages=pages,
        batches=make_batches(pages),
    ).write(d)
    return d


def test_transcribe_skips_existing_batches(tmp_path):
    d = _docpack(tmp_path, 1)
    plan = Plan.read(d)
    backend = get_backend("stub")
    backend.process_batch(BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0]))
    batch_md = d / "_batches" / "pages-001-001.md"
    batch_md.write_text("SENTINEL", encoding="utf-8")  # simulate an already-done batch

    # resume: existing .md is not recomputed
    assert main(["transcribe", str(d), "--backend", "stub"]) == 0
    assert batch_md.read_text("utf-8") == "SENTINEL"

    # --force redoes it
    assert main(["transcribe", str(d), "--backend", "stub", "--force"]) == 0
    assert batch_md.read_text("utf-8") != "SENTINEL"


class _FlakyBackend(SyncPageBackend):
    name = "flaky"
    version = "1"
    max_page_retries = 2

    def transcribe_page(self, req, attempt: int = 0) -> PageResult:
        n = req.page_no
        if attempt == 0:  # invalid: missing png ref + sections
            md = f"<!-- page: {n:03d} -->\n## 第 {n} 页：x\n\n### 文字\n- a\n"
        else:  # valid, spec-conformant
            md = (
                f"<!-- page: {n:03d} -->\n## 第 {n} 页：x\n\n"
                f"![第{n}页](images/p-{n:03d}.png)\n\n"
                "### 文字\n- a\n\n### 公式\n- 无\n\n### 图表\n- b\n\n### 存疑\n- 无\n"
            )
        return PageResult(page_no=n, markdown=md, backend_id=self.backend_id)


def test_page_retry_recovers_from_gate_failure(tmp_path):
    d = _docpack(tmp_path, 1)
    plan = Plan.read(d)
    result = _FlakyBackend().process_batch(
        BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
    )
    block = result.path.read_text("utf-8")
    assert "![第1页]" in block  # the retried (valid) output won
    assert is_ok(validate_page_block(block, 1))


class _CountingBackend(SyncPageBackend):
    name = "count"
    version = "1"

    def __init__(self):
        self.calls = 0

    def transcribe_page(self, req, attempt: int = 0) -> PageResult:
        self.calls += 1
        n = req.page_no
        md = (
            f"<!-- page: {n:03d} -->\n## 第 {n} 页：x\n\n"
            f"![第{n}页](images/p-{n:03d}.png)\n\n"
            "### 文字\n- a\n\n### 公式\n- 无\n\n### 图表\n- b\n\n### 存疑\n- 无\n"
        )
        return PageResult(page_no=n, markdown=md, backend_id=self.backend_id)


def test_per_page_checkpoint_resumes_without_recomputing(tmp_path):
    d = _docpack(tmp_path, 2)
    plan = Plan.read(d)
    b1 = _CountingBackend()
    b1.process_batch(BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0]))
    assert b1.calls == 2
    assert (d / "_batches" / "pages-001-002.p001.md").exists()

    # simulate an interrupt after pages were done but before final assembly
    (d / "_batches" / "pages-001-002.md").unlink()
    b2 = _CountingBackend()
    b2.process_batch(BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0]))
    assert b2.calls == 0  # both pages loaded from checkpoints, not re-run
    assert (d / "_batches" / "pages-001-002.md").exists()


def test_scan_skips_finished_sources(tmp_path, capsys):
    src = tmp_path / "lib"
    src.mkdir()
    (src / "a.png").write_bytes(b"\x89PNG-a")
    (src / "b.png").write_bytes(b"\x89PNG-b")
    out = tmp_path / "out"

    assert main(["scan", str(src), "--out", str(out), "--backend", "stub"]) == 0
    first = capsys.readouterr().out
    assert "done: a" in first and "done: b" in first

    # second run: both already finished -> skipped (sha match)
    assert main(["scan", str(src), "--out", str(out), "--backend", "stub"]) == 0
    second = capsys.readouterr().out
    assert "skip (done): a" in second and "skip (done): b" in second
