# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""块5 正则直答: rule loading/matching, text replies, bus timetable fetch+parse
(offline — the fixture is the real page saved once on 2026-07-02), TTL cache,
and the never-raise fallback contract."""

import json
import time
from pathlib import Path
from urllib.parse import quote

from rtime_chat_runtime.direct_reply import (
    DEFAULT_BUS_ENDPOINT,
    DirectReplyEngine,
    DirectReplyProvider,
    parse_bus_timetable,
)

FIXTURE = (
    Path(__file__).resolve().parent / "fixtures" / "bus_timetable_dongqu_workday.html"
)


def _write_rules(tmp_path, rules) -> str:
    path = tmp_path / "rules.json"
    path.write_text(json.dumps(rules, ensure_ascii=False), encoding="utf-8")
    return str(path)


def _text_rule(name="faq", patterns=("在吗",), reply="在的。"):
    return {"name": name, "patterns": list(patterns), "type": "text", "reply": reply}


def _bus_rule(name="bus", patterns=("班车",), params=None, ttl=None):
    rule = {"name": name, "patterns": list(patterns), "type": "bus_timetable"}
    if params is not None:
        rule["params"] = params
    if ttl is not None:
        rule["ttl_seconds"] = ttl
    return rule


def _fixture_fetcher(calls):
    html = FIXTURE.read_text(encoding="utf-8")

    def fetch(url):
        calls.append(url)
        return html

    return fetch


# --- loading ---
def test_load_text_rule_and_match(tmp_path):
    engine = DirectReplyEngine.load(_write_rules(tmp_path, [_text_rule()]))
    assert engine.enabled
    assert engine.match("在吗") == "在的。"
    assert engine.match_rule("在吗") == ("faq", "在的。")


def test_no_match_returns_none(tmp_path):
    engine = DirectReplyEngine.load(_write_rules(tmp_path, [_text_rule()]))
    assert engine.match("讲讲热统配分函数") is None


def test_first_match_wins(tmp_path):
    rules = [
        _text_rule(name="first", patterns=["班车"], reply="第一条"),
        _text_rule(name="second", patterns=["班车"], reply="第二条"),
    ]
    engine = DirectReplyEngine.load(_write_rules(tmp_path, rules))
    assert engine.match_rule("班车几点") == ("first", "第一条")


def test_rules_dict_wrapper_accepted(tmp_path):
    engine = DirectReplyEngine.load(_write_rules(tmp_path, {"rules": [_text_rule()]}))
    assert engine.match("在吗") == "在的。"


def test_empty_path_disabled():
    engine = DirectReplyEngine.load("")
    assert not engine.enabled
    assert engine.match("在吗") is None


def test_missing_file_disabled(tmp_path):
    engine = DirectReplyEngine.load(str(tmp_path / "nope.json"))
    assert not engine.enabled
    assert engine.match("在吗") is None


def test_corrupt_json_disabled(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    engine = DirectReplyEngine.load(str(path))
    assert not engine.enabled
    assert engine.match("在吗") is None


def test_non_list_json_disabled(tmp_path):
    engine = DirectReplyEngine.load(_write_rules(tmp_path, {"foo": 1}))
    assert not engine.enabled


def test_bad_pattern_skipped_good_one_still_matches(tmp_path):
    rule = _text_rule(patterns=["[unclosed", "在吗"])
    engine = DirectReplyEngine.load(_write_rules(tmp_path, [rule]))
    assert engine.match("在吗") == "在的。"


def test_rule_without_usable_patterns_dropped(tmp_path):
    rules = [
        {"name": "broken", "patterns": ["[bad"], "type": "text", "reply": "x"},
        _text_rule(),
    ]
    engine = DirectReplyEngine.load(_write_rules(tmp_path, rules))
    assert engine.match_rule("在吗") == ("faq", "在的。")


def test_text_rule_without_reply_dropped(tmp_path):
    rules = [{"name": "empty", "patterns": ["在吗"], "type": "text"}]
    engine = DirectReplyEngine.load(_write_rules(tmp_path, rules))
    assert not engine.enabled


def test_unknown_type_dropped(tmp_path):
    rules = [{"name": "weird", "patterns": ["在吗"], "type": "weather"}]
    engine = DirectReplyEngine.load(_write_rules(tmp_path, rules))
    assert not engine.enabled


# --- bus timetable parser (fixture = real page, saved 2026-07-02) ---
def test_parse_fixture_routes_and_note():
    routes, note = parse_bus_timetable(FIXTURE.read_text(encoding="utf-8"))
    assert len(routes) == 2
    stops1, times1 = routes[0]
    assert stops1 == ["东区", "南区"]
    assert times1[:4] == ["7:30", "08:30", "11:35", "11:45#"]  # span # flattened
    assert times1[-1] == "22:30"
    stops2, _times2 = routes[1]
    assert stops2 == ["东区", "北区", "西区"]
    assert "公交车辆" in note


def test_parse_garbage_html_yields_nothing():
    routes, note = parse_bus_timetable("<html><body>maintenance</body></html>")
    assert routes == [] and note == ""


# --- bus timetable rule ---
def test_bus_reply_formats_routes_source_and_time(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]), fetch_html=_fixture_fetcher(calls)
    )
    reply = engine.match("班车时刻表")
    assert reply is not None
    assert "东区→南区" in reply
    assert "7:30" in reply and "11:45#" in reply
    assert DEFAULT_BUS_ENDPOINT in reply  # 来源 URL
    assert "抓取时间:" in reply
    assert len(calls) == 1
    assert quote("东区") in calls[0] and quote("工作日") in calls[0]  # defaults


def test_bus_keyword_overrides_startpoint_and_week(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]), fetch_html=_fixture_fetcher(calls)
    )
    assert engine.match("节假日西区班车几点") is not None
    assert quote("西区") in calls[0] and quote("节假日") in calls[0]


def test_bus_earliest_keyword_is_startpoint(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]), fetch_html=_fixture_fetcher(calls)
    )
    assert engine.match("东区到西区的班车几点") is not None
    assert quote("东区") in calls[0]  # 起点语义: first-mentioned campus wins


def test_bus_rule_params_override_defaults(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule(params={"startpoint": "南区"})]),
        fetch_html=_fixture_fetcher(calls),
    )
    assert engine.match("班车时刻") is not None
    assert quote("南区") in calls[0]


def test_bus_gaoxin_falls_back_to_model(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]), fetch_html=_fixture_fetcher(calls)
    )
    assert (
        engine.match("高新园区班车几点") is None
    )  # different page: let the model answer
    assert calls == []  # and never fetch the wrong page


def test_bus_cache_hits_within_ttl(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule(ttl=3600)]),
        fetch_html=_fixture_fetcher(calls),
    )
    first = engine.match("班车时刻")
    second = engine.match("班车时刻")
    assert first == second
    assert len(calls) == 1  # served from cache
    engine.match("西区班车时刻")
    assert len(calls) == 2  # different params => separate cache entry


def test_bus_cache_expires_after_ttl(tmp_path):
    calls: list[str] = []
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule(ttl=0.05)]),
        fetch_html=_fixture_fetcher(calls),
    )
    assert engine.match("班车时刻") is not None
    time.sleep(0.08)
    assert engine.match("班车时刻") is not None
    assert len(calls) == 2


def test_bus_fetch_failure_returns_none(tmp_path):
    def boom(url):
        raise OSError("network down")

    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]), fetch_html=boom
    )
    assert engine.match("班车时刻") is None  # never raises through


def test_bus_unparseable_page_returns_none(tmp_path):
    engine = DirectReplyEngine.load(
        _write_rules(tmp_path, [_bus_rule()]),
        fetch_html=lambda url: "<html>改版了</html>",
    )
    assert engine.match("班车时刻") is None


def test_bus_failure_does_not_try_later_rules(tmp_path):
    # 首中即用: a matched-but-failed rule falls back to the MODEL, not to rule #2.
    rules = [
        _bus_rule(patterns=["班车"]),
        _text_rule(name="catchall", patterns=["班车"], reply="不该出现"),
    ]

    def boom(url):
        raise OSError("down")

    engine = DirectReplyEngine.load(_write_rules(tmp_path, rules), fetch_html=boom)
    assert engine.match("班车时刻") is None


# --- T8 热调:DirectReplyProvider(按 mtime 失效重建,设计 §2.10) ---


def test_provider_empty_path_is_disabled_no_stat():
    """No rules path => a disabled engine, and current() never stats a file."""
    prov = DirectReplyProvider("")
    assert not prov.current().enabled
    assert prov.current().match("在吗") is None


def test_provider_loads_and_matches(tmp_path):
    path = _write_rules(tmp_path, [_text_rule()])
    prov = DirectReplyProvider(path)
    assert prov.current().enabled
    assert prov.current().match("在吗") == "在的。"


def test_provider_hot_reloads_on_file_edit(tmp_path):
    """Editing the rules file takes effect on the next current() — no restart."""
    import os

    path = tmp_path / "rules.json"
    path.write_text(
        json.dumps([_text_rule(reply="第一版")], ensure_ascii=False), encoding="utf-8"
    )
    prov = DirectReplyProvider(str(path))
    assert prov.current().match("在吗") == "第一版"
    # rewrite with a new reply + a new pattern; bump mtime so (mtime,size) moves.
    path.write_text(
        json.dumps(
            [_text_rule(patterns=("在吗", "在不"), reply="第二版")], ensure_ascii=False
        ),
        encoding="utf-8",
    )
    st = path.stat()
    os.utime(path, (st.st_atime, st.st_mtime + 5))
    assert prov.current().match("在吗") == "第二版"
    assert prov.current().match("在不") == "第二版"  # new pattern also live


def test_provider_appears_after_missing_then_created(tmp_path):
    """A path that starts missing (disabled) becomes enabled once the file appears."""
    path = tmp_path / "rules.json"
    prov = DirectReplyProvider(str(path))
    assert not prov.current().enabled  # file absent
    path.write_text(json.dumps([_text_rule()], ensure_ascii=False), encoding="utf-8")
    assert prov.current().enabled  # created -> hot-detected
    assert prov.current().match("在吗") == "在的。"


def test_provider_unchanged_file_is_not_reloaded(tmp_path):
    """PERF: an unchanged rules file is NOT re-parsed — current() returns the SAME
    engine object (only a stat happened). This is the no-per-message-cost guarantee
    (design §2.10 owner hard constraint)."""
    path = _write_rules(tmp_path, [_text_rule()])
    prov = DirectReplyProvider(path)
    e1 = prov.current()
    e2 = prov.current()
    e3 = prov.current()
    assert e1 is e2 is e3  # same instance: no rebuild on an unchanged file


def test_provider_unchanged_file_does_no_read(tmp_path, monkeypatch):
    """PERF: the unchanged path does a stat but NO file open/parse."""
    import builtins

    path = _write_rules(tmp_path, [_text_rule()])
    prov = DirectReplyProvider(path)
    prov.current()  # prime

    real_open = builtins.open

    def _guard(f, *a, **k):
        if str(f) == str(path):
            raise AssertionError("unchanged rules file must not be re-opened")
        return real_open(f, *a, **k)

    monkeypatch.setattr(builtins, "open", _guard)
    assert prov.current().match("在吗") == "在的。"  # served from cache, no open
