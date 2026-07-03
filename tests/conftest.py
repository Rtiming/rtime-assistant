# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Make ``pytest tests/`` runnable from the repo root with only requirements-dev
installed: add every package's ``src`` dir (plus the gateway app and the
brain-intake module scripts) to ``sys.path`` so individual tests don't each need
a bespoke ``PYTHONPATH``. Top-level package names are distinct, so there is no
shadowing.
"""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

_candidates = [
    ROOT / "apps" / "assistant-gateway",   # gateway.py, rtime_chat.py
    ROOT / "scripts" / "brain-intake",     # m1_registry, intake_common, memory_schema, ...
]
_candidates += sorted((ROOT / "packages").glob("*/src"))

for _path in _candidates:
    _sp = str(_path)
    if _path.is_dir() and _sp not in sys.path:
        sys.path.insert(0, _sp)
