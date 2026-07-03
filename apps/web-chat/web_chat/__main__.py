# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Run the web-chat entry: ``python -m web_chat``."""

from __future__ import annotations

import logging

from .config import WebChatConfig
from .profiles import load_profiles
from .server import build_server


def main() -> None:
    cfg = WebChatConfig.from_env()
    logging.basicConfig(
        level=getattr(logging, cfg.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    profile_ids = [p["id"] for p in load_profiles()]  # fail fast on bad override
    httpd = build_server(cfg)
    mode = f"model({cfg.claude_cli})" if cfg.model_enabled else "echo (no claude CLI found)"
    print(
        f"[web-chat] listening on http://{cfg.bind}:{cfg.port} "
        f"(mode={mode}, profiles={','.join(profile_ids)}, state={cfg.state_dir})",
        flush=True,
    )
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()


if __name__ == "__main__":
    main()
