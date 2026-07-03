# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
from __future__ import annotations

import importlib.util
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "deploy" / "bin" / "rtime-web-fetch"


def load_module():
    loader = SourceFileLoader("rtime_web_fetch", str(SCRIPT))
    spec = importlib.util.spec_from_loader("rtime_web_fetch", loader)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_web_fetch_blocks_localhost():
    module = load_module()

    with pytest.raises(SystemExit):
        module._validate_public_url("http://127.0.0.1:9981/healthz")


def test_web_fetch_rejects_non_http_urls():
    module = load_module()

    with pytest.raises(SystemExit):
        module._validate_public_url("file:///etc/passwd")


def test_html_to_text_removes_scripts_and_tags():
    module = load_module()

    text = module._html_to_text("<h1>A</h1><script>secret()</script><p>B &amp; C</p>")

    assert "secret" not in text
    assert "A" in text
    assert "B & C" in text


def test_session_cookie_path_rejects_unsafe_name(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "SESSION_DIR", tmp_path)

    with pytest.raises(SystemExit):
        module._session_cookie_path("../secret")


def test_session_cookie_path_requires_private_permissions(tmp_path, monkeypatch):
    module = load_module()
    monkeypatch.setattr(module, "SESSION_DIR", tmp_path)
    cookie_file = tmp_path / "icourse.cookies.txt"
    cookie_file.write_text("# Netscape HTTP Cookie File\n", encoding="utf-8")
    cookie_file.chmod(0o644)

    with pytest.raises(SystemExit):
        module._session_cookie_path("icourse")


def test_safe_filename_strips_paths_and_bad_chars():
    module = load_module()

    assert module._safe_filename("../secret.txt") == "secret.txt"
    assert module._safe_filename("bad:name?.pdf") == "bad_name_.pdf"
    assert module._safe_filename("") == "download.bin"


def test_link_collector_absolutizes_hrefs():
    module = load_module()
    parser = module._LinkCollector("https://example.test/path/page.html")

    parser.feed('<a href="/file.pdf"> File </a><a href="next.html">Next</a>')

    assert parser.links == [
        ("File", "https://example.test/file.pdf"),
        ("Next", "https://example.test/path/next.html"),
    ]
