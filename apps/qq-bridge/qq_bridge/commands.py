# SPDX-License-Identifier: AGPL-3.0-only
# Copyright (C) 2026 rtime-assistant contributors (see NOTICE)
"""Declarative slash-command registry (模块化命令表 — 便于以后改/加)。

The bridge used to inline every slash command in one big if-chain inside the
message handler, with the tier decision (who may run what, where) baked into the
control flow. This module makes the command surface DATA:

    COMMANDS: name -> Command(tier, handler, help)

Adding a command = one entry. Adding a tier = extend :class:`Tier` + the gate.
The message handler stays a thin dispatcher: parse the leading ``/word``, look it
up, tier-gate it, and call the handler. Unknown ``/word`` => the handler returns
``None`` (see :func:`dispatch`) and the caller falls through to ordinary Q&A.

Tiers (owner: "切换模型这种指令只给管理员;只给用户开最基础的指令"):

  * ``basic`` — works for EVERY servable user, in private AND groups. These are
    per-user / per-session and harmless: ``/new`` ``/reset`` ``/stream``
    ``/help``. No cost, no abuse surface.
  * ``admin`` — admin tier ONLY, anywhere (private or group). ``/model`` (switch
    model = cost/abuse). A non-admin who sends an admin command gets a friendly
    refusal, no model run. An admin's ``/model`` only mutates the admin's OWN
    session, so it is safe to allow it in a group too.

Access-control note: this registry decides ONLY what a *servable* caller may do.
Whether the caller is servable at all (blocked / private-gate / group准入) is the
``_actor_tier`` gate in ``app.py`` and runs BEFORE dispatch — a blocked user never
reaches here. read_only / library scope are independent hard doors applied at run
time regardless of command.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, replace
from typing import Literal, Protocol

from rtime_chat_runtime.model_routing import (
    ModelChoice,
    model_choice_by_name,
    model_choices,
    numbered_model_choice,
    resolve_alias,
)

# The actor tiers ``_actor_tier`` resolves. Command tiers reuse the SAME vocabulary
# so a command's required tier is comparable to the caller's tier with no mapping.
Tier = Literal["user", "admin"]

# 普通用户发【管理员命令】时的友好拒绝(不落模型)。命令面=运维面,普通用户只有"提问"。
NON_ADMIN_COMMAND_REPLY = "这个命令仅管理员可用;有问题直接发文字问我就可以了。"


class _Store(Protocol):
    """The subset of ``SessionStore`` the handlers touch (typing seam, no import)."""

    def reset(self, user_id: str, chat_id: str) -> object: ...
    def set_model(self, user_id: str, chat_id: str, model: str) -> object: ...
    def set_stream(self, user_id: str, chat_id: str, on: bool) -> object: ...
    def get(self, user_id: str, chat_id: str) -> object: ...


@dataclass(frozen=True)
class CommandContext:
    """Everything a handler needs, bundled so handler signatures stay uniform.

    ``arg`` is the text AFTER the command word (already stripped). ``reply`` sends a
    text reply. ``store`` is the session store. ``default_stream`` is the effective
    instance stream default (used by ``/stream`` with no arg to show current state).
    ``caller_tier`` is the tier the actor-gate resolved; ``/help`` uses it to scope
    its listing to what THIS caller may run. The bridge omits it (defaults to
    ``user``) and :func:`dispatch` fills it in from its ``caller_tier`` argument.
    """

    user_id: str
    chat_id: str
    arg: str
    reply: Callable[[str], Awaitable[None]]
    store: _Store
    default_stream: bool
    default_model: str = ""
    caller_tier: Tier = "user"


Handler = Callable[[CommandContext], Awaitable[None]]


@dataclass(frozen=True)
class Command:
    """One slash command: its required tier, its handler, and a one-line help."""

    tier: Tier
    handler: Handler
    help: str


# --- handlers -------------------------------------------------------------------
# Each keeps the EXACT reply text / behaviour of the old inline if-chain so the
# refactor is behaviour-preserving for the commands that already existed.


async def _cmd_new(ctx: CommandContext) -> None:
    ctx.store.reset(ctx.user_id, ctx.chat_id)
    await ctx.reply("🆕 已开始新对话")


async def _cmd_stream(ctx: CommandContext) -> None:
    arg = ctx.arg.lower()
    if arg in ("on", "开", "1", "true"):
        ctx.store.set_stream(ctx.user_id, ctx.chat_id, True)
        await ctx.reply("✅ 已开启流式输出（中间过程先发出来）")
    elif arg in ("off", "关", "0", "false"):
        ctx.store.set_stream(ctx.user_id, ctx.chat_id, False)
        await ctx.reply("✅ 已关闭流式输出（只发最终回答）")
    else:
        pref = ctx.store.get(ctx.user_id, ctx.chat_id).stream  # type: ignore[attr-defined]
        eff = ctx.default_stream if pref is None else pref
        await ctx.reply(
            f"当前流式输出：{'开' if eff else '关'}（用 /stream on|off 切换）"
        )


async def _cmd_model(ctx: CommandContext) -> None:
    arg = ctx.arg.strip()
    low = arg.lower()
    if not arg or low in ("list", "ls", "列表"):
        await _reply_model_list(ctx)
        return
    if low in ("reset", "default", "默认"):
        ctx.store.set_model(ctx.user_id, ctx.chat_id, ctx.default_model)
        await ctx.reply(f"模型已恢复实例默认：{_display_model(ctx.default_model)}")
        return

    choice: ModelChoice | None = None
    if arg.isdigit():
        choice = numbered_model_choice(arg)
        if choice is None:
            await ctx.reply(
                f"没有编号 {arg}。发送 /models 查看可选模型，再用 /model <编号> 切换。"
            )
            return
    else:
        choice = model_choice_by_name(arg)

    if choice is not None:
        ctx.store.set_model(ctx.user_id, ctx.chat_id, choice.model)
        await ctx.reply(f"模型已设为：{_choice_title(choice)}")
        return

    # Backward-compatible escape hatch: allow raw model ids that are not yet in the
    # registry. The numbered/list path is preferred for ordinary operation.
    resolved = resolve_alias(arg)
    ctx.store.set_model(ctx.user_id, ctx.chat_id, resolved)
    shown = f"{arg} → {resolved}" if resolved != arg else arg
    await ctx.reply(f"模型已设为：{shown}")


async def _cmd_models(ctx: CommandContext) -> None:
    await _reply_model_list(ctx)


def _display_model(model: str) -> str:
    return model or "默认(kimi)"


def _choice_title(choice: ModelChoice) -> str:
    if choice.model and choice.model != choice.key:
        return f"{choice.key} → {choice.model}"
    return _display_model(choice.model or choice.key)


def _cap_text(choice: ModelChoice) -> str:
    caps = choice.capabilities
    parts = []
    parts.append("工具" if caps.get("agent_tools") else "纯聊天")
    parts.append("可看图" if caps.get("vision") else "文本")
    if caps.get("code"):
        parts.append("代码")
    if caps.get("file_extract"):
        parts.append("文件")
    return " / ".join(parts)


async def _reply_model_list(ctx: CommandContext) -> None:
    cur = ctx.store.get(ctx.user_id, ctx.chat_id).model  # type: ignore[attr-defined]
    effective = cur or ctx.default_model
    lines = [
        f"当前模型：{_display_model(effective)}",
        "可选模型：",
    ]
    for i, choice in enumerate(model_choices(), start=1):
        alias_text = "/".join(choice.aliases) if choice.aliases else choice.key
        target = _display_model(choice.model)
        target_text = f" → {target}" if target != alias_text else ""
        lines.append(
            f"{i}. {alias_text} — {choice.label}{target_text} [{_cap_text(choice)}]"
        )
    lines.append("用法：/model 1 按编号切换；/model reset 回到实例默认。")
    await ctx.reply("\n".join(lines))


async def _cmd_help(ctx: CommandContext) -> None:
    # /help lists exactly the commands the CALLER's tier can run (so a normal user
    # never sees admin-only commands). dispatch put the caller's tier on the context.
    lines = ["可用命令："]
    for name, cmd in COMMANDS.items():
        if _tier_ok(ctx.caller_tier, cmd.tier):
            lines.append(f"{name} — {cmd.help}")
    await ctx.reply("\n".join(lines))


# --- the registry ---------------------------------------------------------------
# Adding a command = ONE entry here. Adding a tier = extend Tier + _tier_ok.
COMMANDS: dict[str, Command] = {
    "/new": Command("user", _cmd_new, "开始新对话（清空当前会话上下文）"),
    "/reset": Command("user", _cmd_new, "同 /new，开始新对话"),
    "/stream": Command("user", _cmd_stream, "开关流式输出：/stream on|off"),
    "/help": Command("user", _cmd_help, "列出你可用的命令"),
    "/model": Command(
        "admin", _cmd_model, "切换模型（仅管理员）：/model <编号|别名>"
    ),
    "/models": Command("admin", _cmd_models, "列出可切换模型编号（仅管理员）"),
}


def _tier_ok(caller: Tier, required: Tier) -> bool:
    """Is ``caller`` allowed to run a command requiring ``required``?

    Monotone ladder: ``admin`` may run everything; ``user`` may run only ``user``
    (basic) commands. Extending the ladder = add the new tier to this order.
    """
    order = {"user": 0, "admin": 1}
    return order[caller] >= order[required]


def parse_command(text: str) -> tuple[str, str] | None:
    """Split ``text`` into ``(command, arg)`` iff it is a known slash command.

    Returns the canonical command name (lowercased ``/word``) and the trailing arg
    (original case, stripped), or ``None`` if ``text`` does not START with a known
    command. So ``/model opus`` -> ``("/model", "opus")``; ``/MODEL`` -> match;
    ``/unknown x`` -> None (caller treats it as ordinary text); ``/models`` (no such
    command) -> None. Matching is on the FIRST whitespace-delimited token so a
    command with args is recognized, but a longer word that merely starts with a
    command name is not (``/models`` != ``/model``).
    """
    if not text.startswith("/"):
        return None
    head, _, rest = text.partition(" ")
    name = head.lower()
    if name not in COMMANDS:
        return None
    return name, rest.strip()


async def dispatch(
    text: str,
    *,
    caller_tier: Tier,
    ctx_factory: Callable[[str], CommandContext],
    reply: Callable[[str], Awaitable[None]],
) -> bool:
    """Dispatch a slash command. Returns True iff it was HANDLED (a run must NOT run).

    Contract:
      * unknown ``/word`` (or not a slash command) => return False; the caller falls
        through and treats ``text`` as ordinary Q&A (current behaviour);
      * a KNOWN command the caller's tier may NOT run (admin command, non-admin
        caller) => send the friendly refusal and return True (handled: no model run);
      * a known command the caller may run => run its handler and return True.

    ``ctx_factory(arg)`` builds the :class:`CommandContext` (the caller owns the
    store / ids / default_stream wiring). ``caller_tier`` is the tier ``_actor_tier``
    resolved for this message — it gates admin commands AND scopes ``/help``.
    """
    parsed = parse_command(text)
    if parsed is None:
        return False  # not a known command -> ordinary text
    name, arg = parsed
    cmd = COMMANDS[name]
    if not _tier_ok(caller_tier, cmd.tier):
        # Known admin command, non-admin caller: friendly refusal, no run.
        await reply(NON_ADMIN_COMMAND_REPLY)
        return True
    # Stamp the caller's tier onto the context so /help can scope its listing.
    ctx = replace(ctx_factory(arg), caller_tier=caller_tier)
    await cmd.handler(ctx)
    return True


__all__ = [
    "COMMANDS",
    "NON_ADMIN_COMMAND_REPLY",
    "Command",
    "CommandContext",
    "Tier",
    "dispatch",
    "parse_command",
]
