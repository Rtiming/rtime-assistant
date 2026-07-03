# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make ``rtime_chat_runtime`` importable when these tests are run directly
(e.g. ``pytest packages/rtime-chat-runtime/tests``) without relying on an
editable install. The module-submit gate also sets PYTHONPATH; this is the
belt-and-suspenders for ad-hoc local runs.
"""

import sys
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))
