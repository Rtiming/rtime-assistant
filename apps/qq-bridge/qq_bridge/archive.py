# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Full-content archive of incoming OneBot events (JSONL).

Distinct from ``rtime_chat_runtime.run_log`` (which is redacted metadata): this keeps
the *raw* event so QQ chat history is preserved verbatim for later use (e.g. the
chat-memory candidate pipeline). Best-effort: archiving must never break message
handling, so all errors are swallowed. The path is owner-local and gitignored.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from typing import Any

ArchiveFunc = Callable[[dict[str, Any]], None]


def build_archive_func(config: Any) -> ArchiveFunc | None:
    """按配置选归档形态(A1 P1):archive_root 设置 => 通道无关 envelope 分片归档
    (rtime_chat_runtime.archive);否则沿用 legacy 平铺单文件(archive_path)。
    两者同一纪律:在准入/触发判定之前调用、best-effort 永不抛。"""
    root = getattr(config, "archive_root", None)
    mode = getattr(config, "archive_mode", "events")
    if root:
        from rtime_chat_runtime.archive import ShardedArchiveWriter

        writer = ShardedArchiveWriter(root, "qq", mode=mode)
        if writer.enabled:
            return writer.append
        return None  # root 给了但 mode=off:显式关,不回落 legacy
    return make_archiver(getattr(config, "archive_path", None))


def make_archiver(path: str | None) -> ArchiveFunc | None:
    """Return a sync ``archive(event)`` appender, or None if no path is configured."""
    if not path:
        return None
    directory = os.path.dirname(path)

    def archive(event: dict[str, Any]) -> None:
        try:
            if directory:
                os.makedirs(directory, exist_ok=True)
            with open(path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(event, ensure_ascii=False) + "\n")
        except Exception:
            pass  # best-effort: never let archiving break the bridge

    return archive
