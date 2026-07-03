# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Run the QQ bridge: ``python -m qq_bridge``.

Uses the real Claude+brain pipeline when a model CLI is available (M2); otherwise
falls back to the M1 echo handler (e.g. local dev without the claude-rtime wrapper).
"""

from __future__ import annotations

import asyncio
import logging

from .alerts import build_lifecycle_alerter
from .app import build_pipeline
from .archive import build_archive_func
from .config import PRIVATE_ACCESS_ADMIN_ALLOWED, ConfigProvider
from .onebot.ws_server import OneBotWSServer
from .tool_policy import policy_for_config


def main() -> None:
    # Profile-aware when RTIME_PROFILE is set; plain env otherwise (backward compat).
    # T8 (§2.10): a ConfigProvider so the HOT fields (user lists / system prompt /
    # model default / direct-rules file) hot-reload on the next message when the
    # profile source files change — no container restart. The startup snapshot below
    # is used for the RESTART-level wiring (WS bind, read_only door, archive, alerts).
    provider = ConfigProvider.load()
    config = provider.current()
    logging.basicConfig(
        level=getattr(logging, config.log_level, logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if (
        not config.admin_ids
        and not config.allowed_users
        and config.private_access == PRIVATE_ACCESS_ADMIN_ALLOWED
    ):
        print(
            "[qq-bridge] WARNING: no admin (QQ_ADMIN_IDS / QQ_OWNER_IDS) and no "
            "QQ_ALLOWED_USERS -> REJECTING ALL private messages (hard gate). "
            "Set QQ_OWNER_IDS (or QQ_ADMIN_IDS) to allow your QQ id, or set "
            "QQ_PRIVATE_ACCESS for a public instance.",
            flush=True,
        )
    missing_allow = config.public_groups - config.group_allowlist
    if missing_allow:
        print(
            "[qq-bridge] WARNING: QQ_PUBLIC_GROUPS not covered by QQ_GROUP_ALLOWLIST "
            f"({', '.join(sorted(missing_allow))}) -> the bot will AUTO-LEAVE these "
            "groups. Add them to QQ_GROUP_ALLOWLIST.",
            flush=True,
        )
    echo = not config.model_enabled
    mode = "echo (no claude CLI found)" if echo else f"model({config.claude_cli})"
    pipeline = build_pipeline(config, echo=echo, config_provider=provider)

    server = OneBotWSServer(
        host=config.ws_host,
        port=config.ws_port,
        path=config.ws_path,
        access_token=config.access_token,
        pipeline=pipeline,
        on_lifecycle=build_lifecycle_alerter(config),
        archive=build_archive_func(config),
        replay_grace_seconds=config.replay_grace_seconds,
        suppress_sends_when_offline=config.suppress_sends_when_offline,
    )
    print(
        f"[qq-bridge] reverse-WS listening on ws://{config.ws_host}:{config.ws_port}"
        f"{config.ws_path} (mode={mode}, owners={len(config.owner_ids)}, "
        f"public_groups={len(config.public_groups)}, "
        f"private_access={config.private_access}, "
        f"group_reply_at_sender={'on' if config.group_reply_at_sender else 'off'}, "
        f"read_only={'on' if policy_for_config(config).is_read_only() else 'off'}, "
        f"invite_policy={config.group_invite_policy}, "
        f"archive={'sharded' if config.archive_root else ('legacy' if config.archive_path else 'off')})",
        flush=True,
    )
    asyncio.run(server.serve_forever())


if __name__ == "__main__":
    main()
