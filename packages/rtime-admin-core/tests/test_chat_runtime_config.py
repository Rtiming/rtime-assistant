# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""批 3 config 收编 — schema for the chat-runtime module (admin-core-owned).

``ChatRuntimeConfig`` lives in admin-core's schemas.py (rtime-chat-runtime is a
zero-dep runtime leaf, like library-gateway). It registers the package's one direct
literal env knob (``RTIME_CAMPUS_URLS_FILE``, read by campus_urls.load_campus_urls)
so the panel can manage it (全覆盖) — registration/coverage only, no runtime change
(the package still reads env directly).

Guards: (1) the field's env alias + metadata; (2) the campus-urls hot semantics;
(3) it is on default_registry; (4) the generated doc stays in lockstep.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from rtime_admin_core import default_registry
from rtime_admin_core.schemas import ChatRuntimeConfig

REPO_ROOT = Path(__file__).resolve().parents[3]
DOC_PATH = REPO_ROOT / "docs" / "config" / "chat-runtime.md"


def test_campus_urls_field_metadata():
    props = ChatRuntimeConfig.model_json_schema(by_alias=False)["properties"]
    assert set(props) == {"campus_urls_file"}
    p = props["campus_urls_file"]
    assert p["x-env-aliases"] == ["RTIME_CAMPUS_URLS_FILE"]
    assert p["x-reload"] == "hot"  # campus hit re-reads by mtime
    assert p["x-scope"] == "write:channel"
    assert p.get("x-secret") is not True


def test_campus_urls_default_is_none():
    # None/empty => builtin table (behaviour-preserving default).
    assert ChatRuntimeConfig().campus_urls_file is None


def test_campus_urls_env_loads():
    cfg = ChatRuntimeConfig(RTIME_CAMPUS_URLS_FILE="/x/urls.json")
    assert cfg.campus_urls_file == "/x/urls.json"


def test_registered_by_default():
    reg = default_registry()
    assert reg.has("chat-runtime")
    assert reg.model("chat-runtime") is ChatRuntimeConfig


# --- generated config doc stays in lockstep with the model --------------------
def _render_doc() -> str:
    out = subprocess.run(
        [
            sys.executable,
            "-m",
            "rtime_config",
            "rtime_admin_core.schemas:ChatRuntimeConfig",
            "--title",
            "chat-runtime 配置项",
        ],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )
    assert out.returncode == 0, out.stderr
    return out.stdout


def test_config_doc_is_up_to_date():
    assert DOC_PATH.exists(), (
        f"{DOC_PATH} missing — regenerate: python -m rtime_config "
        "rtime_admin_core.schemas:ChatRuntimeConfig --title 'chat-runtime 配置项' "
        f"--out {DOC_PATH}"
    )
    generated = _render_doc()
    on_disk = DOC_PATH.read_text(encoding="utf-8")
    assert generated == on_disk, (
        "docs/config/chat-runtime.md is stale — the config schema changed. Review the "
        "diff, then regenerate with: python -m rtime_config "
        "rtime_admin_core.schemas:ChatRuntimeConfig --title 'chat-runtime 配置项' "
        f"--out {DOC_PATH}"
    )
