# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
import base64
import json
import io
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "apps" / "assistant-gateway"))

import gateway  # noqa: E402
import gateway_runner  # noqa: E402  (P6:claude 子进程/prewarm 簇已抽到本模块;patch 其内部回调须打在此)
import memory  # noqa: E402  (gateway 子系统:记忆;_parse_memory_frontmatter 等在此模块查找)


def parse_sse_frames(raw: str) -> list[dict]:
    """Decode an SSE response body into its list of JSON event dicts, so tests
    assert on decoded {"type": ...} frames instead of brittle substring/index
    matching against serialized output."""
    frames: list[dict] = []
    for block in raw.replace("\r\n", "\n").split("\n\n"):
        data = "\n".join(
            line[len("data:"):].lstrip()
            for line in block.split("\n")
            if line.startswith("data:")
        ).strip()
        if not data or data == "[DONE]":
            continue
        try:
            frames.append(json.loads(data))
        except json.JSONDecodeError:
            pass
    return frames


def make_brain(tmp_path: Path) -> Path:
    brain = tmp_path / "brain"
    slides = brain / "knowledge" / "courses" / "solid-state-physics" / "slides"
    (slides / "images" / "15自由电子论").mkdir(parents=True)
    (slides / "text" / "15自由电子论").mkdir(parents=True)
    (slides / "15自由电子论.pdf").write_bytes(b"%PDF-1.4 fake")
    (brain / "personal-data").mkdir()
    (brain / "personal-data" / "secret.md").write_text("secret", encoding="utf-8")
    return brain


def write_memory_card(
    brain: Path,
    name: str,
    claim: str,
    *,
    body: str = "这条记忆只在相关任务中作为偏好参考。",
    sensitivity: str = "normal",
    confidence: str = "user-stated",
    layer: str = "trait",
    expires: str = "",
    scope: str = "assistant-behavior",
    unlock_hints: str = "[报告, 结论]",
) -> Path:
    cards = brain / "memory" / "cards"
    cards.mkdir(parents=True, exist_ok=True)
    expires_line = f"expires: {expires}\n" if expires else ""
    path = cards / name
    path.write_text(
        "\n".join(
            [
                "---",
                "type: memory-card",
                f"claim: {claim}",
                f"scope: {scope}",
                "source: test",
                "observed_at: 2026-06-11",
                f"confidence: {confidence}",
                f"layer: {layer}",
                expires_line.rstrip(),
                "supersedes: []",
                f"sensitivity: {sensitivity}",
                f"unlock_hints: {unlock_hints}",
                "---",
                body,
                "",
            ]
        ).replace("\n\nsupersedes", "\nsupersedes"),
        encoding="utf-8",
    )
    return path


FM_NOTE = """---
type: course-pdf
title: "15自由电子论"
source: "knowledge/courses/solid-state-physics/slides/15自由电子论.pdf"
page_image_dir: "images/15自由电子论"
raw_text_dir: "text/15自由电子论"
tags: [course/solid-state-physics]
---

# 15自由电子论
正文。
"""


def test_extract_frontmatter_basic():
    fm = gateway.extract_frontmatter(FM_NOTE)
    assert fm["type"] == "course-pdf"
    assert fm["title"] == "15自由电子论"
    assert fm["source"].endswith("15自由电子论.pdf")
    assert "tags" not in fm  # list values ignored


def test_extract_frontmatter_absent_or_malformed():
    assert gateway.extract_frontmatter("没有frontmatter的正文") == {}
    assert gateway.extract_frontmatter("") == {}
    assert gateway.extract_frontmatter("---\n只有一段没有结束") == {}


def test_collect_unlocks_resolves_relative_to_source(tmp_path):
    brain = make_brain(tmp_path)
    fm = gateway.extract_frontmatter(FM_NOTE)
    unlocks = gateway.collect_unlocks(fm, brain)
    paths = [str(p) for _, p in unlocks]
    assert any(p.endswith("15自由电子论.pdf") for p in paths)
    assert any(p.endswith("images/15自由电子论") for p in paths)
    assert any(p.endswith("text/15自由电子论") for p in paths)


def test_safe_brain_path_blocks_escape_and_sensitive(tmp_path):
    brain = make_brain(tmp_path)
    assert gateway.safe_brain_path("../outside.md", brain) is None
    assert gateway.safe_brain_path("personal-data/secret.md", brain) is None
    assert gateway.safe_brain_path("knowledge/不存在.pdf", brain) is None
    assert gateway.safe_brain_path("https://example.com/a.pdf", brain) is None
    ok = gateway.safe_brain_path(
        "knowledge/courses/solid-state-physics/slides/15自由电子论.pdf", brain
    )
    assert ok is not None and ok.is_file()


def test_build_prompt_contains_key_sections(tmp_path):
    brain = make_brain(tmp_path)
    fm = gateway.extract_frontmatter(FM_NOTE)
    unlocks = gateway.collect_unlocks(fm, brain)
    body = {
        "schema_version": 1,
        "entry": "obsidian",
        "message": "费米面附近态密度怎么算？",
        "context": {
            "active_file": {"path": "课程/固体物理资料/课件/15自由电子论.md"},
            "note": {"text": FM_NOTE, "chars": len(FM_NOTE), "truncated": False},
            "selection": None,
        },
        "options": {"task_mode": "explain", "ui_language": "zh-CN"},
    }
    cfg = {"index_pythonpath": "/x/src", "index_db": "/x/idx.sqlite"}
    prompt = gateway.build_prompt(body, unlocks, cfg)
    assert "personal-data" in prompt  # exclusion rule stated
    assert "15自由电子论.pdf" in prompt
    assert "brain_library index query" in prompt
    assert "费米面附近态密度怎么算" in prompt
    assert "讲解" in prompt  # explain task hint


def test_parse_sources_block():
    answer = (
        "电子热容正比于费米面态密度。\n\n来源：\n"
        "- knowledge/courses/solid-state-physics/slides/15自由电子论.pdf#page=10\n"
        "- knowledge/courses/solid-state-physics/slides/15自由电子论.md\n"
    )
    sources = gateway.parse_sources(answer)
    assert sources[0]["path"].endswith("15自由电子论.pdf")
    assert sources[0]["page"] == 10
    assert sources[1]["path"].endswith("15自由电子论.md")
    assert "page" not in sources[1]


def test_parse_sources_strips_inline_backticks():
    answer = (
        "概括。\n\n来源：\n"
        "- `knowledge/courses/advanced-photonics/lectures/lesson2-main.pdf#page=12`\n"
    )
    sources = gateway.parse_sources(answer)
    assert sources == [
        {
            "path": "knowledge/courses/advanced-photonics/lectures/lesson2-main.pdf",
            "page": 12,
        }
    ]


def test_parse_sources_absent():
    assert gateway.parse_sources("没有来源节的回答。") == []


def test_allowed_tools_is_read_only():
    cfg = {"index_pythonpath": "/x/src", "index_db": "/x/idx.sqlite"}
    tools = gateway.allowed_tools(cfg)
    assert tools.startswith("Read,Glob,Grep,Bash(")
    assert "WebSearch" in tools
    assert "WebFetch" in tools
    assert "Bash(rtime-web-fetch *)" in tools
    for forbidden in ("Write", "Edit"):
        assert forbidden not in tools


def test_allowed_tools_full_access_allows_writes():
    cfg = {
        "index_pythonpath": "/x/src",
        "index_db": "/x/idx.sqlite",
        "gateway_access_mode": "full",
    }
    tools = gateway.allowed_tools(cfg).split(",")
    assert "Write" in tools
    assert "Edit" in tools
    assert "MultiEdit" in tools
    assert "Bash(*)" in tools


def test_allowed_tools_can_add_browser_mcp_names_without_duplicates():
    cfg = {
        "index_pythonpath": "/x/src",
        "index_db": "/x/idx.sqlite",
        "extra_allowed_tools": "mcp__browser__*,mcp__playwright__*,WebSearch",
    }
    tools = gateway.allowed_tools(cfg).split(",")
    assert "mcp__browser__*" in tools
    assert "mcp__playwright__*" in tools
    assert tools.count("WebSearch") == 1


def test_allowed_tools_can_disable_builtin_web_tools():
    cfg = {
        "index_pythonpath": "/x/src",
        "index_db": "/x/idx.sqlite",
        "web_tools_enabled": False,
    }
    tools = gateway.allowed_tools(cfg)
    assert "WebSearch" not in tools
    assert "WebFetch" not in tools
    assert "rtime-web-fetch" not in tools


def test_plugin_release_file_allows_only_release_assets(tmp_path):
    release_dir = tmp_path / "release"
    release_dir.mkdir()
    for name in ("release.json", "manifest.json", "main.js", "styles.css"):
        (release_dir / name).write_text(name, encoding="utf-8")
    cfg = {"plugin_release_dir": release_dir}

    resolved = gateway.plugin_release_file("/api/obsidian/plugin-release/", cfg)
    assert resolved is not None
    assert resolved[0].name == "release.json"
    assert resolved[1].startswith("application/json")

    resolved = gateway.plugin_release_file("/api/obsidian/plugin-release/main.js?cache=0", cfg)
    assert resolved is not None
    assert resolved[0].name == "main.js"
    assert resolved[1].startswith("application/javascript")

    assert gateway.plugin_release_file("/api/obsidian/plugin-release/../data.json", cfg) is None
    assert gateway.plugin_release_file("/api/obsidian/plugin-release/data.json", cfg) is None
    assert gateway.plugin_release_file("/api/obsidian/other/release.json", cfg) is None


def _handler_with_cfg(log_dir: Path, *, capture: bool = False, failed: bool = False):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {
        "log_dir": log_dir,
        "memory_capture_enabled": capture,
        "memory_failed_query_log_enabled": failed,
        "memory_capture_max_chars": 80,
    }
    return handler


def _wait_for(path: Path) -> None:
    for _ in range(30):
        if path.exists():
            return
        time.sleep(0.02)
    raise AssertionError(f"file not written: {path}")


def _wait_for_glob(root: Path, pattern: str) -> Path:
    for _ in range(30):
        matches = list(root.glob(pattern))
        if matches:
            return matches[0]
        time.sleep(0.02)
    raise AssertionError(f"file not written: {root / pattern}")


def _wait_for_nonempty(path: Path) -> None:
    for _ in range(30):
        if path.exists() and path.read_text(encoding="utf-8").strip():
            return
        time.sleep(0.02)
    raise AssertionError(f"file not populated: {path}")


def test_memory_logs_disabled_preserves_request_log_only(tmp_path):
    handler = _handler_with_cfg(tmp_path, capture=False, failed=False)
    body = {"entry": "obsidian", "message": "请记住：我偏好先看图。", "context": {}, "options": {}}
    payload = {"answer": "ok", "sources": []}

    handler._log_request(body, 200, time.time(), payload)

    assert (tmp_path / "requests.jsonl").exists()
    assert not (tmp_path / "memory-session-materials").exists()
    assert not (tmp_path / "failed-queries.jsonl").exists()


def test_memory_capture_and_failed_query_logs_are_async_jsonl(tmp_path):
    handler = _handler_with_cfg(tmp_path, capture=True, failed=True)
    body = {
        "entry": "obsidian",
        "message": "请记住：我复习时更希望先看页图，再核对公式转写。",
        "context": {"active_file": {"path": "课程/固体物理/15.md"}},
        "options": {"task_mode": "ask"},
    }
    payload = {"answer": "没有找到相关来源。", "sources": []}

    handler._log_request(body, 200, time.time(), payload)

    material = _wait_for_glob(tmp_path / "memory-session-materials", "*.jsonl")
    failed = tmp_path / "failed-queries.jsonl"
    _wait_for(failed)
    material_record = json.loads(material.read_text(encoding="utf-8").splitlines()[0])
    failed_record = json.loads(failed.read_text(encoding="utf-8").splitlines()[0])
    assert material_record["message_excerpt"].startswith("请记住")
    assert material_record["source_count"] == 0
    assert failed_record["reason"] in {"zero_sources", "zero_hit_text"}
    assert failed_record["query_excerpt"].startswith("请记住")


