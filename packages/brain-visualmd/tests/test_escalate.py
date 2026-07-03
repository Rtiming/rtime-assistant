# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from brain_visualmd.backends import BatchContext, get_backend
from brain_visualmd.backends.base import SyncPageBackend
from brain_visualmd.backends.escalate import EscalationBackend
from brain_visualmd.models import PageResult, Plan
from brain_visualmd.plan import make_batches


def _docpack(tmp_path, pages=1):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    for i in range(1, pages + 1):
        (d / "images" / f"p-{i:03d}.png").write_bytes(b"\x89PNG")
    Plan(
        slug="dp",
        source="",
        source_sha256="abc",
        pages=pages,
        batches=make_batches(pages),
    ).write(d)
    return d


def _page(n, *, formula: bool):
    body = "$$\nk_B T\n$$" if formula else "- 无"
    return (
        f"<!-- page: {n:03d} -->\n## 第 {n} 页：x\n\n![第{n}页](images/p-{n:03d}.png)\n\n"
        f"### 文字\n- a\n\n### 公式\n{body}\n\n### 图表\n- b\n\n### 存疑\n- 无\n"
    )


class _Tagged(SyncPageBackend):
    """Returns a page tagged with which model produced it; counts calls."""

    def __init__(self, tag, *, formula):
        self.tag = tag
        self.formula = formula
        self.calls = 0
        self.version = tag

    def transcribe_page(self, req, attempt: int = 0) -> PageResult:
        self.calls += 1
        md = _page(req.page_no, formula=self.formula).replace("- a", f"- {self.tag}")
        return PageResult(page_no=req.page_no, markdown=md, backend_id=self.tag)


def test_text_page_stays_on_base(tmp_path):
    d = _docpack(tmp_path)
    plan = Plan.read(d)
    base = _Tagged("base", formula=False)
    strong = _Tagged("strong", formula=False)
    EscalationBackend(base=base, strong=strong).process_batch(
        BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
    )
    assert base.calls == 1 and strong.calls == 0
    assert "base" in (d / "_batches" / "pages-001-001.md").read_text("utf-8")


def test_formula_page_escalates_to_strong(tmp_path):
    d = _docpack(tmp_path)
    plan = Plan.read(d)
    base = _Tagged("base", formula=True)  # base output has a formula -> escalate
    strong = _Tagged("strong", formula=True)
    EscalationBackend(base=base, strong=strong).process_batch(
        BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
    )
    assert base.calls == 1 and strong.calls == 1
    out = (d / "_batches" / "pages-001-001.md").read_text("utf-8")
    assert "strong" in out  # the strong model's result won


class _Raising(SyncPageBackend):
    name = "raise"
    version = "1"

    def transcribe_page(self, req, attempt: int = 0) -> PageResult:
        # TimeoutError (a subclass of OSError, NOT RuntimeError) is the real
        # failure that aborted the run — the fallback must catch it too.
        raise TimeoutError("strong model timed out")


def test_escalation_falls_back_to_base_when_strong_errors(tmp_path):
    d = _docpack(tmp_path)
    plan = Plan.read(d)
    base = _Tagged("base", formula=True)  # formula page -> escalate attempt
    EscalationBackend(base=base, strong=_Raising()).process_batch(
        BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
    )
    out = (d / "_batches" / "pages-001-001.md").read_text("utf-8")
    assert "base" in out  # strong raised -> fell back to base, not empty/abort


def test_escalation_uses_layout_formula_signal(tmp_path):
    from brain_visualmd.layout import FORMULA, LayoutRegion, analyze_docpack

    d = _docpack(tmp_path)  # 1 page

    class _MD:
        name = "mock"

        def detect(self, img, page_no):
            return [LayoutRegion(page_no, FORMULA, [0, 0, 1, 1], 0.9, 0)]

    analyze_docpack(d, _MD(), 1)  # mark page 1 as having a formula region
    plan = Plan.read(d)
    # base output has NO $$ -> the text heuristic alone would NOT escalate
    base = _Tagged("base", formula=False)
    strong = _Tagged("strong", formula=False)
    EscalationBackend(base=base, strong=strong).process_batch(
        BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
    )
    assert strong.calls == 1  # the layout formula region forced escalation


def test_escalate_registered():
    assert (
        "escalate"
        in __import__("brain_visualmd.backends", fromlist=["available"]).available()
    )
    assert isinstance(get_backend("escalate"), EscalationBackend)
