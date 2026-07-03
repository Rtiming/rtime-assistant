# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from brain_visualmd.backends import BatchContext, get_backend
from brain_visualmd.backends.vision_api import VisionApiBackend
from brain_visualmd.models import Plan
from brain_visualmd.plan import make_batches

PAGE_MD = (
    "<!-- page: 001 -->\n## 第 1 页：t\n\n![第1页](images/p-001.png)\n\n"
    "### 文字\n- a\n\n### 公式\n- 无\n\n### 图表\n- b\n\n### 存疑\n- 无\n"
)


def _docpack(tmp_path):
    d = tmp_path / "dp"
    (d / "images").mkdir(parents=True)
    (d / "images" / "p-001.png").write_bytes(b"\x89PNG\r\n")
    Plan(
        slug="dp", source="", source_sha256="abc", pages=1, batches=make_batches(1)
    ).write(d)
    return d


def test_vision_backend_against_mock_openai_server(tmp_path):
    received = {}

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):  # noqa: N802
            length = int(self.headers.get("Content-Length", "0"))
            received["payload"] = json.loads(self.rfile.read(length))
            body = json.dumps({"choices": [{"message": {"content": PAGE_MD}}]}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *args):  # silence
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        port = server.server_address[1]
        d = _docpack(tmp_path)
        plan = Plan.read(d)
        backend = VisionApiBackend(
            base_url=f"http://127.0.0.1:{port}/v1", model="mock-vlm"
        )
        result = backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
        )
        assert result.status == "written"
        out = (d / "_batches" / "pages-001-001.md").read_text("utf-8")
        assert "第 1 页" in out and "![第1页]" in out
        # the request carried the image as a data URL + the strict instruction
        msg = received["payload"]["messages"][0]["content"]
        assert any(p["type"] == "image_url" for p in msg)
        assert any("存疑" in p.get("text", "") for p in msg if p["type"] == "text")
    finally:
        server.shutdown()


def test_vision_backend_unconfigured_raises(tmp_path):
    d = _docpack(tmp_path)
    plan = Plan.read(d)
    backend = VisionApiBackend(base_url="", model="")
    with pytest.raises(RuntimeError):
        backend.process_batch(
            BatchContext(docpack_dir=d, plan=plan, batch=plan.batches[0])
        )


def test_strip_code_fence():
    from brain_visualmd.backends.vision_api import _strip_code_fence

    assert _strip_code_fence("```markdown\n## x\n```") == "## x"
    assert _strip_code_fence("## x\n- a") == "## x\n- a"  # untouched when no fence


def test_normalize_inline_math():
    from brain_visualmd.backends.vision_api import _normalize_inline_math

    # inline $...$ -> \(...\)
    assert _normalize_inline_math("其中 $E=E_n(k)$。") == "其中 \\(E=E_n(k)\\)。"
    # block $$...$$ is preserved untouched
    assert _normalize_inline_math("$$\nk_B T\n$$") == "$$\nk_B T\n$$"
    # mixed
    out = _normalize_inline_math("a $x$ b\n$$\ny\n$$\n")
    assert "\\(x\\)" in out and "$$\ny\n$$" in out
    # a lone/unpaired $ becomes full-width ＄ so it can't trip the inline_dollar gate
    assert _normalize_inline_math("价格 $5 元") == "价格 ＄5 元"


def test_vision_registered():
    assert (
        "vision"
        in __import__("brain_visualmd.backends", fromlist=["available"]).available()
    )
    assert isinstance(get_backend("vision"), VisionApiBackend)