def test_context_source_injection_filters_and_records_metadata(tmp_path):
    brain = make_brain(tmp_path)
    system = brain / "_system"
    system.mkdir()
    (system / "study-plan.md").write_text("今日复习：热统A系综与配分函数。", encoding="utf-8")
    (system / "old-plan.md").write_text("过期计划不应注入。", encoding="utf-8")
    manifest = system / "rtime-context-sources.jsonl"
    records = [
        {
            "id": "study",
            "status": "active",
            "kind": "study-plan",
            "title": "当前复习计划",
            "path": "_system/study-plan.md",
            "tags": ["复习", "计划", "study-plan"],
            "priority": 100,
            "active_from": "2026-01-01",
            "expires": "2099-01-01",
            "max_chars": 1000,
        },
        {
            "id": "inactive",
            "status": "inactive",
            "kind": "study-plan",
            "title": "inactive",
            "path": "_system/study-plan.md",
            "tags": ["复习"],
            "priority": 100,
        },
        {
            "id": "expired",
            "status": "active",
            "kind": "study-plan",
            "title": "expired",
            "path": "_system/old-plan.md",
            "tags": ["复习"],
            "priority": 100,
            "expires": "2020-01-01",
        },
        {
            "id": "secret",
            "status": "active",
            "kind": "preference",
            "title": "secret",
            "path": "personal-data/secret.md",
            "tags": ["复习"],
            "priority": 100,
        },
    ]
    manifest.write_text("\n".join(json.dumps(item, ensure_ascii=False) for item in records) + "\n", encoding="utf-8")
    body = {"message": "我的复习计划在哪里？今天计划是什么？", "context": {}, "options": {"task_mode": "ask"}}
    cfg = {
        "brain_root": brain,
        "context_sources_enabled": True,
        "context_sources_path": manifest,
        "context_sources_max_items": 3,
        "context_sources_max_chars": 4000,
        "memory_injection_enabled": False,
    }

    section, events = gateway.context_source_injection(body, cfg)

    assert section and "今日复习" in section
    assert "过期计划" not in section
    assert "secret" not in section
    assert events["referenced_context_sources"] == [
        {"id": "study", "path": "_system/study-plan.md", "kind": "study-plan"}
    ]
    merged = gateway.memory_events_for_request(body, cfg)
    assert merged["referenced_context_sources"][0]["id"] == "study"


def test_gateway_memory_candidate_writes_review_queue_only(tmp_path):
    brain = make_brain(tmp_path)
    review_dir = brain / "memory" / "review-queue"
    body = {
        "entry": "obsidian",
        "message": "请记住：我复习时先看当天计划再刷题。",
        "context": {"memory": {"commands": ["remember"]}},
    }
    cfg = {"memory_candidate_write_enabled": True, "memory_candidate_review_dir": review_dir}

    result = gateway._write_review_queue_candidate(body, cfg)

    assert result["written"] is True
    assert review_dir.exists()
    files = list(review_dir.glob("*.md"))
    assert len(files) == 1
    text = files[0].read_text(encoding="utf-8")
    assert "type: memory-card" in text
    assert "scope: assistant-personalization" in text
    assert not (brain / "memory" / "cards").exists()


def test_gateway_memory_candidate_holds_sensitive_claim(tmp_path):
    body = {
        "entry": "obsidian",
        "message": "请记住：我的 token 是 secret-token-value",
        "context": {"memory": {"commands": ["remember"]}},
    }
    cfg = {"memory_candidate_write_enabled": True, "memory_candidate_review_dir": tmp_path / "review"}

    result = gateway._write_review_queue_candidate(body, cfg)

    assert result["action"] == "hold"
    assert result["written"] is False
    assert not (tmp_path / "review").exists()


def make_brain_with_manifest(tmp_path):
    brain = make_brain(tmp_path)
    slides = brain / "knowledge" / "courses" / "solid-state-physics" / "slides"
    (slides / "15自由电子论.md").write_text(FM_NOTE, encoding="utf-8")
    idx = brain / "_indexes"
    idx.mkdir()
    import json as _json
    rec = {
        "sha256": "x", "canonical": True,
        "brain_path": "knowledge/courses/solid-state-physics/slides/15自由电子论.pdf",
    }
    (idx / "pdf-manifest.jsonl").write_text(_json.dumps(rec) + "\n", encoding="utf-8")
    return brain


def test_resolve_pdf_unlocks_via_manifest(tmp_path):
    brain = make_brain_with_manifest(tmp_path)
    gateway._MANIFEST_CACHE.update(mtime=None, by_basename={})
    unlocks = gateway.resolve_pdf_unlocks(
        "10 课程/2026春/固体物理/课件/15自由电子论.pdf", brain
    )
    labels = [lbl for lbl, _ in unlocks]
    paths = [str(p) for _, p in unlocks]
    assert "正在阅读的PDF原件" in labels
    assert any(p.endswith("15自由电子论.md") for p in paths)
    assert any(p.endswith("images/15自由电子论") for p in paths)


def test_resolve_pdf_unlocks_unknown_basename(tmp_path):
    brain = make_brain_with_manifest(tmp_path)
    gateway._MANIFEST_CACHE.update(mtime=None, by_basename={})
    assert gateway.resolve_pdf_unlocks("某处/不存在.pdf", brain) == []


def test_build_prompt_includes_pdf_page_hint(tmp_path):
    body = {
        "schema_version": 1,
        "message": "这页公式怎么来的",
        "context": {
            "active_file": {"path": "10 课程/2026春/固体物理/课件/15自由电子论.pdf"},
            "note": None,
            "selection": None,
            "pdf": {"page": 5},
        },
        "options": {"task_mode": "explain"},
    }
    cfg = {"index_pythonpath": "/x", "index_db": "/x.sqlite"}
    prompt = gateway.build_prompt(body, [], cfg)
    assert "第5页" in prompt
    assert "page-*5*" in prompt


def test_process_prepare_caches_pdf_unlocks(tmp_path):
    brain = make_brain_with_manifest(tmp_path)
    gateway._MANIFEST_CACHE.update(mtime=None, by_basename={})
    gateway._PREPARE_CACHE.clear()
    body = {
        "schema_version": 1,
        "entry": "obsidian",
        "message": "",
        "context": {
            "active_file": {
                "path": "10 课程/2026春/固体物理/课件/15自由电子论.pdf",
                "mtime": 123,
            },
            "note": None,
            "selection": None,
            "pdf": {"page": 5},
        },
        "options": {"task_mode": "ask", "context_mode": "current-note"},
    }
    cfg = {
        "brain_root": brain,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "prepare_cache_ttl": 180,
        "prepare_cache_max": 8,
    }
    status, payload = gateway.process_prepare(body, cfg)

    assert status == 200
    assert payload["ok"] is True
    assert payload["unlock_count"] >= 2
    assert payload["prepare_id"].startswith("prep-")
    assert any(item["path"].endswith("15自由电子论.pdf") for item in payload["unlocks"])

    body["prepare_id"] = payload["prepare_id"]
    unlocks, prepare_id = gateway.cached_or_resolved_unlocks(body, cfg)
    assert prepare_id == payload["prepare_id"]
    assert any(path.name == "15自由电子论.pdf" for _label, path in unlocks)
    assert "absolute_path" not in gateway._PREPARE_CACHE[payload["prepare_id"]]["unlocks"][0]

    gateway._PREPARE_CACHE[payload["prepare_id"]]["unlocks"] = [
        {"label": "bad", "path": "personal-data/secret.md"}
    ]
    unlocks, prepare_id = gateway.cached_or_resolved_unlocks(body, cfg)
    assert prepare_id is None
    assert all("personal-data" not in str(path) for _label, path in unlocks)


def test_process_prepare_surfaces_prewarm_status(monkeypatch, tmp_path):
    brain = make_brain(tmp_path)
    body = {
        "schema_version": 1,
        "entry": "obsidian",
        "message": "",
        "context": {"active_file": None, "note": None, "selection": None},
        "options": {"task_mode": "ask", "context_mode": "current-note", "prewarm_model": True},
    }
    cfg = {
        "brain_root": brain,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "prepare_cache_ttl": 180,
        "prepare_cache_max": 8,
    }

    def fake_prewarm(_body, _cfg, _unlocks):
        return {
            "status": "started",
            "reason": "queued",
            "model_provider_id": "gateway-default",
            "model_id": "",
            "model_protocol": "claude-wrapper/agent-tools",
        }

    monkeypatch.setattr(gateway, "start_model_prewarm", fake_prewarm)
    status, payload = gateway.process_prepare(body, cfg)

    assert status == 200
    assert payload["prewarm_status"] == "started"
    assert payload["prewarm_reason"] == "queued"
    assert payload["prewarm_model_provider_id"] == "gateway-default"


def test_start_model_prewarm_requires_explicit_option(monkeypatch, tmp_path):
    with gateway._PREWARM_LOCK:
        gateway._PREWARM_STATE.clear()
        gateway._PREWARM_STATE.update(running_key="", items={})

    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            started.append((self.args, self.kwargs))

    monkeypatch.setattr(gateway.threading, "Thread", FakeThread)
    result = gateway.start_model_prewarm(
        {"context": {}, "options": {"task_mode": "ask"}},
        {
            "brain_root": tmp_path,
            "index_pythonpath": "/x",
            "index_db": "/x.sqlite",
            "claude_bin": "claude",
            "claude_permission_mode": "dontAsk",
        },
        [],
    )

    assert result["status"] == "not_requested"
    assert started == []


def test_start_model_prewarm_dedupes_inflight_and_warm(monkeypatch, tmp_path):
    with gateway._PREWARM_LOCK:
        gateway._PREWARM_STATE.clear()
        gateway._PREWARM_STATE.update(running_key="", items={})

    started = []

    class FakeThread:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

        def start(self):
            started.append((self.args, self.kwargs))

    monkeypatch.setattr(gateway.threading, "Thread", FakeThread)
    body = {
        "context": {"active_file": {"path": "note.md", "mtime": 1}, "pdf": {"page": None}},
        "options": {"task_mode": "ask", "context_mode": "current-note", "prewarm_model": True},
    }
    cfg = {
        "brain_root": tmp_path,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "claude_bin": "claude",
        "claude_permission_mode": "dontAsk",
        "claude_max_turns": "8",
        "claude_fast_max_turns": "4",
        "claude_timeout": 110,
        "prewarm_enabled": True,
        "prewarm_timeout": 5,
        "prewarm_max_turns": "1",
        "prewarm_ttl_seconds": 240,
    }

    first = gateway.start_model_prewarm(body, cfg, [])
    second = gateway.start_model_prewarm(body, cfg, [])
    with gateway._PREWARM_LOCK:
        running_key = gateway._PREWARM_STATE["running_key"]
        gateway._PREWARM_STATE["running_key"] = ""
        gateway._PREWARM_STATE["items"][running_key].update(running=False, finished_at=time.time(), status="ok")
    third = gateway.start_model_prewarm(body, cfg, [])

    assert first["status"] == "started"
    assert second == {
        "status": "skipped",
        "reason": "inflight",
        "model_provider_id": "gateway-default",
        "model_id": "",
        "model_protocol": "claude-wrapper/agent-tools",
    }
    assert third["status"] == "skipped"
    assert third["reason"] == "warm"
    assert len(started) == 1


