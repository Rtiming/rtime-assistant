# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""brain-visualmd — visual-first strict transcription of materials into AI-readable Markdown.

Standalone module. Implements the quality baseline in
``docs/ai-readable-markdown-standard.zh-CN.md`` and the design in
``docs/brain-visualmd-module.zh-CN.md``.

Pipeline stages (each runnable on its own): render -> plan -> transcribe ->
merge -> validate. Transcription backends are pluggable (agent / local / api).

Deliberately NOT wired into the main intake pipeline and does NOT publish into
``brain``. Outputs land in a staging directory until the module is proven.
"""

from .spec import SPEC_VERSION

__all__ = ["SPEC_VERSION"]
__version__ = "0.1.0"
