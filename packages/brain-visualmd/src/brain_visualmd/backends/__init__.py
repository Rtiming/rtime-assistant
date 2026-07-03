# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Pluggable transcription backends.

Select with ``--backend <name>``. To add one, implement
:class:`~brain_visualmd.backends.base.TranscribeBackend` (or subclass
``SyncPageBackend``), register it here, and document it in
``docs/brain-visualmd-module.zh-CN.md`` §5. See that doc's "如何写一个新后端".
"""

from __future__ import annotations

from .agent import AgentBackend
from .base import BatchContext, BatchResult, SyncPageBackend, TranscribeBackend
from .doc import DocOcrBackend
from .escalate import EscalationBackend
from .stub import StubBackend
from .vision_api import VisionApiBackend

_REGISTRY = {
    "agent": AgentBackend,
    "stub": StubBackend,
    "vision": VisionApiBackend,
    "doc": DocOcrBackend,
    "escalate": EscalationBackend,
}


def available() -> list[str]:
    return sorted(_REGISTRY)


def get_backend(name: str) -> TranscribeBackend:
    try:
        return _REGISTRY[name]()
    except KeyError:
        raise KeyError(
            f"unknown backend {name!r}; available: {', '.join(available())}"
        ) from None


__all__ = [
    "get_backend",
    "available",
    "TranscribeBackend",
    "SyncPageBackend",
    "BatchContext",
    "BatchResult",
    "AgentBackend",
    "StubBackend",
    "VisionApiBackend",
    "DocOcrBackend",
    "EscalationBackend",
]
