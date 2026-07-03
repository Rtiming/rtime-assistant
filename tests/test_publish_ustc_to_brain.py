# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
# -*- coding: utf-8 -*-
"""publish_ustc_to_brain 离线单测：路径前缀改写 + 文件索引合并(保留其他管线条目)。"""

import importlib.util
import json
import os

_SPEC = importlib.util.spec_from_file_location(
    "publish_ustc_to_brain",
    os.path.join(os.path.dirname(__file__), "..", "scripts", "publish_ustc_to_brain.py"),
)
pub = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(pub)

STAGING_FILES = "/var/lib/rtime-assistant/ustc-kb-data/files/"
BRAIN_FILES = "/mnt/brain/knowledge/institutions/ustc/sources/files/"


def test_rewrite_path_prefix():
    assert (
        pub.rewrite_path(STAGING_FILES + "physics/a.pdf", STAGING_FILES, BRAIN_FILES)
        == BRAIN_FILES + "physics/a.pdf"
    )
    # 非暂存前缀的路径原样保留
    assert pub.rewrite_path("/other/x.pdf", STAGING_FILES, BRAIN_FILES) == "/other/x.pdf"


def test_merge_index_replaces_own_dept_keeps_others():
    # brain 既有:physics(本管线,将被替换) + 教务处通知附件(别的管线,须保留)
    brain = [
        json.dumps({"dept_id": "physics", "local_path": "/old/p.pdf", "sha256": "old"}),
        json.dumps({"dept_id": "教务处通知附件", "local_path": "/x/t.pdf", "sha256": "t"}),
    ]
    staging = [
        json.dumps({"dept_id": "physics", "local_path": STAGING_FILES + "physics/new.pdf", "sha256": "new"}),
    ]
    merged = pub.merge_index(staging, brain, STAGING_FILES, BRAIN_FILES)
    depts = [r["dept_id"] for r in merged]
    # 别的管线条目保留
    assert "教务处通知附件" in depts
    # physics 旧条目被替换为新条目(路径已改写到 brain)
    phys = [r for r in merged if r["dept_id"] == "physics"]
    assert len(phys) == 1
    assert phys[0]["sha256"] == "new"
    assert phys[0]["local_path"] == BRAIN_FILES + "physics/new.pdf"
