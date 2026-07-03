# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""QQ tool policy — the QQ ChannelProfile on the shared core tool policy.

The intent detection + runtime hints live in ``rtime_chat_runtime.tool_policy`` now
(channel-unification P1). QQ just declares its profile (block Task/Agent, entry=qq,
plaintext math hint) and re-exports the functions app.py imports.

read-only hard door — TWO drivers, either forces it (T2 profile consumption):
  * ``read_only_env="QQ_READ_ONLY"`` — the legacy env flag, checked per call;
  * ``read_only=True`` on a per-config policy built via :func:`policy_for_config` —
    the value ``config.read_only`` resolved ``env > store > profile > default``, so a
    profile ``permissions.read_only: true`` now forces the SAME hard door (closed
    allowlist + write-tool deny set + forced dontAsk permission mode) it did for
    ``QQ_READ_ONLY=1``. app.py builds the per-config policy and additionally forces
    the run's permission mode to READONLY_PERMISSION_MODE (see core tool_policy for
    the CLI deny/allow semantics writeup).

The module-level ``QQ_POLICY`` + free functions stay for the env-only path
(``__main__`` startup banner, direct callers, the 195 legacy tests) — they read
``QQ_READ_ONLY`` exactly as before.
"""

from __future__ import annotations

from rtime_chat_runtime.tool_policy import READONLY_PERMISSION_MODE, ToolPolicy

_QQ_FORMULA_HINT = (
    "\n\n[运行环境提示：QQ 不支持富文本/LaTeX 渲染。写数学时用 Unicode 近似或纯文本表达"
    "（如 ω_pe、E=mc²、≈、≫），不要生成公式图片、不要调用绘图/截图工具把公式画成图。]"
)

# QQ's channel-fixed policy knobs, shared by the env-only singleton below and the
# per-config policy factory (so both stay in sync — one source for the QQ profile).
_QQ_POLICY_KWARGS = dict(
    channel="QQ",
    entry="qq",
    # Task/Agent spawn long-running subagents that hang a QQ chat turn (kimi-code
    # over-uses them); block both on top of the core's Cron* disallow.
    extra_disallowed=("Task", "Agent"),
    personal_library_env="QQ_OWNER_PERSONAL_LIBRARY_ACCESS",
    formula_hint=_QQ_FORMULA_HINT,
)

# Env-only singleton: read_only driven by QQ_READ_ONLY per call (legacy path).
QQ_POLICY = ToolPolicy(read_only_env="QQ_READ_ONLY", **_QQ_POLICY_KWARGS)


def policy_for_config(config) -> ToolPolicy:
    """Build the per-request ToolPolicy for a loaded ``QQBridgeConfig``.

    ``config.read_only`` already carries the ``env > store > profile > default``
    resolution (see ``QQBridgeConfig.from_profile``), so setting ``read_only`` from
    it makes a profile ``permissions.read_only: true`` force the hard door. The env
    flag is ALSO wired (``read_only_env``) so a bare ``QQ_READ_ONLY=1`` with no
    profile still flips it and env can still turn it on over a profile that left it
    off — belt and suspenders, both fail closed (either True => read-only).
    """
    return ToolPolicy(
        read_only=bool(getattr(config, "read_only", False)),
        read_only_env="QQ_READ_ONLY",
        **_QQ_POLICY_KWARGS,
    )


def allowed_tools_for_text(text: str) -> list[str] | None:
    return QQ_POLICY.allowed_tools_for_text(text)


def disallowed_tools_for_text(text: str) -> list[str]:
    return QQ_POLICY.disallowed_tools_for_text(text)


def add_runtime_policy_hints(text: str) -> str:
    return QQ_POLICY.add_runtime_policy_hints(text)


def read_only_enabled() -> bool:
    """Whether the QQ_READ_ONLY hard door is on (env-driven, checked per call)."""
    return QQ_POLICY.is_read_only()


__all__ = [
    "QQ_POLICY",
    "READONLY_PERMISSION_MODE",
    "policy_for_config",
    "allowed_tools_for_text",
    "disallowed_tools_for_text",
    "add_runtime_policy_hints",
    "read_only_enabled",
]
