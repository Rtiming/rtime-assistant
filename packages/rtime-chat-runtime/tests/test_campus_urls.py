# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Campus service URL table (块2 校园网页意图路由): builtin + env-file override."""

import json

from rtime_chat_runtime.campus_urls import (
    BUILTIN_CAMPUS_URLS,
    CAMPUS_URLS_ENV,
    campus_urls_hint,
    load_campus_urls,
)


def test_builtin_table_is_default(monkeypatch):
    monkeypatch.delenv(CAMPUS_URLS_ENV, raising=False)
    entries = load_campus_urls()
    assert entries == [dict(e) for e in BUILTIN_CAMPUS_URLS]
    assert any("busTimetable" in e["url"] for e in entries)
    assert all(e["url"].startswith("https://") for e in entries)


def test_hint_lists_every_entry_and_fetch_guidance(monkeypatch):
    monkeypatch.delenv(CAMPUS_URLS_ENV, raising=False)
    hint = campus_urls_hint()
    for entry in BUILTIN_CAMPUS_URLS:
        assert entry["url"] in hint
        assert entry["name"] in hint
    assert "WebFetch" in hint
    assert "rtime-web-fetch" in hint
    assert hint.startswith("\n\n[运行环境提示")


def test_env_file_list_replaces_builtin(monkeypatch, tmp_path):
    f = tmp_path / "campus.json"
    f.write_text(
        json.dumps([{"name": "测试页", "url": "https://example.org/x"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    entries = load_campus_urls()
    assert entries == [{"name": "测试页", "url": "https://example.org/x", "note": ""}]
    hint = campus_urls_hint()
    assert "https://example.org/x" in hint
    assert "busTimetable" not in hint  # replaced, not merged


def test_env_file_extend_appends_and_dedupes(monkeypatch, tmp_path):
    f = tmp_path / "campus.json"
    f.write_text(
        json.dumps(
            {
                "mode": "extend",
                "entries": [
                    {"name": "新条目", "url": "https://example.org/y", "note": "备注"},
                    # duplicate of a builtin url -> dropped
                    {"name": "重复", "url": BUILTIN_CAMPUS_URLS[0]["url"]},
                ],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    entries = load_campus_urls()
    assert len(entries) == len(BUILTIN_CAMPUS_URLS) + 1
    assert entries[-1] == {
        "name": "新条目",
        "url": "https://example.org/y",
        "note": "备注",
    }


def test_env_file_bad_json_falls_back(monkeypatch, tmp_path):
    f = tmp_path / "campus.json"
    f.write_text("{not json", encoding="utf-8")
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    assert load_campus_urls() == [dict(e) for e in BUILTIN_CAMPUS_URLS]


def test_env_file_missing_falls_back(monkeypatch, tmp_path):
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(tmp_path / "nope.json"))
    assert load_campus_urls() == [dict(e) for e in BUILTIN_CAMPUS_URLS]


def test_env_file_invalid_entries_fall_back(monkeypatch, tmp_path):
    f = tmp_path / "campus.json"
    # no valid entries: missing url scheme / missing name / not a dict
    f.write_text(
        json.dumps([{"name": "x", "url": "ftp://nope"}, {"url": "https://a.b"}, "str"]),
        encoding="utf-8",
    )
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    assert load_campus_urls() == [dict(e) for e in BUILTIN_CAMPUS_URLS]


# --- T8 热调:mtime 缓存 (设计 §2.10) ---


def test_edited_file_hot_reloads_on_mtime_change(monkeypatch, tmp_path):
    """Editing the override file takes effect on the next call (hot), no restart."""
    import os

    f = tmp_path / "campus.json"
    f.write_text(
        json.dumps([{"name": "旧", "url": "https://example.org/old"}]), encoding="utf-8"
    )
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    assert load_campus_urls() == [
        {"name": "旧", "url": "https://example.org/old", "note": ""}
    ]
    # edit the file; bump mtime so the (mtime,size) signature moves even on a fast FS.
    f.write_text(
        json.dumps([{"name": "新", "url": "https://example.org/new-page"}]),
        encoding="utf-8",
    )
    st = f.stat()
    os.utime(f, (st.st_atime, st.st_mtime + 5))
    assert load_campus_urls() == [
        {"name": "新", "url": "https://example.org/new-page", "note": ""}
    ]


def test_unchanged_file_is_not_reparsed(monkeypatch, tmp_path):
    """PERF: an unchanged override file does NOT re-read/parse — only a stat.

    We prime the cache, then make ``Path.read_text`` blow up: a second
    ``load_campus_urls`` that still returns the right table proves the parse path
    (the only file READ) was skipped on the unchanged file (design §2.10 no-regression).
    """
    from rtime_chat_runtime import campus_urls as mod

    f = tmp_path / "campus.json"
    f.write_text(
        json.dumps([{"name": "缓存", "url": "https://example.org/cached"}]),
        encoding="utf-8",
    )
    monkeypatch.setenv(CAMPUS_URLS_ENV, str(f))
    first = load_campus_urls()
    assert first == [{"name": "缓存", "url": "https://example.org/cached", "note": ""}]

    def _boom(*_a, **_k):  # any file READ now fails; stat still works
        raise AssertionError("unchanged campus file must not be re-read")

    monkeypatch.setattr(mod.Path, "read_text", _boom)
    second = load_campus_urls()  # unchanged: cache hit, no read_text
    assert second == first
