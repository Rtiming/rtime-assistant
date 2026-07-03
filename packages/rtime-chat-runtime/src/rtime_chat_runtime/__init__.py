# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Provider-neutral chat-bridge runtime primitives.

Shared by apps/feishu-bridge today and the future WeChat bridge. Each module is
self-contained (stdlib / asyncio only, zero bridge coupling); import the
submodule you need, e.g. ``from rtime_chat_runtime.run_log import append_run_event``.
"""

__version__ = "0.1.0"