def test_start_model_prewarm_uses_live_idle_process(monkeypatch, tmp_path):
    with gateway._LIVE_PREWARM_LOCK:
        gateway._LIVE_PREWARM_STATE.clear()
        gateway._LIVE_PREWARM_STATE.update(items={})

    started = []
    logs = []

    class FakePopen:
        def __init__(self, cmd, **kwargs):
            self.cmd = cmd
            self.kwargs = kwargs
            self.returncode = None
            self.stdin = io.StringIO()
            self.stdout = iter(())
            self.stderr = io.StringIO()
            started.append((cmd, kwargs))

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def wait(self, timeout=None):
            return 0

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(gateway.subprocess, "Popen", FakePopen)
    monkeypatch.setattr(
        gateway_runner,
        "log_prewarm_event",
        lambda cfg, key, result, dur_ms, model_selection, error_type="": logs.append(result),
    )
    body = {
        "context": {"active_file": {"path": "note.md", "mtime": 1}, "pdf": {"page": None}},
        "options": {"task_mode": "ask", "context_mode": "current-note", "prewarm_model": True},
    }
    cfg = {
        "brain_root": tmp_path,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "claude_bin": "claude",
        "claude_permission_mode": "dontAsk",
        "claude_max_turns": "8",
        "prewarm_enabled": True,
        "live_prewarm_enabled": True,
        "live_prewarm_idle_seconds": 240,
    }
    try:
        result = gateway.start_model_prewarm(body, cfg, [])
    finally:
        with gateway._LIVE_PREWARM_LOCK:
            gateway._LIVE_PREWARM_STATE.clear()
            gateway._LIVE_PREWARM_STATE.update(items={})

    assert result["status"] == "started"
    assert result["reason"] == "live_idle"
    assert logs == ["live_idle"]
    assert len(started) == 1
    cmd = started[0][0]
    assert cmd[:2] == ["claude", "-p"]
    assert "--input-format" in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--replay-user-messages" in cmd
    assert "预热" not in " ".join(cmd)


def test_log_prewarm_event_writes_metadata_only(tmp_path):
    cfg = {
        "log_dir": tmp_path,
        "budget_profile": "prewarm",
        "claude_max_turns": "1",
        "claude_permission_mode": "dontAsk",
    }
    gateway.log_prewarm_event(cfg, "sensitive-location-and-prompt", "ok", 12, None)

    text = (tmp_path / "requests.jsonl").read_text(encoding="utf-8")
    entry = json.loads(text)
    assert entry["endpoint"] == "prewarm"
    assert entry["status"] == 200
    assert entry["prewarm_status"] == "ok"
    assert entry["model_provider_id"] == "gateway-default"
    assert "prompt" not in entry
    assert "sensitive-location-and-prompt" not in text


def test_handle_prepare_contract_success_and_schema_error(tmp_path):
    brain = make_brain(tmp_path)
    cfg = {
        "brain_root": brain,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "prepare_cache_ttl": 180,
        "prepare_cache_max": 8,
    }

    def call_prepare(body):
        handler = object.__new__(gateway.GatewayHandler)
        handler.cfg = cfg
        raw = json.dumps(body).encode("utf-8")
        handler.headers = {"Content-Length": str(len(raw))}
        handler.rfile = io.BytesIO(raw)
        captured = []
        logs = []
        handler._respond = lambda status, payload, raw_text=False: captured.append((status, payload))
        handler._log_prepare = lambda body, status, started, payload: logs.append((status, payload))
        handler._handle_prepare()
        return captured[0], logs

    (status, payload), logs = call_prepare(
        {
            "schema_version": 1,
            "entry": "obsidian",
            "message": "",
            "context": {"active_file": None, "note": None, "selection": None},
            "options": {"task_mode": "ask", "context_mode": "current-note"},
        }
    )
    assert status == 200
    assert payload["ok"] is True
    assert payload["prepare_id"].startswith("prep-")
    assert logs and logs[0][0] == 200

    (status, payload), logs = call_prepare({"schema_version": 999})
    assert status == 400
    assert payload["ok"] is False
    assert not logs


def test_handle_prepare_normalizes_unexpected_errors(monkeypatch, tmp_path):
    brain = make_brain(tmp_path)
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {
        "brain_root": brain,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "prepare_cache_ttl": 180,
        "prepare_cache_max": 8,
    }
    raw = json.dumps({"schema_version": 1, "context": {}, "options": {}}).encode("utf-8")
    handler.headers = {"Content-Length": str(len(raw))}
    handler.rfile = io.BytesIO(raw)
    captured = []
    logs = []
    handler._respond = lambda status, payload, raw_text=False: captured.append((status, payload))
    handler._log_prepare = lambda body, status, started, payload: logs.append((status, payload))

    def boom(_body, _cfg):
        raise RuntimeError("unexpected")

    monkeypatch.setattr(gateway, "process_prepare", boom)
    handler._handle_prepare()

    status, payload = captured[0]
    assert status == 500
    assert payload["ok"] is False
    assert payload["error"] == "prepare failed: RuntimeError"
    assert logs and logs[0][0] == 500


def test_build_prompt_includes_attachments_and_memory_policy():
    body = {
        "schema_version": 1,
        "message": "根据附件说明一下",
        "context": {
            "attachments": [
                {
                    "name": "note.md",
                    "kind": "markdown",
                    "size": 20,
                    "intake_mode": "inbox_candidate",
                    "extracted_text": "附件正文摘要",
                }
            ],
            "memory": {"commands": ["do_not_remember"], "disabled": True},
        },
        "options": {"task_mode": "ask"},
    }
    prompt = gateway.build_prompt(body, [], {"index_pythonpath": "/x", "index_db": "/x.sqlite"})
    assert "note.md" in prompt
    assert "附件正文摘要" in prompt
    assert "不得自动写入长期记忆" in prompt
    assert "不要把本轮对话或附件写入长期记忆" in prompt


def test_build_prompt_full_access_does_not_claim_read_only():
    body = {
        "schema_version": 1,
        "message": "把这个附件入库并生成伴生md",
        "context": {
            "attachments": [
                {
                    "name": "lecture.pdf",
                    "kind": "pdf",
                    "size": 20,
                    "path": "/tmp/lecture.pdf",
                }
            ],
        },
        "options": {"task_mode": "ask"},
    }
    prompt = gateway.build_prompt(
        body,
        [],
        {"index_pythonpath": "/x", "index_db": "/x.sqlite", "gateway_access_mode": "full"},
    )
    assert "full-access模式" in prompt
    assert "不要声称自己处于只读模式" in prompt
    assert "可按用户明确请求用于整理、入库或生成伴生材料" in prompt


def test_materialize_image_attachment_for_model_read(tmp_path):
    cfg = {"log_dir": tmp_path}
    raw = b"\x89PNG\r\n\x1a\nfake"
    body = {
        "context": {
            "attachments": [
                {
                    "name": "screen shot.png",
                    "kind": "image",
                    "mime": "image/png",
                    "size": len(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "content_encoding": "base64",
                    "content_media_type": "image/png",
                }
            ]
        }
    }
    tmp_dir = gateway.materialize_image_attachments(body, cfg)
    try:
        assert tmp_dir is not None and tmp_dir.is_dir()
        attachment = body["context"]["attachments"][0]
        path = Path(attachment["path"])
        assert path.is_file()
        assert path.read_bytes() == raw
        prompt = gateway.build_attachments_section(body["context"]["attachments"])
        assert "可按需使用Read读取" in prompt
        assert str(path) in prompt
    finally:
        if tmp_dir is not None:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


def test_materialize_attachment_invalid_base64_marks_error(tmp_path):
    # 回归:content_base64 损坏时应优雅标记 status=error(走 binascii.Error 分支),
    # 不得抛 NameError/500。覆盖此前缺测、导致 binascii 漏导入未被发现的分支。
    cfg = {"log_dir": tmp_path}
    body = {
        "context": {
            "attachments": [
                {
                    "name": "bad.png",
                    "kind": "image",
                    "content_media_type": "image/png",
                    "content_base64": "!!!!not-valid-base64!!!!",
                }
            ]
        }
    }
    tmp_dir = gateway.materialize_image_attachments(body, cfg)
    assert tmp_dir is None  # 没有可用附件落地
    item = body["context"]["attachments"][0]
    assert item["status"] == "error"
    assert "content_base64 is invalid" in item["error"]


def test_materialize_binary_attachment_for_model_read(tmp_path):
    cfg = {"log_dir": tmp_path}
    raw = b"%PDF-1.4 fake"
    body = {
        "context": {
            "attachments": [
                {
                    "name": "lecture.pdf",
                    "kind": "pdf",
                    "mime": "application/pdf",
                    "size": len(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "content_encoding": "base64",
                    "content_media_type": "application/pdf",
                }
            ]
        }
    }
    tmp_dir = gateway.materialize_image_attachments(body, cfg)
    try:
        assert tmp_dir is not None and tmp_dir.is_dir()
        attachment = body["context"]["attachments"][0]
        path = Path(attachment["path"])
        assert path.is_file()
        assert path.read_bytes() == raw
        prompt = gateway.build_attachments_section(body["context"]["attachments"])
        assert "文件内容：本轮临时文件" in prompt
        assert str(path) in prompt
    finally:
        if tmp_dir is not None:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


def test_archive_attachment_materializes_and_forces_tool_route(tmp_path):
    cfg = {"log_dir": tmp_path}
    raw = b"PK\x03\x04fake"
    body = {
        "context": {
            "attachments": [
                {
                    "name": "course pack.zip",
                    "kind": "archive",
                    "mime": "application/zip",
                    "size": len(raw),
                    "content_base64": base64.b64encode(raw).decode("ascii"),
                    "content_encoding": "base64",
                    "content_media_type": "application/zip",
                }
            ]
        }
    }
    tmp_dir = gateway.materialize_image_attachments(body, cfg)
    try:
        assert tmp_dir is not None and tmp_dir.is_dir()
        attachment = body["context"]["attachments"][0]
        path = Path(attachment["path"])
        assert path.is_file()
        assert path.name.endswith(".zip")
        assert path.read_bytes() == raw
        prompt = gateway.build_attachments_section(body["context"]["attachments"])
        assert "压缩包内容" in prompt
        assert "unzip -l" in prompt
        assert str(path) in prompt
        assert gateway.request_has_archive_attachments(body) is True
        assert gateway.request_requires_agent_tools(
            body,
            [],
            {"provider_id": "moonshot-openai", "capabilities": {"file_extract": True}},
        ) is True
    finally:
        if tmp_dir is not None:
            import shutil

            shutil.rmtree(tmp_dir, ignore_errors=True)


def test_memory_events_from_context_is_candidate_only():
    events = gateway.memory_events_from_context({"memory": {"commands": ["remember"]}})
    assert events["candidate_count"] == 1
    assert events["review_count"] == 1
    assert events["auto_merged_count"] == 0
    disabled = gateway.memory_events_from_context({"memory": {"commands": ["do_not_remember"]}})
    assert disabled["disabled"] is True
    assert disabled["candidate_count"] == 0


def test_approved_memory_injection_refs_only_approved_cards(tmp_path):
    brain = make_brain(tmp_path)
    write_memory_card(brain, "report-style.md", "用户希望报告先给结论，再列验证证据。")
    write_memory_card(
        brain,
        "private.md",
        "用户手机号是123。",
        sensitivity="sensitive",
        unlock_hints="[手机号]",
    )
    write_memory_card(
        brain,
        "expired.md",
        "用户期末周临时只看选择题。",
        layer="situational",
        expires="2026-01-01",
        unlock_hints="[期末]",
    )
    body = {
        "schema_version": 1,
        "message": "这份报告怎么组织结论和验证？",
        "context": {},
        "options": {"task_mode": "ask"},
    }
    cfg = {
        "brain_root": brain,
        "memory_root": brain / "memory",
        "memory_injection_enabled": True,
        "memory_injection_max_cards": 3,
        "memory_injection_max_chars": 500,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
    }
    prompt = gateway.build_prompt(body, [], cfg)
    events = gateway.memory_events_for_request(body, cfg)

    assert "关于用户的已批准记忆" in prompt
    assert "[report-style.md]" in prompt
    assert "private.md" not in prompt
    assert "expired.md" not in prompt
    assert events["referenced_count"] == 1
    assert events["referenced_cards"] == ["report-style.md"]


def test_approved_memory_injection_switch_off_preserves_prompt(tmp_path):
    brain = make_brain(tmp_path)
    write_memory_card(brain, "report-style.md", "用户希望报告先给结论，再列验证证据。")
    body = {
        "schema_version": 1,
        "message": "报告结论怎么写？",
        "context": {},
        "options": {"task_mode": "ask"},
    }
    cfg = {
        "brain_root": brain,
        "memory_root": brain / "memory",
        "memory_injection_enabled": False,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
    }
    prompt = gateway.build_prompt(body, [], cfg)
    assert "关于用户的已批准记忆" not in prompt
    assert gateway.memory_events_for_request(body, cfg) is None


def test_approved_memory_injection_budget_truncates(tmp_path):
    brain = make_brain(tmp_path)
    write_memory_card(
        brain,
        "long.md",
        "用户希望报告先给结论，再列验证证据。" + "详细说明" * 80,
        body="补充边界" * 80,
    )
    body = {"schema_version": 1, "message": "报告验证证据", "context": {}, "options": {}}
    cfg = {
        "brain_root": brain,
        "memory_root": brain / "memory",
        "memory_injection_enabled": True,
        "memory_injection_max_cards": 1,
        "memory_injection_max_chars": 80,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
    }
    section, events = gateway.approved_memory_injection(body, cfg)
    assert section is not None
    assert "..." in section
    assert len(section.splitlines()[1]) <= 80
    assert events["referenced_cards"] == ["long.md"]


def test_memory_events_merge_candidate_and_referenced(tmp_path):
    brain = make_brain(tmp_path)
    write_memory_card(brain, "report-style.md", "用户希望报告先给结论，再列验证证据。")
    body = {
        "schema_version": 1,
        "message": "请记住，也帮我整理报告结论。",
        "context": {"memory": {"commands": ["remember"]}},
        "options": {},
    }
    cfg = {
        "brain_root": brain,
        "memory_root": brain / "memory",
        "memory_injection_enabled": True,
        "memory_injection_max_cards": 2,
        "memory_injection_max_chars": 500,
    }
    events = gateway.memory_events_for_request(body, cfg)
    assert events["candidate_count"] == 1
    assert events["review_count"] == 1
    assert events["referenced_count"] == 1
    assert "report-style.md" in events["summary"]


def test_memory_access_log_records_referenced_ids(tmp_path):
    handler = _handler_with_cfg(tmp_path)
    handler.cfg["memory_access_log_enabled"] = True
    body = {"entry": "obsidian", "message": "hi", "context": {}, "options": {}}
    payload = {
        "answer": "ok",
        "sources": [],
        "memory_events": {"referenced_cards": ["report-style.md"], "referenced_count": 1},
    }
    handler._log_request(body, 200, time.time(), payload)
    access = tmp_path / "memory-access.jsonl"
    _wait_for_nonempty(access)
    rec = json.loads(access.read_text(encoding="utf-8").splitlines()[0])
    assert rec["referenced_cards"] == ["report-style.md"]


def test_iter_stream_events_partial_shape():
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_start","content_block":{"type":"tool_use","name":"Read"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"电子热容"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"正比于T。"}}}',
        '{"type":"result","result":"电子热容正比于T。"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    kinds = [e[0] for e in events]
    assert kinds == ["status", "delta", "delta", "final"]
    assert events[-1][1] == "电子热容正比于T。"


def test_iter_stream_events_exposes_tool_status_detail():
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {
                            "type": "tool_use",
                            "name": "WebSearch",
                            "input": {"query": "中科大近期热点"},
                        }
                    ]
                },
            }
        ),
        '{"type":"assistant","message":{"content":[{"type":"text","text":"找到公开来源。"}]}}',
        '{"type":"result","result":"找到公开来源。"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert events[0] == ("status", "正在搜索网页：中科大近期热点…")
    assert events[-1] == ("final", "找到公开来源。")


