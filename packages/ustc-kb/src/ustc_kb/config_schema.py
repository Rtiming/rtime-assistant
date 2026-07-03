# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Schema-driven config for the ustc-kb crawler — the ``UstcKbConfig`` model.

P2 config 收编 (批 3 · coverage-sweep, see docs/design/config-full-coverage-plan
-2026-07.zh-CN.md §二 批 3 + docs/reference/ustc-kb-config.zh-CN.md): the crawler's
``USTC_KB_*`` env surface (data root / crawl parallelism / the入库 date pin, read at
import time by ``ustc_kb.config`` + ``ustc_kb.crawl``) is expressed here as
``UstcKbConfig`` — a ``rtime-config`` pydantic-settings model — and REGISTERED into
the admin-core registry as module ``ustc-kb`` so the panel / config-agent can manage
it (全覆盖).

Why a SEPARATE module (not inside config.py): ``ustc_kb.config`` is a plain
import-time constants module for a standalone deterministic crawler that runs
without the admin-core stack; keeping it stdlib-only avoids forcing pydantic onto
every ``ustc-kb`` invocation. admin-core lazily imports ``UstcKbConfig`` from here to
register the module (mirrors the qq / web-chat leaf-import pattern), and the crawler
runtime is UNCHANGED — it still reads env directly via ``config.py``. Registration is
behaviour-neutral.

BEHAVIOUR-PRESERVING: every field default matches the crawler's ``os.environ.get``
default verbatim (a drift here misreports the panel default, not the crawler, which
never reads this class). No secrets (the login password is entered interactively,
never an env). ``USTC_KB_DATA`` / ``USTC_KB_WORKERS`` are 重启级 (read once at import);
``USTC_KB_TODAY`` is the入库-date pin the scriptable env supplies (no ``Date.now``).
"""

from __future__ import annotations

from pydantic_settings import SettingsConfigDict
from rtime_config import RtimeBaseSettings, config_field


class UstcKbConfig(RtimeBaseSettings):
    # env_prefix="" (not "USTC_KB_"): every field declares its COMPLETE accepted env
    # name via env_aliases, so the accepted surface equals exactly what is declared.
    model_config = SettingsConfigDict(
        env_prefix="",
        extra="ignore",
        populate_by_name=True,
        validate_default=True,
        case_sensitive=False,
    )

    data_root: str = config_field(
        default="~/Desktop/ustc-kb-data",
        description="抓取产物根目录(原始 HTML/文件/笔记/索引/台账);默认在仓库外避免 git 膨胀。"
        "~ 展开。改后重启(config.py 导入时解析派生所有子目录)。",
        scope="write:library",
        env_aliases=["USTC_KB_DATA"],
    )
    workers: int = config_field(
        default=8,
        description="抓取并发数(I/O 密集,并发提速);crawl.py 默认 8。改后重启。",
        ge=1,
        scope="write:library",
        env_aliases=["USTC_KB_WORKERS"],
    )
    today: str = config_field(
        default="2026-06-20",
        description="入库日期(脚本环境无 Date.now,显式给)。改后重启。",
        scope="write:library",
        env_aliases=["USTC_KB_TODAY"],
    )
