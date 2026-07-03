# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import pytest
from brain_visualmd.backends import BatchContext, get_backend
from brain_visualmd.merge import MergeError, merge_docpack
from brain_visualmd.models import Plan
from brain_visualmd.plan import make_batches
from brain_visualmd.validate import is_ok, validate_docpack


def make_docpack(tmp_path, pages=3):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    for i in range(1, pages + 1):
        (d / "images" / f"p-{i:03d}.png").write_bytes(b"x")
    Plan(
        slug="dp",
        source="",
        source_sha256="abc",
        pages=pages,
        batches=make_batches(pages),
    ).write(d)
    return d


def test_stub_pipeline_runs_green(tmp_path):
    d = make_docpack(tmp_path, 3)
    plan = Plan.read(d)
    backend = get_backend("stub")
    for batch in plan.batches:
        result = backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=batch)
        )
        assert result.status == "written"
    out = merge_docpack(d, backend_id=backend.backend_id, status="draft")
    assert out.exists()
    issues = validate_docpack(d)
    assert is_ok(issues), [i.message for i in issues]


def test_failing_page_emits_placeholder_not_crash(tmp_path):
    # one degenerate page (model raises / returns empty) must NOT abort the run:
    # the page gets a 存疑-flagged placeholder, the batch still writes + gates green.
    from brain_visualmd.backends.base import EmptyContentError, SyncPageBackend

    class _AlwaysEmpty(SyncPageBackend):
        name = "empty"
        version = "1"
        max_page_retries = 1

        def transcribe_page(self, req, attempt: int = 0):
            raise EmptyContentError("model returned empty content (finish=length)")

    d = make_docpack(tmp_path, 2)
    plan = Plan.read(d)
    backend = _AlwaysEmpty()
    for batch in plan.batches:
        result = backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=batch)
        )
        assert result.status == "written"
    merge_docpack(d, backend_id=backend.backend_id, status="draft")
    out = (d / f"{plan.slug}.md").read_text("utf-8")
    assert out.count("本页未能自动转写") == 2  # both pages placeholdered (once each)
    assert "本页自动转写失败" in out  # flagged under 存疑
    assert is_ok(validate_docpack(d)), [i.message for i in validate_docpack(d)]


def test_agent_backend_emits_task_and_blocks_merge(tmp_path):
    d = make_docpack(tmp_path, 2)
    plan = Plan.read(d)
    backend = get_backend("agent")
    for batch in plan.batches:
        result = backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=batch)
        )
        assert result.status == "pending"
        assert result.path.name.endswith(".task.md")
        # the task spec carries the page image refs and the strict format rules
        text = result.path.read_text("utf-8")
        assert "images/p-001.png" in text
        assert "存疑" in text
    with pytest.raises(MergeError):
        merge_docpack(d)


def test_unknown_backend_raises(tmp_path):
    with pytest.raises(KeyError):
        get_backend("does-not-exist")