def test_iter_stream_events_exposes_streamed_tool_input_detail():
    lines = [
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_start",
                    "index": 1,
                    "content_block": {"type": "tool_use", "name": "Bash"},
                },
            }
        ),
        json.dumps(
            {
                "type": "stream_event",
                "event": {
                    "type": "content_block_delta",
                    "index": 1,
                    "delta": {
                        "type": "input_json_delta",
                        "partial_json": '{"command":"rtime-web-fetch search \\"中科大近期热点\\" --limit 2"}',
                    },
                },
            }
        ),
        '{"type":"stream_event","event":{"type":"content_block_stop","index":1}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"找到公开来源。"}}}',
        '{"type":"result","result":"找到公开来源。"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert ("status", '正在搜索网页：rtime-web-fetch search "中科大近期热点" --limit 2…') in events
    assert events[-1] == ("final", "找到公开来源。")


def test_iter_stream_events_exposes_thinking_status_once():
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_start","content_block":{"type":"thinking"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"thinking_delta","thinking":"..."}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"答案"}}}',
        '{"type":"result","result":"答案"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert events.count(("status", "思考中…")) == 1
    assert ("delta", "答案") in events


def test_iter_stream_events_forwards_permission_request():
    lines = [
        json.dumps(
            {
                "type": "permission_request",
                "tool_name": "Bash",
                "command": "find . -maxdepth 2 -type f",
                "message": "Allow this command?",
            }
        ),
        '{"type":"result","result":"需要批准后继续。"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert events[0][0] == "approval_request"
    assert "Bash" in events[0][1]
    assert "find . -maxdepth 2 -type f" in events[0][1]
    assert events[-1] == ("final", "需要批准后继续。")


def test_iter_stream_events_whole_message_fallback():
    lines = [
        '{"type":"assistant","message":{"content":[{"type":"text","text":"完整回答。"}]}}',
        '{"type":"result","result":null}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert ("delta", "完整回答。") in events
    assert events[-1] == ("final", "完整回答。")


def test_iter_stream_events_raises_on_error_result():
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"先扫描PDF。"}}}',
        '{"type":"result","subtype":"error_max_turns","is_error":true,"result":"先扫描PDF。"}',
    ]
    with pytest.raises(gateway.ClaudeStreamIncomplete) as exc:
        list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert "error_max_turns" in str(exc.value)
    assert exc.value.partial == "先扫描PDF。"


def test_iter_stream_events_keeps_answer_on_success_subtype_with_is_error(capsys):
    # Regression: the Claude CLI sometimes sets is_error=true on a successful run
    # (transient brain-MCP / hook error mid-stream) while subtype stays "success".
    # That must NOT be treated as fatal — the complete answer is kept, not turned
    # into the nonsensical "模型流以非成功状态结束：success".
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"大创经费一般为"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"国家级1万元。"}}}',
        '{"type":"result","subtype":"success","is_error":true,"result":"大创经费一般为国家级1万元。"}',
    ]
    events = list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert events[-1] == ("final", "大创经费一般为国家级1万元。")
    # The anomaly is recorded to stderr so it stays diagnosable.
    assert "is_error=true on non-error subtype" in capsys.readouterr().err


def test_iter_stream_events_raises_when_is_error_without_subtype():
    # is_error with no subtype at all is still a genuine terminal failure.
    lines = [
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"先看一下。"}}}',
        '{"type":"result","is_error":true,"result":"先看一下。"}',
    ]
    with pytest.raises(gateway.ClaudeStreamIncomplete) as exc:
        list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert "模型流以非成功状态结束：error" in str(exc.value)


def test_iter_stream_events_rejects_tool_prelude_without_final_answer():
    lines = [
        json.dumps(
            {
                "type": "assistant",
                "message": {
                    "content": [
                        {"type": "text", "text": "继续查找课件中的重复讲稿。先扫描目录。"},
                        {"type": "tool_use", "name": "Glob"},
                    ]
                },
            },
            ensure_ascii=False,
        ),
        json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "result": "继续查找课件中的重复讲稿。先扫描目录。",
            },
            ensure_ascii=False,
        ),
    ]
    with pytest.raises(gateway.ClaudeStreamIncomplete) as exc:
        list(gateway.iter_stream_events(iter(lines), deadline=9e12))
    assert "中间计划" in str(exc.value)


def test_run_claude_stream_records_trace(monkeypatch, tmp_path):
    class FakeStderr:
        def read(self):
            return ""

    class FakeProc:
        def __init__(self):
            self.stdout = iter([
                '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"边"}}}',
                '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"输出"}}}',
                '{"type":"result","result":"边输出"}',
            ])
            self.stderr = FakeStderr()
            self.returncode = 0

        def wait(self, timeout=None):
            self.returncode = 0
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    monkeypatch.setattr(gateway.subprocess, "Popen", lambda *a, **k: FakeProc())
    trace = {}
    events = []
    answer = gateway.run_claude_stream(
        "prompt",
        {
            "claude_bin": "claude",
            "brain_root": tmp_path,
            "claude_max_turns": "1",
            "claude_timeout": 5,
            "index_pythonpath": "/x",
            "index_db": "/x.sqlite",
        },
        lambda etype, data: events.append((etype, data)),
        trace=trace,
    )
    assert answer == "边输出"
    assert events == [("delta", "边"), ("delta", "输出")]
    assert trace["claude_spawned"] <= trace["first_stdout_event"] <= trace["process_exit"]


def test_iter_stream_events_stops_after_result_for_live_process():
    consumed_after_result = False

    def lines():
        nonlocal consumed_after_result
        yield '{"type":"result","subtype":"success","result":"好了"}'
        consumed_after_result = True
        yield '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"不该读取"}}}'

    assert list(gateway.iter_stream_events(lines(), deadline=9e12)) == [("final", "好了")]
    assert consumed_after_result is False


def test_claude_live_cmd_and_input_contract(tmp_path):
    cfg = {
        "claude_bin": "claude",
        "claude_max_turns": "8",
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "claude_permission_mode": "default",
    }
    cmd = gateway._claude_live_cmd(
        cfg,
        {"protocol": "claude-wrapper/agent-tools", "cli_model": "kimi-code"},
    )
    assert cmd[:2] == ["claude", "-p"]
    assert "--input-format" in cmd
    assert cmd[cmd.index("--input-format") + 1] == "stream-json"
    assert cmd[cmd.index("--output-format") + 1] == "stream-json"
    assert cmd[cmd.index("--permission-prompt-tool") + 1] == "stdio"
    assert "--replay-user-messages" in cmd
    assert cmd[cmd.index("--model") + 1] == "kimi-code"

    line = gateway._claude_live_input("真实请求")
    payload = json.loads(line)
    assert payload["type"] == "user"
    assert payload["session_id"] == ""
    assert payload["parent_tool_use_id"] is None
    assert payload["message"] == {"role": "user", "content": "真实请求"}


