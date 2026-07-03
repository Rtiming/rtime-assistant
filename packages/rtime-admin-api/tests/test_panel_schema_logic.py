# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Drive the panel's pure JS form-logic (panel.schema.js) through node.

The schema→form generation and change-collection are non-trivial and load-
bearing (design: forms are 100% schema-generated; secret-left-blank must not be
submitted; unchanged fields must be dropped). We test that logic directly with
node — the assertions live in ``panel_schema.test.js``. Skipped cleanly when
node is unavailable so the Python-only gate still passes.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

_TEST_JS = Path(__file__).resolve().parent / "panel_schema.test.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
def test_panel_schema_logic_node():
    result = subprocess.run(
        ["node", str(_TEST_JS)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, (
        f"panel_schema.test.js failed:\nstdout:\n{result.stdout}\n"
        f"stderr:\n{result.stderr}"
    )
    assert "passed" in result.stdout
