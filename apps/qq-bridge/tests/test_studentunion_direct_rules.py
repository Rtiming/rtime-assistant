# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""A0 直答 FAQ: profiles/studentunion/direct-rules.json 本身的合同测试。

真实规则文件必须:全部规则可编译(坏正则会被 loader 静默丢弃,靠计数抓)、
校车走 bus_timetable 实时抓取(离线 fixture)、校历文本带教务处来源 URL、
高新班车与无关问题回落模型(None)。改规则文件必须跑本测试。
"""

from pathlib import Path

from rtime_chat_runtime.direct_reply import DirectReplyEngine

REPO_ROOT = Path(__file__).resolve().parents[3]
RULES_FILE = REPO_ROOT / "profiles" / "studentunion" / "direct-rules.json"
BUS_FIXTURE = (
    REPO_ROOT
    / "packages"
    / "rtime-chat-runtime"
    / "tests"
    / "fixtures"
    / "bus_timetable_dongqu_workday.html"
)


def _engine() -> DirectReplyEngine:
    html = BUS_FIXTURE.read_text(encoding="utf-8")
    return DirectReplyEngine.load(str(RULES_FILE), fetch_html=lambda url: html)


def test_all_rules_compile_none_dropped():
    engine = _engine()
    assert engine.enabled
    # loader 静默丢弃坏正则/坏字段的规则 —— 计数不符即有规则被丢
    assert len(engine._rules) == 3
    assert [r.name for r in engine._rules] == ["identity", "campus-bus", "academic-calendar"]


def test_bus_queries_hit_live_timetable():
    engine = _engine()
    for text in ("班车时刻表", "班车", "校车", "东区班车几点", "什么时候有校车"):
        hit = engine.match_rule(text)
        assert hit is not None, text
        name, reply = hit
        assert name == "campus-bus", text
        assert reply.strip(), text


def test_gaoxin_bus_falls_back_to_model():
    # 高新园区班车在另一页面(结构未适配),绝不拿校园班车数据冒充
    assert _engine().match_rule("高新的班车几点") is None


def test_identity_queries_hit_static():
    # A3 §六:身份问答("你谁")秒回,省模型调用(校会问了4+次)
    engine = _engine()
    for text in ("你是谁", "你谁啊", "你是干什么的", "你能干什么", "介绍一下你自己"):
        hit = engine.match_rule(text)
        assert hit is not None, text
        assert hit[0] == "identity", text
        assert "学生会答疑助手" in hit[1]


def test_calendar_queries_hit_static_answer():
    engine = _engine()
    for text in (
        "校历",
        "教学日历发一下",
        "什么时候放暑假",
        "什么时候开学",
        "国庆什么时候放假",
        "寒假安排",
        "开学时间",
    ):
        hit = engine.match_rule(text)
        assert hit is not None, text
        name, reply = hit
        assert name == "academic-calendar", text
        # 硬规则:直答内容带权威来源 URL;关键日期在
        assert "teach.ustc.edu.cn" in reply
        assert "暑假" in reply and "寒假" in reply


def test_unrelated_text_falls_back_to_model():
    engine = _engine()
    for text in ("食堂几点开门", "作业什么时候交", "考试什么时候出分", "你好"):
        assert engine.match_rule(text) is None, text