def test_run_claude_stream_uses_live_prewarm_stdin_once(monkeypatch, tmp_path):
    class FakeStderr:
        def read(self):
            return ""

    class FakeStdin:
        def __init__(self):
            self.writes = []
            self.flushed = False

        def write(self, value):
            self.writes.append(value)

        def flush(self):
            self.flushed = True

    class FakeLiveProc:
        def __init__(self):
            self.stdin = FakeStdin()
            self.stdout = iter([
                '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"好"}}}',
                '{"type":"result","subtype":"success","result":"好的"}',
            ])
            self.stderr = FakeStderr()
            self.returncode = None

        def wait(self, timeout=None):
            if self.returncode is None:
                raise AssertionError("live process should not wait for natural EOF")
            return 0

        def poll(self):
            return self.returncode

        def terminate(self):
            self.returncode = -15

        def kill(self):
            self.returncode = -9

    proc = FakeLiveProc()
    replenished = []
    monkeypatch.setattr(
        gateway_runner,
        "claim_live_prewarm_process",
        lambda cfg, model_selection: {"proc": proc, "created_at": time.time() - 2},
    )
    monkeypatch.setattr(
        gateway_runner,
        "replenish_live_prewarm_process",
        lambda cfg, model_selection: replenished.append((cfg, model_selection)),
    )

    trace = {}
    events = []
    answer = gateway.run_claude_stream(
        "真实请求",
        {
            "claude_bin": "claude",
            "brain_root": tmp_path,
            "claude_max_turns": "1",
            "claude_timeout": 5,
            "index_pythonpath": "/x",
            "index_db": "/x.sqlite",
            "live_prewarm_enabled": True,
        },
        lambda etype, data: events.append((etype, data)),
        trace=trace,
    )

    assert answer == "好的"
    assert events == [("delta", "好")]
    assert json.loads(proc.stdin.writes[0])["message"]["content"] == "真实请求"
    assert proc.stdin.flushed is True
    assert proc.returncode == -15
    assert len(replenished) == 1
    assert trace["live_prewarm_claimed"] == trace["claude_spawned"]
    assert trace["live_prewarm_age_ms"] >= 1900


def test_static_model_catalog_has_safe_defaults(tmp_path):
    cfg = {
        "moonshot_base_url": "https://api.moonshot.ai/v1",
        "ustc_base_url": "https://api.llm.ustc.edu.cn/v1",
    }
    catalog = gateway.static_model_catalog(cfg)
    providers = {item["id"]: item for item in catalog["providers"]}
    assert "moonshot-openai" in providers
    assert "kimi-k2.7-code" in [m["id"] for m in providers["moonshot-openai"]["models"]]
    text = json.dumps(catalog, ensure_ascii=False)
    assert "API_KEY" not in text
    assert "keyfile" not in text.lower()


def test_static_catalog_is_faithful_projection_of_registry(monkeypatch):
    """P3 anchor: the catalog the gateway serves to Obsidian is exactly the
    registry's catalog providers/models/capabilities (no env override applied)."""
    import rtime_models

    for var in ("RTIME_KIMI_OPENAI_MODELS", "RTIME_USTC_MODELS"):
        monkeypatch.delenv(var, raising=False)
    cfg = {
        "moonshot_base_url": rtime_models.base_url("moonshot-openai"),
        "ustc_base_url": rtime_models.base_url("ustc-openai"),
    }
    catalog = gateway.static_model_catalog(cfg)

    assert [p["id"] for p in catalog["providers"]] == [
        p["id"] for p in rtime_models.catalog_providers()
    ]
    served_by_id = {p["id"]: p for p in catalog["providers"]}
    for prov in rtime_models.catalog_providers():
        served = served_by_id[prov["id"]]
        assert served["label"] == prov["label"]
        assert served["protocol"] == prov["protocol"]
        if prov.get("base_url_cfg_key"):
            assert served["base_url_label"] == prov["base_url"]
        else:
            assert "base_url_label" not in served
        expected_ids = rtime_models.catalog_model_ids(prov["id"]) or [
            m["id"] for m in prov["models"]
        ]
        assert [m["id"] for m in served["models"]] == expected_ids
        for served_model in served["models"]:
            reg_model = rtime_models.model(prov["id"], served_model["id"])
            assert reg_model is not None, f"{prov['id']}/{served_model['id']} not in registry"
            assert served_model["capabilities"] == reg_model["capabilities"]


def test_static_catalog_matches_golden_snapshot(monkeypatch):
    """Pin the served catalog (capabilities included) to the known-good pre-P3
    values. The faithful-projection test above is tautological for capabilities
    (registry==projection); this golden catches a *registry capability edit* that
    would silently change what the gateway serves. Regenerate intentionally from
    static_model_catalog with default env when a model legitimately changes."""
    import json as _json
    from pathlib import Path as _Path

    import rtime_models

    for var in ("RTIME_KIMI_OPENAI_MODELS", "RTIME_USTC_MODELS"):
        monkeypatch.delenv(var, raising=False)
    cfg = {
        "moonshot_base_url": rtime_models.base_url("moonshot-openai"),
        "ustc_base_url": rtime_models.base_url("ustc-openai"),
    }
    catalog = gateway.static_model_catalog(cfg)
    catalog.pop("generated_at", None)
    golden_path = _Path(__file__).resolve().parents[1] / "tests" / "golden" / "model_catalog.json"
    golden = _json.loads(golden_path.read_text(encoding="utf-8"))
    assert catalog == golden, (
        "served catalog drifted from tests/golden/model_catalog.json; if intentional, "
        "regenerate the golden from static_model_catalog with default env"
    )


def test_resolve_model_selection_accepts_catalog_and_rejects_unknown(tmp_path):
    cfg = {
        "model_catalog_path": tmp_path / "missing.json",
        "moonshot_base_url": "https://api.moonshot.ai/v1",
        "ustc_base_url": "https://api.llm.ustc.edu.cn/v1",
    }
    body = {
        "options": {
            "model_provider_id": "kimi-code-wrapper",
            "model_id": "kimi-code",
            "model_protocol": "claude-wrapper/agent-tools",
        }
    }
    selected, warning = gateway.resolve_model_selection(body, cfg)
    assert warning is None
    assert selected["cli_model"] == "kimi-code"
    bad = {"options": {"model_provider_id": "kimi-code-wrapper", "model_id": "bad", "model_protocol": "openai-chat"}}
    selected, warning = gateway.resolve_model_selection(bad, cfg)
    assert selected is None
    assert "已回退默认模型" in warning


def test_claude_cmd_adds_only_catalog_model(tmp_path):
    cfg = {
        "claude_bin": "claude",
        "claude_max_turns": "8",
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "claude_permission_mode": "default",
    }
    cmd = gateway._claude_cmd(
        "prompt",
        cfg,
        {"protocol": "claude-wrapper/agent-tools", "cli_model": "kimi-code"},
    )
    assert cmd[cmd.index("--permission-mode") + 1] == "default"
    assert "--bare" in cmd
    assert "--no-session-persistence" in cmd
    assert "--exclude-dynamic-system-prompt-sections" in cmd
    assert cmd[-2:] == ["--model", "kimi-code"]
    assert ";" not in " ".join(cmd)


def test_claude_cmd_avoids_bare_when_mcp_tools_are_configured():
    cfg = {
        "claude_bin": "claude",
        "claude_max_turns": "8",
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "claude_permission_mode": "default",
        "extra_allowed_tools": "mcp__browser__*",
    }
    cmd = gateway._claude_cmd("prompt", cfg)
    assert "--bare" not in cmd
    assert "--no-session-persistence" in cmd


def test_cfg_for_request_threads_permission_options():
    cfg = {
        "claude_permission_mode": "dontAsk",
        "approval_forwarding_enabled": True,
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_timeout": 110,
    }
    request_cfg = gateway.cfg_for_request(
        {
            "options": {
                "task_mode": "ask",
                "permission_mode": "plan",
                "approval_forwarding": False,
            }
        },
        cfg,
    )
    assert request_cfg["claude_permission_mode"] == "plan"
    assert request_cfg["approval_forwarding_enabled"] is False


def test_cfg_for_request_full_access_forces_bypass_permissions():
    cfg = {
        "claude_permission_mode": "dontAsk",
        "gateway_access_mode": "full",
        "claude_fast_max_turns": "3",
        "claude_max_turns": "8",
    }
    request_cfg = gateway.cfg_for_request(
        {"options": {"task_mode": "ask", "permission_mode": "plan"}},
        cfg,
    )
    assert request_cfg["claude_permission_mode"] == "bypassPermissions"


def test_cfg_for_request_rejects_unknown_permission_mode():
    cfg = {
        "claude_permission_mode": "dontAsk",
        "approval_forwarding_enabled": True,
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_timeout": 110,
    }
    request_cfg = gateway.cfg_for_request(
        {"options": {"task_mode": "ask", "permission_mode": "root"}},
        cfg,
    )
    assert request_cfg["claude_permission_mode"] == "dontAsk"


def test_refresh_model_catalog_uses_mock_openai_models(monkeypatch, tmp_path):
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-token")

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({
                "data": [
                    {
                        "id": "kimi-k2.7-code",
                        "owned_by": "moonshot",
                        "context_length": 256000,
                        "supports_image_in": True,
                        "supports_video_in": True,
                        "supports_reasoning": True,
                    }
                ]
            }).encode()

    monkeypatch.setattr(gateway.urllib.request, "urlopen", lambda *a, **k: FakeResponse())
    cfg = {
        "moonshot_base_url": "https://api.moonshot.ai/v1",
        "ustc_base_url": "https://api.llm.ustc.edu.cn/v1",
        "model_refresh_timeout": 1,
        "model_catalog_path": tmp_path / "catalog.json",
    }
    catalog = gateway.refresh_model_catalog(cfg)
    moonshot = next(p for p in catalog["providers"] if p["id"] == "moonshot-openai")
    assert moonshot["models"][0]["id"] == "kimi-k2.7-code"
    assert moonshot["models"][0]["capabilities"]["thinking"] == "required"
    assert moonshot["models"][0]["capabilities"]["file_extract"] is True
    assert (tmp_path / "catalog.json").is_file()
    assert "secret-token" not in (tmp_path / "catalog.json").read_text(encoding="utf-8")


def test_run_openai_chat_uses_provider_key_without_logging(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-token")
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout=None):
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = request.full_url
        return FakeResponse()

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    text = gateway.run_openai_chat(
        "prompt",
        {"claude_timeout": 10},
        {"provider_id": "moonshot-openai", "model_id": "kimi-k2.7-code", "base_url": "https://api.moonshot.ai/v1"},
    )
    assert text == "ok"
    assert seen["auth"] == "Bearer secret-token"
    assert seen["url"].endswith("/chat/completions")


