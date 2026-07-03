# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Web tool policy — the web ChannelProfile on the shared core tool policy.

Same pattern as ``qq_bridge.tool_policy``: intent detection + allow/disallow +
runtime hints live in ``rtime_chat_runtime.tool_policy``; this module only
declares the web channel profile. Unlike QQ the policy is built *per request
profile* (owner vs studentunion), because read_only comes from the selected
profile (compiled from the git profile layer by ``web_chat.profiles``), not from a
single process-wide flag.

read-only hard door — TWO drivers, either forces it (fail-closed union, T5b):
  * ``read_only=True`` on the per-profile dict — the profile's
    ``permissions.read_only`` already unioned with the env door in
    ``web_chat.profiles.load_profiles`` (env can only ADD read_only);
  * ``read_only_env=WEB_CHAT_READ_ONLY`` — belt-and-suspenders, so a bare
    ``WEB_CHAT_READ_ONLY=1`` flips the door even against a stale/writable profile
    record. Both fail closed (either True => read-only); env=0 can NEVER weaken it.
"""

from __future__ import annotations

from rtime_chat_runtime.tool_policy import READONLY_PERMISSION_MODE, ToolPolicy

from .config import READ_ONLY_ENV

# The page renders markdown + KaTeX (index.html), so steer the model AWAY from
# QQ-style plaintext math and toward the delimiters auto-render is configured for.
_WEB_FORMULA_HINT = (
    "\n\n[运行环境提示：网页端渲染Markdown与LaTeX。数学公式请用行内$...$"
    "或独立$$...$$，不要生成公式图片、不要调用绘图/截图工具把公式画成图。]"
)


def policy_for_profile(profile: dict) -> ToolPolicy:
    """Build the web ToolPolicy for one profile dict (see web_chat.profiles shape).

    Task/Agent are blocked like QQ: a web chat turn must answer, not spawn
    long-running subagents. read_only=True additionally flips the shared
    read-only hard door (closed allowlist + write tools hard-denied); the caller
    must then run the turn with READONLY_PERMISSION_MODE."""
    return ToolPolicy(
        channel="Web",
        entry="web",
        extra_disallowed=("Task", "Agent"),
        formula_hint=_WEB_FORMULA_HINT,
        read_only=bool(profile.get("read_only")),
        # belt-and-suspenders: env door can still turn it on over a writable profile
        # record; env=0 can never turn it off (fail-closed union, do not downgrade).
        read_only_env=READ_ONLY_ENV,
    )


__all__ = ["READONLY_PERMISSION_MODE", "policy_for_profile"]
