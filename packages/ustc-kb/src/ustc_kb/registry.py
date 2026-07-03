# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""站点清单（记录抓了哪些网站/栏目）。真相源 = 同目录 sites.json。"""

import json
import os

_SITES_PATH = os.path.join(os.path.dirname(__file__), "sites.json")
_COLLEGES_PATH = os.path.join(os.path.dirname(__file__), "colleges.json")


def load():
    with open(_SITES_PATH, encoding="utf-8") as f:
        return json.load(f)


def departments():
    return load().get("departments", {})


def colleges():
    """各学院/系自动发现的清单（colleges.json，可能不存在）。"""
    if not os.path.exists(_COLLEGES_PATH):
        return {}
    with open(_COLLEGES_PATH, encoding="utf-8") as f:
        return json.load(f).get("colleges", {})


def dept(dept_id):
    """先查手工部处清单，再回退到自动学院清单。"""
    return departments().get(dept_id) or colleges().get(dept_id)


def dept_ids():
    return list(departments().keys())


def college_ids():
    """学院/系（kind=college；旧条目无 kind 视为 college）。"""
    return [k for k, v in colleges().items() if v.get("kind", "college") == "college"]


def org_ids():
    """管理机构（kind=admin）。"""
    return [k for k, v in colleges().items() if v.get("kind") == "admin"]


def research_ids():
    """科研机构/重点实验室（kind=research）。"""
    return [k for k, v in colleges().items() if v.get("kind") == "research"]


def unit_ids():
    """全部自动发现单位（学院 + 管理机构 + 科研机构）。"""
    return list(colleges().keys())