def test_run_openai_chat_sends_image_content_blocks_for_vision_model(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-token")
    seen = {}

    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return b'{"choices":[{"message":{"content":"ok"}}]}'

    def fake_urlopen(request, timeout=None):
        seen["payload"] = json.loads(request.data.decode("utf-8"))
        return FakeResponse()

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    text = gateway.run_openai_chat(
        "prompt",
        {"claude_timeout": 10},
        {
            "provider_id": "moonshot-openai",
            "model_id": "kimi-k2.7-code",
            "base_url": "https://api.moonshot.ai/v1",
            "capabilities": {"vision": True},
        },
        [
            {
                "name": "screen.png",
                "kind": "image",
                "mime": "image/png",
                "content_base64": base64.b64encode(b"png").decode("ascii"),
                "content_media_type": "image/png",
            }
        ],
    )
    assert text == "ok"
    content = seen["payload"]["messages"][1]["content"]
    assert content[0] == {"type": "text", "text": "prompt"}
    assert content[1]["type"] == "image_url"
    assert content[1]["image_url"]["url"].startswith("data:image/png;base64,")


def test_run_openai_chat_uses_moonshot_file_extract_for_pdf(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-token")
    seen = {"uploads": 0}

    class FakeResponse:
        def __init__(self, data: bytes):
            self._data = data

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return self._data

    def fake_urlopen(request, timeout=None):
        url = request.full_url
        if url.endswith("/files"):
            seen["uploads"] += 1
            body = request.data
            assert b'name="purpose"' in body
            assert b"file-extract" in body
            assert b"%PDF" in body
            return FakeResponse(b'{"id":"file_pdf_1","object":"file"}')
        if url.endswith("/files/file_pdf_1/content"):
            return FakeResponse("第一页讲自由电子论。".encode("utf-8"))
        if url.endswith("/chat/completions"):
            seen["payload"] = json.loads(request.data.decode("utf-8"))
            return FakeResponse(b'{"choices":[{"message":{"content":"ok"}}]}')
        raise AssertionError(url)

    monkeypatch.setattr(gateway.urllib.request, "urlopen", fake_urlopen)
    text = gateway.run_openai_chat(
        "请总结附件",
        {"claude_timeout": 10, "file_extract_max_files": 4, "file_extract_max_chars": 80000},
        {
            "provider_id": "moonshot-openai",
            "model_id": "kimi-k2.6",
            "base_url": "https://api.moonshot.ai/v1",
            "capabilities": {"vision": True, "file_extract": True},
        },
        [
            {
                "name": "lecture.pdf",
                "kind": "pdf",
                "mime": "application/pdf",
                "content_base64": base64.b64encode(b"%PDF").decode("ascii"),
                "content_media_type": "application/pdf",
            }
        ],
    )
    assert text == "ok"
    assert seen["uploads"] == 1
    messages = seen["payload"]["messages"]
    assert any("lecture.pdf" in item["content"] and "自由电子论" in item["content"] for item in messages if item["role"] == "system")
    assert messages[-1]["content"] == "请总结附件"


def test_ustc_key_uses_default_runtime_key_file(monkeypatch, tmp_path):
    monkeypatch.delenv("RTIME_USTC_API_KEY", raising=False)
    monkeypatch.delenv("RTIME_USTC_API_KEY_FILE", raising=False)
    key_path = tmp_path / "ustc-api-key"
    key_path.write_text("ustc-secret\n", encoding="utf-8")
    assert gateway._openai_secret_for_provider(
        "ustc-openai",
        {"ustc_api_key_file": key_path},
    ) == "ustc-secret"


def test_chat_only_model_falls_back_for_tool_requests(tmp_path):
    body = {
        "message": "帮我扫描课程目录，找重复讲稿",
        "options": {"task_mode": "ask"},
    }
    selected, warning = gateway.enforce_agent_tool_model(
        body,
        [("原件", tmp_path / "lesson.pdf")],
        {
            "provider_id": "ustc-openai",
            "model_id": "deepseek-v4-flash-ascend",
            "protocol": "openai-chat",
        },
        None,
    )
    assert selected is None
    assert "chat-only" in warning
    assert "已回退默认工具模型" in warning


def test_non_vision_chat_model_falls_back_for_image_request():
    body = {
        "message": "看这张图",
        "context": {"attachments": [{"name": "screen.png", "kind": "image", "content_base64": "cG5n"}]},
        "options": {"task_mode": "ask"},
    }
    selected, warning = gateway.enforce_agent_tool_model(
        body,
        [],
        {
            "provider_id": "ustc-openai",
            "model_id": "deepseek-v4-flash-ascend",
            "protocol": "openai-chat",
            "capabilities": {"vision": False},
        },
        None,
    )
    assert selected is None
    assert "chat-only" in warning

    vision_selected, vision_warning = gateway.enforce_agent_tool_model(
        body,
        [],
        {
            "provider_id": "moonshot-openai",
            "model_id": "kimi-k2.7-code",
            "protocol": "openai-chat",
            "capabilities": {"vision": True},
        },
        None,
    )
    assert vision_selected is not None
    assert vision_warning is None


def test_ustc_chat_model_falls_back_for_pdf_attachment():
    body = {
        "message": "看这个PDF附件",
        "context": {"attachments": [{"name": "lecture.pdf", "kind": "pdf", "content_base64": "JVBERg=="}]},
        "options": {"task_mode": "ask"},
    }
    selected, warning = gateway.enforce_agent_tool_model(
        body,
        [],
        {
            "provider_id": "ustc-openai",
            "model_id": "deepseek-v4-flash-ascend",
            "protocol": "openai-chat",
            "capabilities": {"vision": False, "file_extract": False},
        },
        None,
    )
    assert selected is None
    assert "chat-only" in warning

    kimi_selected, kimi_warning = gateway.enforce_agent_tool_model(
        body,
        [],
        {
            "provider_id": "moonshot-openai",
            "model_id": "kimi-k2.6",
            "protocol": "openai-chat",
            "capabilities": {"vision": True, "file_extract": True},
        },
        None,
    )
    assert kimi_selected is not None
    assert kimi_warning is None


def test_stream_claude_emits_initial_status_and_trace(monkeypatch):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {"stream_trace_enabled": True}
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None

    def fake_stream(prompt, cfg, emit, on_spawn=None, trace=None, model_selection=None):
        if trace is not None:
            trace["claude_spawned"] = time.time()
            trace["first_stdout_event"] = time.time()
        emit("delta", "OK")
        if trace is not None:
            trace["process_exit"] = time.time()
        return "OK"

    monkeypatch.setattr(gateway, "run_claude_stream", fake_stream)
    trace = {"request_received": time.time()}
    payload, status = handler._stream_claude("prompt", trace=trace)

    raw = handler.wfile.getvalue().decode("utf-8")
    assert status == 200
    assert payload["answer"] == "OK"
    assert raw.index('"type": "status"') < raw.index('"type": "delta"')
    assert "已接收请求" in raw
    assert "done_emit" in payload["trace"]


def test_stream_openai_chat_yields_incremental_pieces(monkeypatch):
    monkeypatch.setenv("MOONSHOT_API_KEY", "secret-token")
    lines = [
        b'data: {"choices":[{"delta":{"content":"He"}}]}\n',
        b"\n",
        b'data: {"choices":[{"delta":{"content":"llo"}}]}\n',
        b"data: [DONE]\n",
    ]

    class FakeSSE:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def __iter__(self):
            return iter(lines)

    monkeypatch.setattr(gateway.urllib.request, "urlopen", lambda *a, **k: FakeSSE())
    pieces = list(
        gateway.stream_openai_chat(
            "prompt",
            {"claude_timeout": 10},
            {"provider_id": "moonshot-openai", "model_id": "kimi-k2.7-code", "base_url": "https://api.moonshot.ai/v1"},
        )
    )
    assert pieces == ["He", "llo"]


def test_stream_openai_chat_handler_emits_incremental_deltas(monkeypatch):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {"stream_trace_enabled": True}
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None

    monkeypatch.setattr(gateway, "stream_openai_chat", lambda *a, **k: iter(["foo", "bar"]))
    trace = {"request_received": time.time()}
    payload, status = handler._stream_openai_chat(
        "prompt",
        None,
        trace,
        {"provider_id": "moonshot-openai", "model_id": "m", "base_url": "https://x/v1", "protocol": "openai-chat"},
    )

    raw = handler.wfile.getvalue().decode("utf-8")
    assert status == 200
    assert payload["answer"] == "foobar"
    # each chunk is its own delta event — first token shows without waiting for the whole answer.
    assert raw.count('"type": "delta"') == 2
    assert "first_sse_emit" in trace


def test_stream_openai_chat_handler_falls_back_when_stream_fails(monkeypatch):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {}
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None

    def boom(*args, **kwargs):
        raise RuntimeError("no SSE from provider")

    monkeypatch.setattr(gateway, "stream_openai_chat", boom)
    monkeypatch.setattr(gateway, "run_openai_chat", lambda *a, **k: "blocking-answer")
    payload, status = handler._stream_openai_chat(
        "prompt",
        None,
        None,
        {"provider_id": "moonshot-openai", "model_id": "m", "base_url": "https://x/v1"},
    )

    raw = handler.wfile.getvalue().decode("utf-8")
    assert status == 200
    assert payload["answer"] == "blocking-answer"
    assert raw.count('"type": "delta"') == 1


def test_stream_openai_chat_handler_error_frame_on_total_failure(monkeypatch):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {}
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None

    def boom(*args, **kwargs):
        raise RuntimeError("provider down")

    monkeypatch.setattr(gateway, "stream_openai_chat", boom)
    monkeypatch.setattr(gateway, "run_openai_chat", boom)  # fallback fails too
    payload, status = handler._stream_openai_chat(
        "p", None, None, {"provider_id": "x", "model_id": "m", "base_url": "https://x/v1"}
    )
    frames = parse_sse_frames(handler.wfile.getvalue().decode("utf-8"))
    assert status == 500
    assert any(f.get("type") == "error" for f in frames)


def test_stream_claude_sse_frames_decode_in_order(monkeypatch):
    # Robust companion to the index-based test: assert decoded frame order/content.
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {"stream_trace_enabled": True}
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None

    def fake_stream(prompt, cfg, emit, on_spawn=None, trace=None, model_selection=None):
        emit("status", "正在启动模型…")
        emit("delta", "Hel")
        emit("delta", "lo")
        return "Hello"

    monkeypatch.setattr(gateway, "run_claude_stream", fake_stream)
    payload, status = handler._stream_claude("prompt", trace={"request_received": time.time()})
    frames = parse_sse_frames(handler.wfile.getvalue().decode("utf-8"))
    types = [f["type"] for f in frames]
    assert status == 200
    assert types[0] == "status" and types[-1] == "done" and "delta" in types
    assert payload["answer"] == "Hello"
    assert [f for f in frames if f["type"] == "done"][0]["answer"] == "Hello"


# --- FIN watcher: client-disconnect kills the right child, spares later ones ---


class _FakeConn:
    def __init__(self, mode):  # "fin" -> empty recv; "rst" -> OSError
        self.mode = mode
        self.calls = 0

    def recv(self, _n):
        self.calls += 1
        if self.mode == "rst":
            raise OSError("connection reset")
        return b""  # FIN


class _FakeChild:
    def __init__(self, alive=True):
        self._alive = alive
        self.killed = False

    def poll(self):
        return None if self._alive else 0

    def kill(self):
        self.killed = True


def _disconnect_handler(conn):
    handler = object.__new__(gateway.GatewayHandler)
    handler.connection = conn
    return handler


def test_watch_client_disconnect_kills_child_on_fin():
    child = _FakeChild(alive=True)
    gone = threading.Event()
    _disconnect_handler(_FakeConn("fin"))._watch_client_disconnect(gone, {"proc": child, "finished": False})
    assert gone.is_set() and child.killed is True


def test_watch_client_disconnect_kills_child_on_rst():
    child = _FakeChild(alive=True)
    gone = threading.Event()
    _disconnect_handler(_FakeConn("rst"))._watch_client_disconnect(gone, {"proc": child, "finished": False})
    assert gone.is_set() and child.killed is True


def test_watch_client_disconnect_finished_guard_spares_later_child():
    # Request already over: the watcher must NOT kill what is now a later request's child.
    child = _FakeChild(alive=True)
    gone = threading.Event()
    _disconnect_handler(_FakeConn("fin"))._watch_client_disconnect(gone, {"proc": child, "finished": True})
    assert gone.is_set() and child.killed is False


def test_watch_client_disconnect_skips_already_exited_child():
    child = _FakeChild(alive=False)
    gone = threading.Event()
    _disconnect_handler(_FakeConn("fin"))._watch_client_disconnect(gone, {"proc": child, "finished": False})
    assert gone.is_set() and child.killed is False


def test_iter_stream_events_raises_timeout_past_deadline():
    lines = iter([
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"hi"}}}',
        '{"type":"assistant","message":{}}',
    ])
    with pytest.raises(subprocess.TimeoutExpired):
        list(gateway.iter_stream_events(lines, deadline=0.0))


