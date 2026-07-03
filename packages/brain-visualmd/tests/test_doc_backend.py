# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

from brain_visualmd.backends import BatchContext, get_backend
from brain_visualmd.backends.doc import DocOcrBackend, _wrap_doc_page
from brain_visualmd.models import Plan
from brain_visualmd.plan import make_batches
from brain_visualmd.validate import is_ok, validate_page_block

# free-form doc-model output: a title, text with inline $...$, a block formula
DOC_OUTPUT = "6.1 分布函数\n\n其中 $E=E_n(k)$。\n$$\nf_0 = \\frac{1}{e^x+1}\n$$"


def test_first_line_skips_formulas_and_strips_dollar():
    from brain_visualmd.backends.doc import _first_line

    # a page that opens with a formula -> heading uses the first real text line
    assert _first_line("$$f_0=\\frac{1}{x}$$\n\n热电效应简介") == "热电效应简介"
    # never let a $ into the heading even from a truncated formula
    assert "$" not in _first_line("$$ f_0 = \\frac{1}{\\exp")


def test_wrap_doc_page_passes_machine_gate():
    block = _wrap_doc_page(1, DOC_OUTPUT)
    assert "## 第 1 页：6.1 分布函数" in block
    assert "![第1页](images/p-001.png)" in block
    # all four section headers present -> passes the existing gate
    assert is_ok(validate_page_block(block, 1)), [
        i.message for i in validate_page_block(block, 1)
    ]


def test_unpaired_dollar_page_passes_gate():
    # regression (audit W6): an unpaired $ (e.g. currency on a slide) must NOT
    # trip the inline_dollar gate, and must not leave a backslash in the heading.
    block = _wrap_doc_page(1, "本页讲成本，约 $5 每页", None)
    assert is_ok(validate_page_block(block, 1)), [
        i.message for i in validate_page_block(block, 1)
    ]
    assert "＄5" in block  # $ normalized to full-width, gate-safe
    assert "\\$" not in block and "\\5" not in block  # no escape/backslash artifact


def _docpack(tmp_path):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    (d / "images" / "p-001.png").write_bytes(b"\x89PNG")
    Plan(
        slug="dp", source="", source_sha256="abc", pages=1, batches=make_batches(1)
    ).write(d)
    return d


def test_doc_backend_wraps_and_normalizes(tmp_path):
    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            self.rfile.read(int(self.headers.get("Content-Length", "0")))
            body = json.dumps(
                {"choices": [{"message": {"content": DOC_OUTPUT}}]}
            ).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        d = _docpack(tmp_path)
        plan = Plan.read(d)
        backend = DocOcrBackend(
            base_url=f"http://127.0.0.1:{server.server_address[1]}/v1", model="glm-ocr"
        )
        backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
        )
        out = (d / "_batches" / "pages-001-001.md").read_text("utf-8")
        assert "\\(E=E_n(k)\\)" in out  # inline $...$ normalized to \(...\)
        assert "$$" in out  # block formula preserved
        assert "### 存疑" in out  # gate-conformant
    finally:
        server.shutdown()


def test_doc_registered():
    assert (
        "doc"
        in __import__("brain_visualmd.backends", fromlist=["available"]).available()
    )
    assert isinstance(get_backend("doc"), DocOcrBackend)
