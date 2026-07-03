# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""工作记录：每次抓取/审计/组装都追加一条，便于复盘、续爬、交接。"""

import datetime
import json
import os

from . import config


def log(action, detail):
    """action 如 crawl/audit/assemble；detail 为 dict。写 jsonl + 人读 md。"""
    config.ensure_dirs()
    ts = datetime.datetime.now().isoformat(timespec="seconds")
    rec = {"ts": ts, "action": action, "detail": detail}
    with open(config.WORKLOG_JSONL, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    line = "- %s **%s** %s\n" % (ts, action, json.dumps(detail, ensure_ascii=False))
    new = not os.path.exists(config.WORKLOG_MD)
    with open(config.WORKLOG_MD, "a", encoding="utf-8") as f:
        if new:
            f.write("# USTC-KB 工作记录\n\n")
        f.write(line)
    return rec