# --- version stamp: prove which code is actually running ---


def _get_handler(path):
    handler = object.__new__(gateway.GatewayHandler)
    handler.cfg = {}
    handler.path = path
    handler.wfile = io.BytesIO()
    handler.send_response = lambda status: None
    handler.send_header = lambda key, value: None
    handler.end_headers = lambda: None
    return handler


def test_do_get_version_reports_revision():
    handler = _get_handler("/version")
    handler.do_GET()
    body = json.loads(handler.wfile.getvalue().decode("utf-8"))
    assert body["revision"] == gateway.GATEWAY_REVISION
    assert body["server_version"] == handler.server_version
    assert body["started_at"] == gateway.GATEWAY_STARTED_AT


def test_do_get_healthz_stamps_revision():
    handler = _get_handler("/healthz")
    handler.do_GET()
    text = handler.wfile.getvalue().decode("utf-8")
    assert text.startswith("ok")
    assert gateway.GATEWAY_REVISION in text


def test_log_request_records_gateway_revision(tmp_path):
    handler = _handler_with_cfg(tmp_path)
    handler._log_request(
        {"entry": "obsidian", "message": "hi", "context": {}, "options": {"task_mode": "ask"}},
        200,
        time.time(),
        {"answer": "ok", "sources": []},
    )
    entry = json.loads((tmp_path / "requests.jsonl").read_text(encoding="utf-8").strip().splitlines()[-1])
    assert entry["rev"] == gateway.GATEWAY_REVISION


def test_approved_memory_cards_cache_hits_and_invalidates(tmp_path, monkeypatch):
    write_memory_card(tmp_path, "pref.md", "我偏好先看图")
    cfg = {"memory_injection_enabled": True, "brain_root": tmp_path}
    gateway._MEMORY_CARDS_CACHE["entry"] = None  # isolate from other tests

    calls = {"n": 0}
    real_parse = memory._parse_memory_frontmatter

    def counting_parse(text):
        calls["n"] += 1
        return real_parse(text)

    # _approved_memory_cards 现居 memory 模块,内部按 memory 的名字查找 _parse_memory_frontmatter
    monkeypatch.setattr(memory, "_parse_memory_frontmatter", counting_parse)

    first = gateway._approved_memory_cards(cfg)
    parsed_after_first = calls["n"]
    assert parsed_after_first >= 1
    assert any(card["claim"] == "我偏好先看图" for card in first)

    second = gateway._approved_memory_cards(cfg)
    assert calls["n"] == parsed_after_first  # cache hit: no re-parse
    assert [c["id"] for c in second] == [c["id"] for c in first]

    # Mutate the card: size/mtime changes -> fingerprint changes -> cache invalidates.
    write_memory_card(tmp_path, "pref.md", "我偏好先看公式推导再核对图")
    third = gateway._approved_memory_cards(cfg)
    assert calls["n"] > parsed_after_first  # re-parsed
    assert any("公式" in card["claim"] for card in third)


# --- v0.3 request queue ---


def test_request_queue_fifo_order_and_full():
    q = gateway.RequestQueue(2)
    assert q.try_enter() == ("run", None)
    k2, t2 = q.try_enter()
    k3, t3 = q.try_enter()
    assert (k2, k3) == ("wait", "wait")
    assert q.try_enter() == ("full", None)  # 第3个等待者才被拒

    order = []

    def run(name, ticket):
        assert q.wait_turn(ticket, timeout=5)
        order.append(name)
        q.release()

    th3 = threading.Thread(target=run, args=("third", t3))
    th2 = threading.Thread(target=run, args=("second", t2))
    th3.start()  # 故意先启动后到的等待者：顺序必须按入队不按线程启动
    th2.start()
    time.sleep(0.05)
    q.release()  # 第一个请求完成
    th2.join(timeout=5)
    th3.join(timeout=5)
    assert order == ["second", "third"]


def test_request_queue_wait_timeout_drops_ticket():
    q = gateway.RequestQueue(2)
    assert q.try_enter()[0] == "run"
    _, ticket = q.try_enter()
    t0 = time.monotonic()
    assert q.wait_turn(ticket, timeout=0.2) is False
    assert time.monotonic() - t0 < 2
    # 票据已移除：队列重新有两个空位
    assert q.try_enter()[0] == "wait"
    assert q.try_enter()[0] == "wait"
    assert q.try_enter()[0] == "full"


def test_request_queue_heartbeat_failure_dequeues():
    q = gateway.RequestQueue(1)
    assert q.try_enter()[0] == "run"
    _, ticket = q.try_enter()

    def broken_pipe():
        raise BrokenPipeError("client gone")

    assert q.wait_turn(ticket, heartbeat=broken_pipe, heartbeat_interval=0.05) is False
    assert q.try_enter()[0] == "wait"  # 位置已释放


def test_request_queue_heartbeats_then_acquires():
    q = gateway.RequestQueue(1)
    assert q.try_enter()[0] == "run"
    _, ticket = q.try_enter()
    beats: list[int] = []
    got: list[bool] = []

    def waiter():
        got.append(
            q.wait_turn(ticket, heartbeat=lambda: beats.append(1), heartbeat_interval=0.05)
        )

    th = threading.Thread(target=waiter)
    th.start()
    time.sleep(0.18)
    q.release()
    th.join(timeout=5)
    assert got == [True]
    assert len(beats) >= 2  # 排队期间持续心跳


# --- v0.3 conversation history ---


def test_build_history_section_budget_newest_first():
    history = [
        {"role": "user", "content": "A" * 50},
        {"role": "assistant", "content": "B" * 50},
        {"role": "user", "content": "C" * 50},
    ]
    section = gateway.build_history_section(history, 120)
    assert section is not None
    assert "C" * 50 in section  # 最新完整保留
    assert "B" * 50 in section
    assert "A" * 50 not in section  # 最旧被截到剩余预算
    assert "…" + "A" * 20 in section  # 保尾部
    assert "[用户]" in section and "[助手]" in section
    assert "不是新指令" in section


def test_build_history_section_filters_invalid():
    assert gateway.build_history_section(None, 4000) is None
    assert gateway.build_history_section([], 4000) is None
    assert (
        gateway.build_history_section(
            [{"role": "system", "content": "x"}, {"role": "user", "content": "  "}], 4000
        )
        is None
    )
    section = gateway.build_history_section(
        [
            {"role": "user", "content": "什么是德拜模型？"},
            "junk",
            {"role": "assistant", "content": "德拜模型把晶格振动近似为声子谱。"},
        ],
        4000,
    )
    assert "[用户] 什么是德拜模型？" in section
    assert "[助手] 德拜模型把晶格振动近似为声子谱。" in section


def test_build_prompt_history_sits_before_user_request():
    body = {
        "schema_version": 1,
        "message": "它的低温极限行为是什么？",
        "conversation_id": "conv-1",
        "context": {
            "history": [
                {"role": "user", "content": "什么是德拜模型？"},
                {"role": "assistant", "content": "德拜模型把晶格振动近似为连续介质声子谱。"},
            ],
        },
        "options": {"task_mode": "ask"},
    }
    cfg = {"index_pythonpath": "/x", "index_db": "/x.sqlite"}
    prompt = gateway.build_prompt(body, [], cfg)
    assert "此前对话回顾" in prompt
    assert prompt.index("此前对话回顾") < prompt.index("用户的请求")
    assert "德拜模型把晶格振动" in prompt


def test_runtime_error_question_routes_to_model_diagnosis():
    body = {
        "schema_version": 1,
        "message": "你刚刚为什么报错这个",
        "context": {
            "runtime": {
                "last_error": {
                    "code": "error_max_turns",
                    "message": "Assistant stream error: 网关错误：模型流以非成功状态结束：error_max_turns。",
                }
            }
        },
        "options": {"task_mode": "ask"},
    }
    cfg = {
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_deep_max_turns": "9",
        "claude_runtime_diag_max_turns": "4",
        "claude_timeout": 110,
    }
    assert gateway.request_is_runtime_error_question(body) is True
    assert gateway.cfg_for_request(body, cfg)["budget_profile"] == "runtime-diagnosis"


def test_runtime_error_question_ignores_subject_followup_with_stale_context():
    # A subject-matter follow-up ("怎么回事") must NOT be misrouted to runtime
    # diagnosis just because a prior error left last_error in the context.
    body = {
        "schema_version": 1,
        "message": "这个干涉现象到底是怎么回事，帮我讲讲",
        "context": {
            "runtime": {
                "last_error": {
                    "code": "error_max_turns",
                    "message": "网关错误：模型流以非成功状态结束：error_max_turns。",
                }
            }
        },
        "options": {"task_mode": "ask"},
    }
    assert gateway.request_is_runtime_error_question(body) is False


