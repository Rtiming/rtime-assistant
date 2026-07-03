# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Simulation-harness helpers shared by the bridges' test suites (T4 模拟测试台地基).

Import-safe in production installs: stdlib only, no pytest / no network / no
subprocess. Ships inside the package (like ``numpy.testing``) so bridge test
suites and the future ``rtime-chat --profile`` console harness can import one
canonical set of fakes instead of each re-inventing monkeypatched doubles.

Contents:
  - :mod:`rtime_chat_runtime.testing.fake_runner` — ``FakeModelRunner``, a drop-in
    test double for :func:`rtime_chat_runtime.model_runner.run_claude` that records
    every invocation parameter and plays back scripted replies;
  - :mod:`rtime_chat_runtime.testing.synth` — synthetic inbound-event constructors
    (``make_qq_private`` / ``make_qq_group_at`` / ``make_feishu_msg``) with realistic
    OneBot v11 / Feishu wire shapes.

Design reference: docs/design/mainline-profiles-and-entries-2026-07.zh-CN.md §三.
"""

from __future__ import annotations

from .fake_runner import FakeModelRunner, RecordedModelCall, ScriptedReply
from .synth import make_feishu_msg, make_qq_group_at, make_qq_private

__all__ = [
    "FakeModelRunner",
    "RecordedModelCall",
    "ScriptedReply",
    "make_feishu_msg",
    "make_qq_group_at",
    "make_qq_private",
]