def test_build_prompt_runtime_error_context_gives_redacted_log_evidence(tmp_path):
    log_dir = tmp_path / "logs"
    log_dir.mkdir()
    (log_dir / "requests.jsonl").write_text(
        "\n".join(
            [
                json.dumps(
                    {
                        "ts": "2026-06-15T11:04:23+0800",
                        "status": 502,
                        "dur_ms": 36807,
                        "queued_ms": 0,
                        "note": "10 课程/课件/pdf/lesson.pdf",
                        "task_mode": "ask",
                        "conversation_id": "conv-1",
                    },
                    ensure_ascii=False,
                ),
                json.dumps(
                    {
                        "ts": "2026-06-15T11:10:38+0800",
                        "status": 200,
                        "dur_ms": 5811,
                        "queued_ms": 0,
                        "task_mode": "ask",
                        "budget_profile": "fast",
                        "claude_max_turns": "4",
                        "conversation_id": "conv-1",
                    },
                    ensure_ascii=False,
                ),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    body = {
        "schema_version": 1,
        "message": "这个错误怎么回事？",
        "conversation_id": "conv-1",
        "context": {
            "active_file": {"path": "10 课程/课件/pdf/lesson.pdf"},
            "pdf": {"page": 6},
            "runtime": {
                "last_error": {
                    "code": "incomplete_answer",
                    "message": "模型在工具调用后没有返回最终回答。",
                }
            }
        },
        "options": {"task_mode": "ask"},
    }
    cfg = {"index_pythonpath": "/x", "index_db": "/x.sqlite", "log_dir": log_dir}
    prompt = gateway.build_prompt(body, [("原件", tmp_path / "lesson.pdf")], cfg)
    assert "最近一次助手运行错误" in prompt
    assert "运行诊断证据包" in prompt
    assert '"status": 502' in prompt
    assert '"note_basename": "lesson.pdf"' in prompt
    assert "不要读取当前PDF/课件资料" in prompt
    assert "page-*6*" not in prompt
    assert "本次解锁的资料" not in prompt
    assert "incomplete_answer" in prompt


def test_build_prompt_without_history_unchanged():
    body = {"schema_version": 1, "message": "你好", "context": {}, "options": {}}
    cfg = {"index_pythonpath": "/x", "index_db": "/x.sqlite"}
    prompt = gateway.build_prompt(body, [], cfg)
    assert "此前对话回顾" not in prompt  # 旧请求体行为与v0.2一致
    assert "brain_library index query" not in prompt  # no-context prompt stays slim


def test_build_prompt_related_prefetch_from_relations(tmp_path):
    brain = make_brain(tmp_path)
    rel_path = brain / "_indexes" / "relations.jsonl"
    rel_path.parent.mkdir(exist_ok=True)
    rel_path.write_text(
        json.dumps(
            {
                "src": "knowledge/courses/solid-state-physics/slides/15自由电子论.pdf",
                "dst": "knowledge/courses/solid-state-physics/slides/17能带论.pdf",
                "rel": "same-course",
                "evidence": "same directory",
                "score": 0.55,
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    pdf = brain / "knowledge" / "courses" / "solid-state-physics" / "slides" / "15自由电子论.pdf"
    body = {
        "schema_version": 1,
        "message": "找相关材料",
        "context": {},
        "options": {"task_mode": "related"},
    }
    cfg = {
        "brain_root": brain,
        "relations_path": rel_path,
        "related_prefetch_limit": 5,
        "related_prefetch_max_chars": 500,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
    }
    prompt = gateway.build_prompt(body, [("原件", pdf)], cfg)
    assert "已预取的库内相关材料" in prompt
    assert "17能带论.pdf" in prompt


def test_cfg_for_request_uses_task_profile():
    cfg = {
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_deep_max_turns": "9",
        "claude_investigation_max_turns": "12",
        "claude_timeout": 110,
        "claude_investigation_timeout": 180,
    }
    assert gateway.cfg_for_request({"options": {"task_mode": "ask"}}, cfg)["budget_profile"] == "fast"
    assert gateway.cfg_for_request({"options": {"task_mode": "explain"}}, cfg)["budget_profile"] == "fast"
    assert gateway.cfg_for_request({"options": {"task_mode": "related"}}, cfg)["budget_profile"] == "deep"
    assert gateway.cfg_for_request({"options": {"task_mode": "citation-review"}}, cfg)["budget_profile"] == "deep"


def test_append_turn_limit_uncapped_by_default():
    cmd = ["claude"]
    gateway._append_turn_limit(cmd, {"claude_max_turns": ""})
    gateway._append_turn_limit(cmd, {})
    gateway._append_turn_limit(cmd, {"claude_max_turns": "0"})
    assert "--max-turns" not in cmd


def test_append_turn_limit_opt_in_positive():
    cmd = ["claude"]
    gateway._append_turn_limit(cmd, {"claude_max_turns": "5"})
    assert cmd[-2:] == ["--max-turns", "5"]


def test_cfg_for_request_honors_optional_turn_cap():
    base = {"claude_max_turns": "", "claude_timeout": 110}
    # default: no per-request cap
    assert gateway.cfg_for_request({"options": {"task_mode": "ask"}}, base)["claude_max_turns"] == ""
    # opt-in via plugin setting (options.max_tool_turns)
    capped = gateway.cfg_for_request({"options": {"task_mode": "ask", "max_tool_turns": 7}}, base)
    assert capped["claude_max_turns"] == "7"
    # zero stays uncapped
    assert gateway.cfg_for_request({"options": {"task_mode": "ask", "max_tool_turns": 0}}, base)["claude_max_turns"] == ""


def test_cfg_for_request_detects_multi_file_investigation_budget():
    cfg = {
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_deep_max_turns": "9",
        "claude_investigation_max_turns": "12",
        "claude_timeout": 110,
        "claude_investigation_timeout": 180,
    }
    body = {
        "message": "你帮我看看先进光子物理有哪些讲稿是重复的",
        "context": {"active_file": {"path": "课程/先进光子物理/lesson1-main.pdf"}},
        "options": {"task_mode": "ask"},
    }
    request_cfg = gateway.cfg_for_request(body, cfg)
    assert request_cfg["budget_profile"] == "investigation"
    assert request_cfg["claude_timeout"] == 180


def test_cfg_for_request_detects_public_web_budget():
    cfg = {
        "claude_max_turns": "8",
        "claude_fast_max_turns": "3",
        "claude_deep_max_turns": "9",
        "claude_web_max_turns": "12",
        "claude_timeout": 110,
        "claude_web_timeout": 170,
    }
    body = {
        "message": "搜索看看中科大近期热点",
        "context": {},
        "options": {"task_mode": "ask"},
    }
    request_cfg = gateway.cfg_for_request(body, cfg)
    assert request_cfg["budget_profile"] == "web"
    assert request_cfg["claude_timeout"] == 170


def test_build_prompt_adds_investigation_guardrail(tmp_path):
    brain = make_brain(tmp_path)
    body = {
        "schema_version": 1,
        "message": "扫描课程目录下的幻灯片/PDF文件，找出重复讲稿",
        "context": {"active_file": {"path": "课程/先进光子物理/lesson1-main.pdf"}},
        "options": {"task_mode": "ask"},
    }
    cfg = {
        "brain_root": brain,
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
    }
    prompt = gateway.build_prompt(body, [], cfg)
    assert "多文件检索/查重任务" in prompt
    assert "不要只输出" in prompt


def test_build_prompt_adds_web_tool_hint_for_search_request():
    body = {
        "schema_version": 1,
        "message": "搜索看看中科大近期热点",
        "context": {},
        "options": {"task_mode": "ask"},
    }
    cfg = {
        "index_pythonpath": "/x",
        "index_db": "/x.sqlite",
        "web_tools_enabled": True,
    }
    prompt = gateway.build_prompt(body, [], cfg)
    assert "WebSearch/WebFetch" in prompt
    assert "rtime-web-fetch search" in prompt
    assert "不要为了速度跳过必要核验" in prompt


def test_log_request_records_queue_and_conversation(tmp_path):
    handler = _handler_with_cfg(tmp_path)
    body = {
        "entry": "obsidian",
        "message": "hi",
        "conversation_id": "conv-9",
        "context": {},
        "options": {},
    }
    handler._log_request(
        body, 503, time.time(), {"answer": "busy", "sources": []}, queued_ms=1234
    )
    rec = json.loads((tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["status"] == 503  # busy-503如今必落日志
    assert rec["queued_ms"] == 1234
    assert rec["conversation_id"] == "conv-9"
    assert rec["budget_profile"] == "fast"
    assert rec["message_chars"] == 2
    assert rec["answer_chars"] == 4


def test_log_request_defaults_without_new_fields(tmp_path):
    handler = _handler_with_cfg(tmp_path)
    body = {"entry": "obsidian", "message": "hi", "context": {}, "options": {}}
    handler._log_request(body, 200, time.time(), {"answer": "ok", "sources": []})
    rec = json.loads((tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["queued_ms"] == 0
    assert "conversation_id" not in rec


def test_log_request_records_trace_milestones(tmp_path):
    handler = _handler_with_cfg(tmp_path)
    started = time.time()
    body = {"entry": "obsidian", "message": "hi", "context": {}, "options": {}}
    payload = {
        "answer": "ok",
        "sources": [{"path": "x.md"}],
        "trace": {
            "request_received": started,
            "queue_acquired": started + 0.01,
            "first_sse_emit": started + 0.02,
            "claude_spawned": started + 0.03,
            "first_stdout_event": started + 1.23,
            "process_exit": started + 1.8,
            "done_emit": started + 1.81,
        },
    }
    handler._log_request(body, 200, started, payload)
    rec = json.loads((tmp_path / "requests.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert rec["source_count"] == 1
    assert rec["trace_ms"]["claude_spawned_ms"] >= 20
    assert rec["trace_ms"]["first_stdout_event_ms"] >= 1200
    assert rec["trace_ms"]["done_emit_ms"] >= rec["trace_ms"]["process_exit_ms"]


def _intake_cfg(tmp_path, **over):
    cfg = {
        "brain_root": tmp_path / "brain",
        "intake_max_mb": 1,
        "notify_target": "",
        "reminder_register": str(tmp_path / "missing-register"),
        "log_dir": tmp_path / "logs",
    }
    cfg.update(over)
    return cfg


def _intake_body(name="笔记.md", data=b"hello brain", **over):
    body = {
        "schema_version": 1,
        "name": name,
        "content_base64": base64.b64encode(data).decode(),
        "source": "obsidian",
    }
    body.update(over)
    return body


def test_intake_writes_inbox_file_and_ticket(tmp_path):
    status, payload = gateway.process_intake(_intake_body(), _intake_cfg(tmp_path))
    assert status == 200 and payload["ok"] is True
    ticket = payload["ticket"]
    inbox = Path(ticket["inbox_path"])
    assert inbox.is_file() and inbox.read_bytes() == b"hello brain"
    assert "_inbox/obsidian/" in ticket["inbox_path"]
    record = json.loads(Path(ticket["ticket_path"]).read_text(encoding="utf-8"))
    assert record["status"] == "inbox"
    assert record["schema"] == "rtime-intake-ticket-v1"
    assert payload["needs_confirm"] is False
    assert payload["notify"] == "skipped"  # no target configured
    assert not list((tmp_path / "brain" / "_inbox").glob(".tmp-intake-*"))  # temp cleaned


def test_intake_personal_hint_needs_confirm_and_notifies(tmp_path, monkeypatch):
    register = tmp_path / "register"
    register.write_text("#!/bin/sh\nexit 0\n")
    register.chmod(0o755)
    seen = {}

    def fake_run(cmd, **kwargs):
        seen["cmd"] = cmd
        return subprocess.CompletedProcess(cmd, 0, stdout="{}", stderr="")

    monkeypatch.setattr(gateway.subprocess, "run", fake_run)
    cfg = _intake_cfg(tmp_path, notify_target="ou_user", reminder_register=str(register))
    status, payload = gateway.process_intake(
        _intake_body(name="录取通知.pdf", privacy_hint="personal"), cfg
    )
    assert status == 200
    assert payload["needs_confirm"] is True
    assert payload["notify"] == "sent"
    cmd = seen["cmd"]
    assert cmd[1:5] == ["add", "--mode", "notify", "--due"]
    message = cmd[cmd.index("--message") + 1]
    assert "待确认" in message and "录取通知.pdf" in message
    assert "hello brain" not in message  # never file bodies


def test_intake_duplicate_same_sha_is_idempotent(tmp_path):
    cfg = _intake_cfg(tmp_path)
    first = gateway.process_intake(_intake_body(), cfg)
    second = gateway.process_intake(_intake_body(), cfg)
    assert first[0] == 200 and second[0] == 200
    assert first[1]["ticket"]["sha256"] == second[1]["ticket"]["sha256"]


def test_intake_same_name_different_content_conflicts(tmp_path):
    cfg = _intake_cfg(tmp_path)
    assert gateway.process_intake(_intake_body(data=b"v1"), cfg)[0] == 200
    status, payload = gateway.process_intake(_intake_body(data=b"v2"), cfg)
    assert status == 409
    assert payload["ok"] is False


def test_intake_rejects_bad_base64_and_oversize_and_source(tmp_path):
    cfg = _intake_cfg(tmp_path)
    assert gateway.process_intake(_intake_body(content_base64="not@@base64"), cfg)[0] == 400
    big = base64.b64encode(b"x" * (2 * 1024 * 1024)).decode()
    assert gateway.process_intake(_intake_body(content_base64=big), cfg)[0] == 413
    assert gateway.process_intake(_intake_body(source="email"), cfg)[0] == 400
    assert gateway.process_intake({"schema_version": 1, "name": "", "content_base64": "aGk="}, cfg)[0] == 400
